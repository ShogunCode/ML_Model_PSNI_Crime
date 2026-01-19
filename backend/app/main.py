import csv
import json
import os
import re
import smtplib
from datetime import date, datetime, timezone, timedelta
from email.message import EmailMessage
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg import sql

from .domain.ratings import (
    RATING_RATE_WEIGHT,
    RATING_TREND_WEIGHT,
    percentile,
    rating_band,
    rating_score,
    trend_direction,
)

def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


OUTPUT_DIR = os.getenv("CRIMEMAP_OUTPUT_DIR") or os.path.join(_repo_root(), "outputs")
WARD_ANALYSIS_PATH = os.path.join(OUTPUT_DIR, "ward_crime_analysis.csv")
WARD_OFFICIALS_PATH = os.path.join(OUTPUT_DIR, "ward_officials.csv")
GAP_REPORT_PATH = os.path.join(OUTPUT_DIR, "police_api_gap_report.csv")
WARDS_MAP_PATH = os.path.join(OUTPUT_DIR, "wards_interactive_map.html")
CRIME_HISTORY_PATH = os.getenv("CRIMEMAP_HISTORY_PATH") or os.path.join(
    _repo_root(), "data", "processed", "crime_history.csv"
)
CLEANED_CRIME_PATH = os.getenv("CRIMEMAP_CLEANED_CRIME_PATH") or os.path.join(
    _repo_root(), "data", "processed", "cleaned_crime_data.csv"
)
RAW_CRIME_PATH = os.getenv("CRIMEMAP_CRIME_PATH") or os.path.join(
    _repo_root(), "data", "raw", "crime_data.csv"
)

HARM_KEYWORDS = (
    ("violence", 10),
    ("violent", 10),
    ("assault", 10),
    ("murder", 10),
    ("homicide", 10),
    ("robbery", 10),
    ("burglary", 5),
    ("theft", 1),
    ("shoplifting", 1),
)

COVERAGE_CONFIDENCE_SQL = (
    "CASE "
    "WHEN population IS NULL OR population = 0 OR months IS NULL OR months < 6 THEN 'low' "
    "WHEN months < 12 THEN 'medium' "
    "ELSE 'high' "
    "END"
)


class WardRow(BaseModel):
    ward_code: str
    ward_name: str
    population: int | None = None
    number_of_crimes: int | None = None
    crime_rate_per_100k: float | None = None
    rate_percentile: float | None = None
    rate_rank: int | None = None
    high_crime_rate: bool | None = None
    total_crimes: int | None = None
    avg_monthly: float | None = None
    trend_change: float | None = None
    trend_pct: float | None = None
    trend_slope: float | None = None
    yoy_current: int | None = None
    yoy_prior: int | None = None
    yoy_change: float | None = None
    total_harm: float | None = None
    harm_score_per_100k: float | None = None
    months: int | None = None
    first_month: str | None = None
    last_month: str | None = None
    rating_score: float | None = None
    rating_band: str | None = None
    trend_percentile: float | None = None
    trend_direction: str | None = None
    coverage_confidence: Literal["high", "medium", "low"] | None = None
    coverage_flags: list[str] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "ward_code": "95A",
                "ward_name": "Central",
                "population": 2400,
                "crime_rate_per_100k": 5400.2,
                "rate_percentile": 82.1,
                "trend_slope": 0.42,
                "yoy_change": 12.4,
                "rating_score": 78.4,
                "rating_band": "Elevated",
                "coverage_confidence": "high",
                "coverage_flags": [],
            }
        }
    }


class WardListResponse(BaseModel):
    items: list[WardRow]
    total: int
    limit: int
    offset: int
    sort: str
    order: str
    source: str
    filters: dict
    coverage_start: str | None = None
    coverage_end: str | None = None


class WardTypeTrend(BaseModel):
    crime_type: str
    crime_type_label: str | None = None
    total_crimes: int | None = None
    avg_monthly: float | None = None
    trend_change: float | None = None
    trend_pct: float | None = None
    trend_slope: float | None = None
    months: int | None = None
    first_month: str | None = None
    last_month: str | None = None
    trend_direction: str | None = None


class WardOfficial(BaseModel):
    name: str
    role: str | None = None
    party: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None


class WardDetailResponse(BaseModel):
    ward: WardRow
    rating_explain: dict
    crime_types: list[WardTypeTrend]
    officials: list[WardOfficial] = []
    source: str
    coverage_start: str | None = None
    coverage_end: str | None = None


class TimeSeriesPoint(BaseModel):
    month: str
    value: float | None = None


class TimeSeriesSummary(BaseModel):
    window: int
    latest_avg: float | None = None
    prior_avg: float | None = None
    change: float | None = None
    pct_change: float | None = None
    direction: str | None = None


class TimeSeriesResponse(BaseModel):
    ward_code: str
    ward_name: str
    metric: str
    crime_type: str | None = None
    points: list[TimeSeriesPoint]
    summary: TimeSeriesSummary
    source: str
    coverage_start: str | None = None
    coverage_end: str | None = None


class OpsJobsResponse(BaseModel):
    source: str
    jobs: list[dict]


class OpsQualityResponse(BaseModel):
    source: str
    dataset_version: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    population_missing: int | None = None
    population_missing_pct: float | None = None
    short_history: int | None = None
    short_history_pct: float | None = None
    invalid_coords: int | None = None
    invalid_coords_pct: float | None = None
    crime_rows: int | None = None
    ward_rows: int | None = None
    confidence_counts: dict
    gap_report: dict | None = None


class AlertRuleBase(BaseModel):
    name: str
    description: str | None = None
    rule_type: Literal["ward", "filter"]
    ward_code: str | None = None
    metric: str | None = None
    operator: str | None = None
    threshold_value: str | None = None
    threshold_number: float | None = None
    filter_json: dict | None = None
    trigger_on: Literal["enter", "always"] = "enter"
    window_months: int | None = None
    is_active: bool = True
    muted_until: str | None = None
    notify_emails: list[str] = Field(default_factory=list)


class AlertRuleCreate(AlertRuleBase):
    pass


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rule_type: Literal["ward", "filter"] | None = None
    ward_code: str | None = None
    metric: str | None = None
    operator: str | None = None
    threshold_value: str | None = None
    threshold_number: float | None = None
    filter_json: dict | None = None
    trigger_on: Literal["enter", "always"] | None = None
    window_months: int | None = None
    is_active: bool | None = None
    muted_until: str | None = None
    notify_emails: list[str] | None = None


class AlertRuleResponse(AlertRuleBase):
    id: int
    created_at: str | None = None
    updated_at: str | None = None


class AlertEventResponse(BaseModel):
    id: int
    alert_rule_id: int
    ward_code: str | None = None
    ward_name: str | None = None
    dataset_version: str | None = None
    coverage_end: str | None = None
    status: str
    message: str | None = None
    metric: str | None = None
    operator: str | None = None
    threshold_value: str | None = None
    threshold_number: float | None = None
    observed_value: float | None = None
    observed_text: str | None = None
    value_json: dict | None = None
    triggered_at: str | None = None
    acknowledged_at: str | None = None
    rule_name: str | None = None
    rule_type: str | None = None


class AlertEventListResponse(BaseModel):
    items: list[AlertEventResponse]
    total: int
    limit: int
    offset: int
    source: str


class AlertMuteRequest(BaseModel):
    hours: int | None = None
    until: str | None = None


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_bool(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def _normalize_month(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%Y-%m")
    except ValueError:
        return None


def _as_percent(value):
    if value in (None, ""):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 1:
        return round(num * 100, 1)
    return round(num, 1)


def _to_fraction(value):
    if value in (None, ""):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num > 1:
        return num / 100.0
    return num


def _coverage_confidence(population, months):
    if population in (None, 0) or months in (None, 0):
        return "low"
    if months >= 12:
        return "high"
    if months >= 6:
        return "medium"
    return "low"


def _coverage_flags(population, months):
    flags = []
    if population in (None, 0):
        flags.append("population_missing")
    if months is None:
        flags.append("months_missing")
    elif months < 6:
        flags.append("short_history")
    elif months < 12:
        flags.append("partial_history")
    return flags


def _parse_iso_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _serialize_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_filter_json(value):
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _normalize_emails(value):
    if not value:
        return []
    if isinstance(value, list):
        return [email for email in value if email]
    text = str(value)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _normalize_db_url(url):
    if not url:
        return url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_conninfo():
    url = (
        os.getenv("DATABASE_URL")
        or os.getenv("CRIMEMAP_DATABASE_URL")
        or os.getenv("POSTGRES_URL")
    )
    if url:
        return _normalize_db_url(url)
    host = os.getenv("POSTGRES_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "crimemap"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }


def _db_connect():
    conninfo = _database_conninfo()
    if not conninfo:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        return None
    try:
        if isinstance(conninfo, str):
            return psycopg.connect(conninfo, row_factory=dict_row)  # type: ignore
        return psycopg.connect(row_factory=dict_row, **conninfo)  # type: ignore
    except Exception:
        return None


def _format_month(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return _normalize_month(value)


def _load_history_sparkline(path, window=6):
    if not os.path.exists(path):
        return []

    counts = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Month" not in reader.fieldnames:
            return []
        for row in reader:
            month = _normalize_month(row.get("Month"))
            if not month:
                continue
            counts[month] = counts.get(month, 0) + 1

    if not counts:
        return []
    months = sorted(counts.keys())
    tail = months[-window:]
    return [counts[month] for month in tail]


def _enrich_ward_rows(wards):
    if not wards:
        return wards

    rate_values = sorted(
        [w["crime_rate_per_100k"] for w in wards if w["crime_rate_per_100k"] is not None]
    )
    slope_values = sorted([w["trend_slope"] for w in wards if w["trend_slope"] is not None])

    for ward in wards:
        rate_percentile = ward.get("rate_percentile")
        if rate_percentile is None:
            rate_percentile = percentile(rate_values, ward.get("crime_rate_per_100k"))
        trend_percentile = percentile(slope_values, ward.get("trend_slope"))
        score = rating_score(rate_percentile, trend_percentile)

        ward["rate_percentile"] = rate_percentile
        ward["trend_percentile"] = (
            round(trend_percentile * 100, 1) if trend_percentile is not None else None
        )
        ward["rating_score"] = score
        ward["rating_band"] = rating_band(score)
        ward["trend_direction"] = trend_direction(ward.get("trend_slope"))

    return wards


def _load_ward_analysis_from_csv(path):
    if not os.path.exists(path):
        return []

    wards = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            annualized = _parse_float(row.get("AnnualizedCrimeRatePer100k"))
            crime_rate = annualized
            if crime_rate is None:
                crime_rate = _parse_float(row.get("CrimeRatePer100kPeople"))
            wards.append(
                {
                    "ward_code": row.get("WardCode") or "",
                    "ward_name": row.get("WARDNAME") or "",
                    "population": _parse_int(row.get("Population")),
                    "number_of_crimes": _parse_int(row.get("NumberOfCrimes")),
                    "crime_rate_per_100k": crime_rate,
                    "rate_percentile": _parse_float(row.get("RatePercentile")),
                    "rate_rank": _parse_int(row.get("RateRank")),
                    "high_crime_rate": _parse_bool(row.get("HighCrimeRate")),
                    "total_crimes": _parse_int(row.get("TotalCrimes")),
                    "avg_monthly": _parse_float(row.get("AvgMonthly")),
                    "trend_change": _parse_float(row.get("TrendChange")),
                    "trend_pct": _parse_float(row.get("TrendPct")),
                    "trend_slope": _parse_float(row.get("TrendSlope")),
                    "yoy_current": _parse_int(row.get("YoYCurrent")),
                    "yoy_prior": _parse_int(row.get("YoYPrior")),
                    "yoy_change": _parse_float(row.get("YoYChange")),
                    "total_harm": _parse_float(row.get("TotalHarm")),
                    "harm_score_per_100k": _parse_float(row.get("HarmScorePer100k")),
                    "months": _parse_int(row.get("Months")),
                    "first_month": row.get("FirstMonth") or "",
                    "last_month": row.get("LastMonth") or "",
                }
            )

    return _enrich_ward_rows(wards)


def _load_gap_report_from_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return None
    return rows[-1]


def _latest_dataset(cursor):
    cursor.execute(
        """
        SELECT dataset_version, coverage_start, coverage_end, started_at, finished_at,
               status, rows_loaded
        FROM job_runs
        WHERE status = 'completed'
        ORDER BY finished_at DESC NULLS LAST, started_at DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row:
        return dict(row) if not isinstance(row, dict) else row

    return None


def _latest_ward_dataset(cursor):
    cursor.execute(
        """
        SELECT dataset_version, coverage_start, coverage_end
        FROM ward_metrics
        ORDER BY coverage_end DESC NULLS LAST
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row:
        row_dict = dict(row) if not isinstance(row, dict) else row
        row_dict["status"] = "completed"
        return row_dict
    return row


def _latest_crime_dataset(cursor):
    cursor.execute(
        """
        SELECT dataset_version, MAX(month) AS coverage_end
        FROM crimes
        GROUP BY dataset_version
        ORDER BY coverage_end DESC NULLS LAST
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row:
        row_dict = dict(row) if not isinstance(row, dict) else row
        row_dict["status"] = "completed"
        return row_dict
    return row


def _load_ward_analysis_from_db():
    conn = _db_connect()
    if not conn:
        return None, None
    try:
        with conn.cursor() as cursor:
            dataset = _latest_dataset(cursor)
            if not dataset:
                return [], None
            dataset_version = dataset.get("dataset_version")
            coverage_end = dataset.get("coverage_end")
            if not dataset_version or not coverage_end:
                return [], dataset
            cursor.execute(
                """
                SELECT ward_code, ward_name, population, number_of_crimes, crime_rate_per_100k,
                       rate_percentile, rate_rank, high_crime_rate, total_crimes, avg_monthly,
                       trend_change, trend_pct, trend_slope, yoy_current, yoy_prior, yoy_change,
                       total_harm, harm_score_per_100k, months, first_month, last_month,
                       rating_score, rating_band, trend_percentile, annualized_crime_rate_per_100k
                FROM ward_metrics
                WHERE dataset_version = %s AND coverage_end = %s
                """,
                (dataset_version, coverage_end),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    wards = []
    for row in rows:
        row_dict = dict(row) if not isinstance(row, dict) else row
        crime_rate = row_dict.get("crime_rate_per_100k")
        if crime_rate is None:
            crime_rate = row_dict.get("annualized_crime_rate_per_100k")
        wards.append(
            {
                "ward_code": row_dict.get("ward_code") or "",
                "ward_name": row_dict.get("ward_name") or "",
                "population": row_dict.get("population"),
                "number_of_crimes": row_dict.get("number_of_crimes"),
                "crime_rate_per_100k": crime_rate,
                "rate_percentile": row_dict.get("rate_percentile"),
                "rate_rank": row_dict.get("rate_rank"),
                "high_crime_rate": row_dict.get("high_crime_rate"),
                "total_crimes": row_dict.get("total_crimes"),
                "avg_monthly": row_dict.get("avg_monthly"),
                "trend_change": row_dict.get("trend_change"),
                "trend_pct": row_dict.get("trend_pct"),
                "trend_slope": row_dict.get("trend_slope"),
                "yoy_current": row_dict.get("yoy_current"),
                "yoy_prior": row_dict.get("yoy_prior"),
                "yoy_change": row_dict.get("yoy_change"),
                "total_harm": row_dict.get("total_harm"),
                "harm_score_per_100k": row_dict.get("harm_score_per_100k"),
                "months": row_dict.get("months"),
                "first_month": _format_month(row_dict.get("first_month")) or "",
                "last_month": _format_month(row_dict.get("last_month")) or "",
                "rating_score": row_dict.get("rating_score"),
                "rating_band": row_dict.get("rating_band"),
                "trend_percentile": row_dict.get("trend_percentile"),
            }
        )

    return _enrich_ward_rows(wards), dataset


def _coalesce(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _ward_payload(row):
    crime_rate = _coalesce(
        row,
        "crime_rate_per_100k",
        "annualized_crime_rate_per_100k",
        "CrimeRatePer100kPeople",
        "AnnualizedCrimeRatePer100k",
    )
    population = _parse_int(_coalesce(row, "population", "Population"))
    months = _parse_int(_coalesce(row, "months", "Months"))
    rate_percentile = _as_percent(_coalesce(row, "rate_percentile", "RatePercentile"))
    trend_percentile = _as_percent(
        _coalesce(row, "trend_percentile", "TrendPercentile")
    )

    payload = {
        "ward_code": _coalesce(row, "ward_code", "WardCode") or "",
        "ward_name": _coalesce(row, "ward_name", "WARDNAME") or "",
        "population": population,
        "number_of_crimes": _parse_int(_coalesce(row, "number_of_crimes", "NumberOfCrimes")),
        "crime_rate_per_100k": _parse_float(crime_rate),
        "rate_percentile": rate_percentile,
        "rate_rank": _parse_int(_coalesce(row, "rate_rank", "RateRank")),
        "high_crime_rate": (
            _parse_bool(_coalesce(row, "high_crime_rate", "HighCrimeRate"))
            if _coalesce(row, "high_crime_rate", "HighCrimeRate") is not None
            else None
        ),
        "total_crimes": _parse_int(_coalesce(row, "total_crimes", "TotalCrimes")),
        "avg_monthly": _parse_float(_coalesce(row, "avg_monthly", "AvgMonthly")),
        "trend_change": _parse_float(_coalesce(row, "trend_change", "TrendChange")),
        "trend_pct": _parse_float(_coalesce(row, "trend_pct", "TrendPct")),
        "trend_slope": _parse_float(_coalesce(row, "trend_slope", "TrendSlope")),
        "yoy_current": _parse_int(_coalesce(row, "yoy_current", "YoYCurrent")),
        "yoy_prior": _parse_int(_coalesce(row, "yoy_prior", "YoYPrior")),
        "yoy_change": _parse_float(_coalesce(row, "yoy_change", "YoYChange")),
        "total_harm": _parse_float(_coalesce(row, "total_harm", "TotalHarm")),
        "harm_score_per_100k": _parse_float(
            _coalesce(row, "harm_score_per_100k", "HarmScorePer100k")
        ),
        "months": months,
        "first_month": _format_month(_coalesce(row, "first_month", "FirstMonth")) or "",
        "last_month": _format_month(_coalesce(row, "last_month", "LastMonth")) or "",
        "rating_score": _parse_float(_coalesce(row, "rating_score", "RatingScore")),
        "rating_band": _coalesce(row, "rating_band", "RatingBand"),
        "trend_percentile": trend_percentile,
        "trend_direction": _coalesce(row, "trend_direction", "TrendDirection"),
    }
    payload["coverage_confidence"] = _coverage_confidence(population, months)
    payload["coverage_flags"] = _coverage_flags(population, months)
    return payload


def _rating_explain(ward):
    rate_percentile = ward.get("rate_percentile")
    trend_percentile = ward.get("trend_percentile")
    rating_score_value = ward.get("rating_score")
    if rating_score_value is None:
        rating_score_value = rating_score(rate_percentile, trend_percentile)
    return {
        "rate_percentile": rate_percentile,
        "trend_percentile": trend_percentile,
        "rate_weight": RATING_RATE_WEIGHT,
        "trend_weight": RATING_TREND_WEIGHT,
        "rating_score": rating_score_value,
        "rating_band": ward.get("rating_band"),
    }


def _load_gap_report_from_db(dataset_version=None, coverage_end=None):
    conn = _db_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            if dataset_version and coverage_end:
                cursor.execute(
                    """
                    SELECT checked_at, history_latest, latest_available, gap_months,
                           default_start_month
                    FROM gap_report
                    WHERE dataset_version = %s AND coverage_end = %s
                    ORDER BY checked_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (dataset_version, coverage_end),
                )
            else:
                cursor.execute(
                    """
                    SELECT checked_at, history_latest, latest_available, gap_months,
                           default_start_month
                    FROM gap_report
                    ORDER BY checked_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
            row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return None
    row_dict = dict(row) if not isinstance(row, dict) else row
    checked_at = row_dict.get("checked_at")
    return {
        "checked_at": checked_at.isoformat() if checked_at else "",
        "history_latest": _format_month(row_dict.get("history_latest")) or "",
        "latest_available": _format_month(row_dict.get("latest_available")) or "",
        "gap_months": row_dict.get("gap_months"),
        "default_start_month": _format_month(row_dict.get("default_start_month")) or "",
    }


def _coverage_from_wards(wards):
    if not wards:
        return None, None
    first = None
    last = None
    for ward in wards:
        first_month = _normalize_month(ward.get("first_month"))
        last_month = _normalize_month(ward.get("last_month"))
        if first_month and (first is None or first_month < first):
            first = first_month
        if last_month and (last is None or last_month > last):
            last = last_month
    return first, last


def _ward_type_trends_from_csv(path, ward_code, limit=12):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("WardCode") or "").strip() != str(ward_code):
                continue
            rows.append(
                {
                    "crime_type": row.get("Crime type") or "",
                    "crime_type_label": _crime_type_label(row.get("Crime type")),
                    "total_crimes": _parse_int(row.get("TotalCrimes")),
                    "avg_monthly": _parse_float(row.get("AvgMonthly")),
                    "trend_change": _parse_float(row.get("TrendChange")),
                    "trend_pct": _parse_float(row.get("TrendPct")),
                    "trend_slope": _parse_float(row.get("TrendSlope")),
                    "months": _parse_int(row.get("Months")),
                    "first_month": _format_month(row.get("FirstMonth")) or "",
                    "last_month": _format_month(row.get("LastMonth")) or "",
                    "trend_direction": row.get("TrendDirection") or "",
                }
            )
    rows.sort(
        key=lambda item: item.get("total_crimes") or 0,
        reverse=True,
    )
    return rows[:limit] if limit else rows


def _ward_type_trends_from_db(cursor, dataset_version, coverage_end, ward_code, limit=12):
    cursor.execute(
        """
        SELECT crime_type, total_crimes, avg_monthly, trend_change, trend_pct, trend_slope,
               months, first_month, last_month, trend_direction
        FROM ward_type_metrics
        WHERE dataset_version = %s AND coverage_end = %s AND ward_code = %s
        ORDER BY total_crimes DESC NULLS LAST
        LIMIT %s
        """,
        (dataset_version, coverage_end, ward_code, limit),
    )
    rows = []
    for row in cursor.fetchall():
        rows.append(
            {
                "crime_type": row.get("crime_type") or "",
                "crime_type_label": _crime_type_label(row.get("crime_type")),
                "total_crimes": row.get("total_crimes"),
                "avg_monthly": row.get("avg_monthly"),
                "trend_change": row.get("trend_change"),
                "trend_pct": row.get("trend_pct"),
                "trend_slope": row.get("trend_slope"),
                "months": row.get("months"),
                "first_month": _format_month(row.get("first_month")) or "",
                "last_month": _format_month(row.get("last_month")) or "",
                "trend_direction": row.get("trend_direction") or "",
            }
        )
    return rows


def _ward_officials_from_csv(path, ward_code):
    if not os.path.exists(path):
        return []
    officials = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if str(row.get("ward_code") or "").strip() != str(ward_code):
                continue
            officials.append(
                {
                    "name": row.get("official_name") or row.get("name") or "",
                    "role": row.get("role") or "",
                    "party": row.get("party") or "",
                    "email": row.get("email") or "",
                    "phone": row.get("phone") or "",
                    "source": row.get("source") or "",
                }
            )
    return officials


def _ward_officials_from_db(cursor, ward_code):
    cursor.execute(
        """
        SELECT official_name, role, party, email, phone, source
        FROM ward_officials
        WHERE ward_code = %s
        ORDER BY official_name
        """,
        (ward_code,),
    )
    officials = []
    for row in cursor.fetchall():
        officials.append(
            {
                "name": row.get("official_name") or "",
                "role": row.get("role") or "",
                "party": row.get("party") or "",
                "email": row.get("email") or "",
                "phone": row.get("phone") or "",
                "source": row.get("source") or "",
            }
        )
    return officials


def _filter_ward_rows(
    rows,
    query=None,
    band=None,
    min_rate_percentile=None,
    max_rate_percentile=None,
    min_trend_slope=None,
    max_trend_slope=None,
    min_yoy_change=None,
    max_yoy_change=None,
    coverage_confidence=None,
):
    filtered = []
    query_text = (query or "").strip().lower()
    band_text = (band or "").strip().lower()
    confidence_text = (coverage_confidence or "").strip().lower()
    for row in rows:
        payload = _ward_payload(row)
        if query_text:
            name = (payload.get("ward_name") or "").lower()
            code = (payload.get("ward_code") or "").lower()
            if query_text not in name and query_text not in code:
                continue
        if band_text and band_text != "all":
            if (payload.get("rating_band") or "").lower() != band_text:
                continue
        if min_rate_percentile is not None:
            if payload.get("rate_percentile") is None:
                continue
            if payload["rate_percentile"] < min_rate_percentile:
                continue
        if max_rate_percentile is not None:
            if payload.get("rate_percentile") is None:
                continue
            if payload["rate_percentile"] > max_rate_percentile:
                continue
        if min_trend_slope is not None:
            if payload.get("trend_slope") is None:
                continue
            if payload["trend_slope"] < min_trend_slope:
                continue
        if max_trend_slope is not None:
            if payload.get("trend_slope") is None:
                continue
            if payload["trend_slope"] > max_trend_slope:
                continue
        if min_yoy_change is not None:
            if payload.get("yoy_change") is None:
                continue
            if payload["yoy_change"] < min_yoy_change:
                continue
        if max_yoy_change is not None:
            if payload.get("yoy_change") is None:
                continue
            if payload["yoy_change"] > max_yoy_change:
                continue
        if confidence_text and confidence_text != "all":
            if payload.get("coverage_confidence") != confidence_text:
                continue
        filtered.append(payload)
    return filtered


def _harm_weight(crime_type):
    text = str(crime_type or "").lower()
    for keyword, score in HARM_KEYWORDS:
        if keyword in text:
            return score
    return 1


def _crime_type_label(value):
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"[_-]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title()


def _iter_months(start_date, end_date):
    current = start_date.replace(day=1)
    end_date = end_date.replace(day=1)
    while current <= end_date:
        yield current
        year = current.year + (current.month // 12)
        month = current.month % 12 + 1
        current = current.replace(year=year, month=month)


def _build_timeseries_points(counts, harms, metric, population, start, end):
    if not counts:
        return [], []
    start_date = _month_to_date(start) if start else None
    end_date = _month_to_date(end) if end else None
    if not start_date:
        start_date = _month_to_date(min(counts.keys()))
    if not end_date:
        end_date = _month_to_date(max(counts.keys()))
    if not start_date or not end_date:
        return [], []

    points = []
    values = []
    for month_date in _iter_months(start_date, end_date):
        month = month_date.strftime("%Y-%m")
        count = counts.get(month, 0)
        harm_total = harms.get(month, 0)
        if metric == "count":
            value = float(count)
        elif population:
            if metric == "harm":
                value = (harm_total / population) * 100000
            else:
                value = (count / population) * 100000
        else:
            value = None
        points.append(TimeSeriesPoint(month=month, value=value))
        values.append(value)
    return points, values


def _series_summary(values, window=3):
    if not values:
        return {
            "window": window,
            "latest_avg": None,
            "prior_avg": None,
            "change": None,
            "pct_change": None,
            "direction": None,
        }
    numeric = [value for value in values if value is not None]
    if len(numeric) < window:
        return {
            "window": window,
            "latest_avg": None,
            "prior_avg": None,
            "change": None,
            "pct_change": None,
            "direction": None,
        }
    latest = numeric[-window:]
    prior = numeric[-window * 2 : -window]
    if not prior:
        return {
            "window": window,
            "latest_avg": round(sum(latest) / len(latest), 2),
            "prior_avg": None,
            "change": None,
            "pct_change": None,
            "direction": None,
        }
    latest_avg = sum(latest) / len(latest)
    prior_avg = sum(prior) / len(prior)
    change = latest_avg - prior_avg
    pct_change = (change / prior_avg * 100) if prior_avg else None
    if change > 0:
        direction = "up"
    elif change < 0:
        direction = "down"
    else:
        direction = "flat"
    return {
        "window": window,
        "latest_avg": round(latest_avg, 2),
        "prior_avg": round(prior_avg, 2),
        "change": round(change, 2),
        "pct_change": round(pct_change, 2) if pct_change is not None else None,
        "direction": direction,
    }


def _email_settings():
    host = os.getenv("ALERT_SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("ALERT_SMTP_PORT", "587")),
        "user": os.getenv("ALERT_SMTP_USER"),
        "password": os.getenv("ALERT_SMTP_PASSWORD"),
        "from_addr": os.getenv("ALERT_EMAIL_FROM") or os.getenv("ALERT_SMTP_USER"),
        "starttls": os.getenv("ALERT_SMTP_STARTTLS", "true").lower() != "false",
    }


def _send_alert_email(recipients, subject, body):
    settings = _email_settings()
    if not settings or not recipients:
        return False, "smtp_not_configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.get("from_addr") or "alerts@crimemap.local"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=10) as server:
            if settings.get("starttls"):
                server.starttls()
            if settings.get("user"):
                server.login(settings["user"], settings.get("password") or "")
            server.send_message(msg)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _apply_filter_json(ward, filters):
    if not filters:
        return True
    band = filters.get("band")
    if band:
        if (ward.get("rating_band") or "").lower() != str(band).lower():
            return False
    coverage = filters.get("coverage_confidence")
    if coverage:
        if (ward.get("coverage_confidence") or "").lower() != str(coverage).lower():
            return False
    min_rate = filters.get("min_rate_percentile")
    if min_rate is not None:
        if ward.get("rate_percentile") is None or ward["rate_percentile"] < float(min_rate):
            return False
    max_rate = filters.get("max_rate_percentile")
    if max_rate is not None:
        if ward.get("rate_percentile") is None or ward["rate_percentile"] > float(max_rate):
            return False
    min_trend = filters.get("min_trend_slope")
    if min_trend is not None:
        if ward.get("trend_slope") is None or ward["trend_slope"] < float(min_trend):
            return False
    min_yoy = filters.get("min_yoy_change")
    if min_yoy is not None:
        if ward.get("yoy_change") is None or ward["yoy_change"] < float(min_yoy):
            return False
    return True


def _metric_value(ward, metric):
    if not metric:
        return None
    return ward.get(metric)


def _compare_values(operator, value, threshold):
    op = (operator or "eq").lower()
    if op == "eq":
        return value == threshold
    if op == "neq":
        return value != threshold
    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "contains":
        return str(threshold).lower() in str(value).lower()
    return False


def _rule_matches(ward, rule):
    filters = _parse_filter_json(rule.get("filter_json"))
    if not _apply_filter_json(ward, filters):
        return False, None, None
    metric = rule.get("metric")
    if not metric:
        return True, None, None
    value = _metric_value(ward, metric)
    if value is None:
        return False, None, None
    operator = rule.get("operator") or "eq"
    if metric in ("rating_band", "coverage_confidence"):
        threshold = rule.get("threshold_value")
        if threshold is None:
            return False, value, None
        match = _compare_values(operator, str(value).lower(), str(threshold).lower())
        return match, value, threshold
    threshold = rule.get("threshold_number")
    if threshold is None:
        raw = rule.get("threshold_value")
        try:
            threshold = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            threshold = None
    if threshold is None:
        return False, value, None
    if metric in ("rate_percentile", "trend_percentile") and threshold <= 1:
        threshold = threshold * 100
    match = _compare_values(operator, float(value), float(threshold))
    return match, float(value), float(threshold)


def _fetch_ward_payloads(cursor, coverage_end):
    cursor.execute(
        """
        SELECT ward_code, ward_name, population, months, rating_band, rating_score,
               crime_rate_per_100k, trend_slope, trend_pct, yoy_change, harm_score_per_100k,
               rate_percentile, trend_percentile, total_crimes, avg_monthly, total_harm
        FROM ward_metrics
        WHERE coverage_end = %s
        """,
        (coverage_end,),
    )
    rows = cursor.fetchall()
    payloads = {}
    for row in rows:
        payload = _ward_payload(row)
        if payload.get("ward_code"):
            payloads[payload["ward_code"]] = payload
    return payloads


def _alert_rule_row(row):
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "rule_type": row.get("rule_type"),
        "ward_code": row.get("ward_code"),
        "metric": row.get("metric"),
        "operator": row.get("operator"),
        "threshold_value": row.get("threshold_value"),
        "threshold_number": row.get("threshold_number"),
        "filter_json": _parse_filter_json(row.get("filter_json")),
        "trigger_on": row.get("trigger_on") or "enter",
        "window_months": row.get("window_months"),
        "is_active": row.get("is_active"),
        "muted_until": _serialize_datetime(row.get("muted_until")),
        "notify_emails": _normalize_emails(row.get("notify_emails")),
        "created_at": _serialize_datetime(row.get("created_at")),
        "updated_at": _serialize_datetime(row.get("updated_at")),
    }


def _alert_event_row(row):
    return {
        "id": row.get("id"),
        "alert_rule_id": row.get("alert_rule_id"),
        "ward_code": row.get("ward_code"),
        "ward_name": row.get("ward_name"),
        "dataset_version": row.get("dataset_version"),
        "coverage_end": _format_month(row.get("coverage_end")),
        "status": row.get("status") or "open",
        "message": row.get("message"),
        "metric": row.get("metric"),
        "operator": row.get("operator"),
        "threshold_value": row.get("threshold_value"),
        "threshold_number": row.get("threshold_number"),
        "observed_value": row.get("observed_value"),
        "observed_text": row.get("observed_text"),
        "value_json": _parse_filter_json(row.get("value_json")),
        "triggered_at": _serialize_datetime(row.get("triggered_at")),
        "acknowledged_at": _serialize_datetime(row.get("acknowledged_at")),
        "rule_name": row.get("rule_name"),
        "rule_type": row.get("rule_type"),
    }


def _evaluate_alerts(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT coverage_end FROM ward_metrics "
            "ORDER BY coverage_end DESC NULLS LAST LIMIT 2"
        )
        coverage_rows = cursor.fetchall()
        if not coverage_rows:
            return {"evaluated": 0, "created": 0, "coverage_end": None}
        coverage_end = coverage_rows[0].get("coverage_end")
        previous_end = (
            coverage_rows[1].get("coverage_end") if len(coverage_rows) > 1 else None
        )
        cursor.execute(
            """
            SELECT dataset_version
            FROM ward_metrics
            WHERE coverage_end = %s
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
            """,
            (coverage_end,),
        )
        dataset_row = cursor.fetchone() or {}
        dataset_version = dataset_row.get("dataset_version")

        cursor.execute(
            """
            SELECT *
            FROM alert_rules
            WHERE is_active = TRUE
              AND (muted_until IS NULL OR muted_until <= NOW())
            ORDER BY id
            """
        )
        rules = cursor.fetchall()
        if not rules:
            return {
                "evaluated": 0,
                "created": 0,
                "coverage_end": _format_month(coverage_end),
            }

        current_payloads = _fetch_ward_payloads(cursor, coverage_end)
        previous_payloads = (
            _fetch_ward_payloads(cursor, previous_end) if previous_end else {}
        )

        cursor.execute(
            "SELECT alert_rule_id, ward_code FROM alert_events WHERE coverage_end = %s",
            (coverage_end,),
        )
        existing = {
            (row.get("alert_rule_id"), row.get("ward_code") or "")
            for row in cursor.fetchall()
        }

        total_created = 0
        total_evaluated = 0
        for rule_row in rules:
            rule = _alert_rule_row(rule_row)
            rule_id = rule["id"]
            matched_count = 0
            created_count = 0
            target_codes = (
                [rule.get("ward_code")] if rule.get("ward_code") else current_payloads.keys()
            )
            for code in target_codes:
                if not code:
                    continue
                ward = current_payloads.get(code)
                if not ward:
                    continue
                total_evaluated += 1
                match, observed_value, threshold_value = _rule_matches(ward, rule)
                if not match:
                    continue
                matched_count += 1

                if rule.get("trigger_on") == "enter":
                    prev = previous_payloads.get(code)
                    if prev:
                        prev_match, _, _ = _rule_matches(prev, rule)
                        if prev_match:
                            continue

                if (rule_id, code) in existing:
                    continue

                message = (
                    f"Alert '{rule['name']}' triggered for {ward.get('ward_name')} ({code})."
                )
                cursor.execute(
                    """
                    INSERT INTO alert_events (
                        alert_rule_id, dataset_version, coverage_end, ward_code, ward_name,
                        metric, operator, threshold_value, threshold_number, observed_value,
                        observed_text, status, message, value_json, triggered_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        rule_id,
                        dataset_version,
                        coverage_end,
                        code,
                        ward.get("ward_name"),
                        rule.get("metric"),
                        rule.get("operator"),
                        rule.get("threshold_value"),
                        rule.get("threshold_number"),
                        observed_value if isinstance(observed_value, (int, float)) else None,
                        observed_value if isinstance(observed_value, str) else None,
                        message,
                        json.dumps(
                            {
                                "rating_band": ward.get("rating_band"),
                                "rating_score": ward.get("rating_score"),
                                "crime_rate_per_100k": ward.get("crime_rate_per_100k"),
                                "trend_slope": ward.get("trend_slope"),
                                "trend_pct": ward.get("trend_pct"),
                                "yoy_change": ward.get("yoy_change"),
                                "harm_score_per_100k": ward.get("harm_score_per_100k"),
                                "rate_percentile": ward.get("rate_percentile"),
                                "trend_percentile": ward.get("trend_percentile"),
                                "coverage_confidence": ward.get("coverage_confidence"),
                            }
                        ),
                        now,
                    ),
                )
                event_id = cursor.fetchone().get("id")
                created_count += 1
                total_created += 1
                existing.add((rule_id, code))

                recipients = rule.get("notify_emails") or []
                if recipients:
                    subject = f"CrimeMap Alert: {rule.get('name')}"
                    body = (
                        f"{message}\n\n"
                        f"Coverage end: {_format_month(coverage_end)}\n"
                        f"Rating band: {ward.get('rating_band')}\n"
                        f"Rate / 100k: {ward.get('crime_rate_per_100k')}\n"
                        f"Trend slope: {ward.get('trend_slope')}\n"
                    )
                    success, error = _send_alert_email(recipients, subject, body)
                    status = "sent" if success else "failed"
                    cursor.execute(
                        """
                        INSERT INTO alert_notifications
                        (alert_event_id, channel, recipient, status, sent_at, error)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event_id,
                            "email",
                            ", ".join(recipients),
                            status,
                            now if success else None,
                            error,
                        ),
                    )

            cursor.execute(
                """
                INSERT INTO alert_rule_runs
                (alert_rule_id, dataset_version, coverage_end, evaluated_at,
                 matched_count, created_count, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rule_id,
                    dataset_version,
                    coverage_end,
                    now,
                    matched_count,
                    created_count,
                    "completed",
                ),
            )

        return {
            "evaluated": total_evaluated,
            "created": total_created,
            "coverage_end": _format_month(coverage_end),
            "dataset_version": dataset_version,
        }


def _load_wards():
    wards, dataset = _load_ward_analysis_from_db()
    if wards is not None:
        return wards, "db", dataset
    return _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH), "csv", None


def _load_summary(wards, source):
    if not wards:
        return {
            "source": source or "empty",
            "total_wards": 0,
            "high_crime_wards": 0,
            "avg_rate_per_100k": None,
            "latest_month": None,
            "band_counts": {},
            "total_population": None,
            "sparkline": [],
        }

    total_wards = len(wards)
    high_crime = sum(1 for ward in wards if ward["high_crime_rate"])
    rate_values = [
        ward["crime_rate_per_100k"]
        for ward in wards
        if ward["crime_rate_per_100k"] is not None
    ]
    avg_rate = sum(rate_values) / len(rate_values) if rate_values else None

    latest = None
    for ward in wards:
        last_month = ward.get("last_month")
        if not last_month:
            continue
        try:
            month_val = datetime.strptime(last_month, "%Y-%m")
        except ValueError:
            continue
        if latest is None or month_val > latest:
            latest = month_val

    band_counts = {}
    total_population = 0
    population_count = 0
    for ward in wards:
        band = ward.get("rating_band") or "Unknown"
        band_counts[band] = band_counts.get(band, 0) + 1
        if ward.get("population") is not None:
            total_population += ward["population"]
            population_count += 1

    latest_month = latest.strftime("%Y-%m") if latest else None
    return {
        "source": source,
        "total_wards": total_wards,
        "high_crime_wards": high_crime,
        "avg_rate_per_100k": round(avg_rate, 2) if avg_rate is not None else None,
        "latest_month": latest_month,
        "band_counts": band_counts,
        "total_population": total_population if population_count else None,
        "sparkline": _load_history_sparkline(CRIME_HISTORY_PATH, window=6),
    }


def _build_ward_filters_sql(
    query=None,
    band=None,
    min_rate_percentile=None,
    max_rate_percentile=None,
    min_trend_slope=None,
    max_trend_slope=None,
    min_yoy_change=None,
    max_yoy_change=None,
    coverage_confidence=None,
):
    clauses = []
    params = []
    if query:
        clauses.append("(ward_name ILIKE %s OR ward_code ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    if band:
        clauses.append("LOWER(rating_band) = LOWER(%s)")
        params.append(band)
    if min_rate_percentile is not None:
        clauses.append("rate_percentile >= %s")
        params.append(min_rate_percentile)
    if max_rate_percentile is not None:
        clauses.append("rate_percentile <= %s")
        params.append(max_rate_percentile)
    if min_trend_slope is not None:
        clauses.append("trend_slope >= %s")
        params.append(min_trend_slope)
    if max_trend_slope is not None:
        clauses.append("trend_slope <= %s")
        params.append(max_trend_slope)
    if min_yoy_change is not None:
        clauses.append("yoy_change >= %s")
        params.append(min_yoy_change)
    if max_yoy_change is not None:
        clauses.append("yoy_change <= %s")
        params.append(max_yoy_change)
    if coverage_confidence:
        clauses.append(f"{COVERAGE_CONFIDENCE_SQL} = %s")
        params.append(coverage_confidence)
    return clauses, params


def _resolve_sort(sort):
    sort_map = {
        "rating": "rating_score",
        "rating_score": "rating_score",
        "crime_rate": "crime_rate_per_100k",
        "crime_rate_per_100k": "crime_rate_per_100k",
        "rate_percentile": "rate_percentile",
        "trend": "trend_slope",
        "trend_slope": "trend_slope",
        "trend_pct": "trend_pct",
        "yoy": "yoy_change",
        "yoy_change": "yoy_change",
        "band": "rating_band",
        "rating_band": "rating_band",
        "coverage": "coverage_confidence",
        "coverage_confidence": "coverage_confidence",
        "ward": "ward_name",
        "ward_name": "ward_name",
        "harm_score_per_100k": "harm_score_per_100k",
    }
    return sort_map.get(sort or "", "rating_score")


def _sort_clause(sort_key):
    if sort_key == "coverage_confidence":
        return (
            "CASE "
            "WHEN " + COVERAGE_CONFIDENCE_SQL + " = 'high' THEN 3 "
            "WHEN " + COVERAGE_CONFIDENCE_SQL + " = 'medium' THEN 2 "
            "ELSE 1 END"
        )
    return sort_key


def _normalize_order(order):
    return "asc" if str(order).lower() == "asc" else "desc"


def _harm_case_sql():
    parts = []
    for keyword, score in HARM_KEYWORDS:
        pattern = f"'%{keyword}%'"
        parts.append(f"WHEN crime_type ILIKE {pattern} THEN {score}")
    return "CASE " + " ".join(parts) + " ELSE 1 END"


app = FastAPI(title="CrimeMap API", version="0.1.0")

origins = os.getenv("CRIMEMAP_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/assets", StaticFiles(directory=OUTPUT_DIR), name="assets")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/summary")
def summary():
    wards, source, _ = _load_wards()
    return _load_summary(wards, source)

@app.get("/api/v2/summary", tags=["v2"], response_model=dict)
def summary_v2():
    wards, source, _ = _load_wards()
    return _load_summary(wards, source)


@app.get("/api/wards")
def wards(limit: int = Query(0, ge=0), sort: str = Query("rating")):
    ward_rows, _, _ = _load_wards()
    if sort == "rate":
        ward_rows.sort(key=lambda row: row.get("crime_rate_per_100k") or 0, reverse=True)
    elif sort == "trend":
        ward_rows.sort(key=lambda row: row.get("trend_slope") or 0, reverse=True)
    else:
        ward_rows.sort(key=lambda row: row.get("rating_score") or 0, reverse=True)

    if limit:
        ward_rows = ward_rows[:limit]
    return ward_rows


@app.get("/api/v2/wards", tags=["v2", "wards"], response_model=WardListResponse)
def wards_v2(
    q: str | None = Query(None, description="Search by ward name or code."),
    band: str | None = Query(None, description="Filter by rating band."),
    min_rate_percentile: float | None = Query(
        None, ge=0, le=100, description="Minimum rate percentile."
    ),
    max_rate_percentile: float | None = Query(
        None, ge=0, le=100, description="Maximum rate percentile."
    ),
    min_trend_slope: float | None = Query(None, description="Minimum trend slope."),
    max_trend_slope: float | None = Query(None, description="Maximum trend slope."),
    min_yoy_change: float | None = Query(None, description="Minimum YoY change."),
    max_yoy_change: float | None = Query(None, description="Maximum YoY change."),
    coverage_confidence: str | None = Query(
        None, description="Filter by coverage confidence (high|medium|low)."
    ),
    sort: str = Query("rating_score", description="Sort field."),
    order: str = Query("desc", description="Sort order (asc|desc)."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = q.strip() if q else None
    band_value = band.strip() if band else None
    if band_value and band_value.lower() == "all":
        band_value = None
    confidence_value = coverage_confidence.strip().lower() if coverage_confidence else None
    if confidence_value == "all":
        confidence_value = None

    sort_key = _resolve_sort(sort)
    order = _normalize_order(order)
    min_rate = _to_fraction(min_rate_percentile)
    max_rate = _to_fraction(max_rate_percentile)

    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = _latest_dataset(cursor) or _latest_ward_dataset(cursor)
                if not dataset:
                    return WardListResponse(
                        items=[],
                        total=0,
                        limit=limit,
                        offset=offset,
                        sort=sort_key,
                        order=order,
                        source="db",
                        filters={},
                        coverage_start=None,
                        coverage_end=None,
                    )
                dataset_dict = dict(dataset) if not isinstance(dataset, dict) else dataset
                dataset_version = dataset_dict.get("dataset_version")
                coverage_end = dataset_dict.get("coverage_end")
                coverage_start = dataset_dict.get("coverage_start")

                clauses, params = _build_ward_filters_sql(
                    query=query,
                    band=band_value,
                    min_rate_percentile=min_rate,
                    max_rate_percentile=max_rate,
                    min_trend_slope=min_trend_slope,
                    max_trend_slope=max_trend_slope,
                    min_yoy_change=min_yoy_change,
                    max_yoy_change=max_yoy_change,
                    coverage_confidence=confidence_value,
                )
                where_parts = ["dataset_version = %s", "coverage_end = %s"] + clauses
                where = " AND ".join(where_parts)
                base_params = [dataset_version, coverage_end] + params

                cursor.execute(
                    sql.SQL("SELECT COUNT(*) AS total FROM ward_metrics WHERE ") + sql.SQL(where),
                    base_params,
                )
                total = cursor.fetchone().get("total") or 0

                order_expr = _sort_clause(sort_key)
                cursor.execute(
                    sql.SQL("SELECT ward_code, ward_name, population, number_of_crimes, crime_rate_per_100k,"
                    "       rate_percentile, rate_rank, high_crime_rate, total_crimes, avg_monthly,"
                    "       trend_change, trend_pct, trend_slope, yoy_current, yoy_prior, yoy_change,"
                    "       total_harm, harm_score_per_100k, months, first_month, last_month,"
                    "       rating_score, rating_band, trend_percentile, annualized_crime_rate_per_100k "
                    "FROM ward_metrics "
                    "WHERE ") + sql.SQL(where) + sql.SQL(" "
                    "ORDER BY ") + sql.SQL(order_expr) + sql.SQL(" " + order + " NULLS LAST, ward_name ASC "
                    "LIMIT %s OFFSET %s"),
                    base_params + [limit, offset],
                )
                items = [_ward_payload(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        return WardListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            sort=sort_key,
            order=order,
            source="db",
            filters={
                "q": query,
                "band": band_value,
                "min_rate_percentile": min_rate_percentile,
                "max_rate_percentile": max_rate_percentile,
                "min_trend_slope": min_trend_slope,
                "max_trend_slope": max_trend_slope,
                "min_yoy_change": min_yoy_change,
                "max_yoy_change": max_yoy_change,
                "coverage_confidence": confidence_value,
            },
            coverage_start=_format_month(coverage_start),
            coverage_end=_format_month(coverage_end),
        )

    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    coverage_start, coverage_end = _coverage_from_wards(wards)
    filtered = _filter_ward_rows(
        wards,
        query=query,
        band=band_value,
        min_rate_percentile=min_rate_percentile,
        max_rate_percentile=max_rate_percentile,
        min_trend_slope=min_trend_slope,
        max_trend_slope=max_trend_slope,
        min_yoy_change=min_yoy_change,
        max_yoy_change=max_yoy_change,
        coverage_confidence=confidence_value,
    )

    def sort_value(row):
        if sort_key == "ward_name":
            return row.get("ward_name") or ""
        if sort_key == "coverage_confidence":
            return {"low": 1, "medium": 2, "high": 3}.get(
                row.get("coverage_confidence") or "low", 1
            )
        if sort_key == "rating_band":
            order_map = {"High": 4, "Elevated": 3, "Watch": 2, "Stable": 1}
            return order_map.get(row.get("rating_band"), 0)
        return row.get(sort_key) if row.get(sort_key) is not None else 0

    filtered.sort(key=sort_value, reverse=order == "desc")
    total = len(filtered)
    items = filtered[offset : offset + limit]

    return WardListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        sort=sort_key,
        order=order,
        source="csv",
        filters={
            "q": query,
            "band": band_value,
            "min_rate_percentile": min_rate_percentile,
            "max_rate_percentile": max_rate_percentile,
            "min_trend_slope": min_trend_slope,
            "max_trend_slope": max_trend_slope,
            "min_yoy_change": min_yoy_change,
            "max_yoy_change": max_yoy_change,
            "coverage_confidence": confidence_value,
        },
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )


@app.get("/api/v2/wards/{ward_code}", tags=["v2", "wards"], response_model=WardDetailResponse)
def ward_detail(ward_code: str):
    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = _latest_dataset(cursor) or _latest_ward_dataset(cursor)
                if not dataset:
                    raise HTTPException(status_code=404, detail="Ward not found")
                dataset_version = dataset.get("dataset_version")
                coverage_end = dataset.get("coverage_end")
                coverage_start = dataset.get("coverage_start")
                cursor.execute(
                    """
                    SELECT ward_code, ward_name, population, number_of_crimes, crime_rate_per_100k,
                           rate_percentile, rate_rank, high_crime_rate, total_crimes, avg_monthly,
                           trend_change, trend_pct, trend_slope, yoy_current, yoy_prior, yoy_change,
                           total_harm, harm_score_per_100k, months, first_month, last_month,
                           rating_score, rating_band, trend_percentile, annualized_crime_rate_per_100k
                    FROM ward_metrics
                    WHERE dataset_version = %s AND coverage_end = %s AND ward_code = %s
                    LIMIT 1
                    """,
                    (dataset_version, coverage_end, ward_code),
                )
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Ward not found")
                ward = _ward_payload(row)

                if ward.get("rate_percentile") is None:
                    cursor.execute(
                        """
                        SELECT pct FROM (
                            SELECT ward_code,
                                   percent_rank() OVER (ORDER BY crime_rate_per_100k) AS pct
                            FROM ward_metrics
                            WHERE dataset_version = %s AND coverage_end = %s
                        ) ranked
                        WHERE ward_code = %s
                        """,
                        (dataset_version, coverage_end, ward_code),
                    )
                    pct_row = cursor.fetchone()
                    if pct_row and pct_row.get("pct") is not None:
                        ward["rate_percentile"] = _as_percent(pct_row.get("pct"))

                if ward.get("trend_percentile") is None:
                    cursor.execute(
                        """
                        SELECT pct FROM (
                            SELECT ward_code,
                                   percent_rank() OVER (ORDER BY trend_slope) AS pct
                            FROM ward_metrics
                            WHERE dataset_version = %s AND coverage_end = %s
                        ) ranked
                        WHERE ward_code = %s
                        """,
                        (dataset_version, coverage_end, ward_code),
                    )
                    pct_row = cursor.fetchone()
                    if pct_row and pct_row.get("pct") is not None:
                        ward["trend_percentile"] = _as_percent(pct_row.get("pct"))

                if ward.get("rating_score") is None:
                    rate_weight = (
                        ward["rate_percentile"] / 100 if ward.get("rate_percentile") else 0
                    )
                    trend_weight = (
                        ward["trend_percentile"] / 100 if ward.get("trend_percentile") else 0
                    )
                    ward["rating_score"] = round((0.7 * rate_weight + 0.3 * trend_weight) * 100, 1)
                if ward.get("rating_band") is None:
                    ward["rating_band"] = _rating_band(ward.get("rating_score"))

                crime_types = _ward_type_trends_from_db(
                    cursor, dataset_version, coverage_end, ward_code
                )
                try:
                    officials = _ward_officials_from_db(cursor, ward_code)
                except Exception:
                    officials = []
        finally:
            conn.close()

        return WardDetailResponse(
            ward=ward,
            rating_explain=_rating_explain(ward),
            crime_types=crime_types,
            officials=officials,
            source="db",
            coverage_start=_format_month(coverage_start),
            coverage_end=_format_month(coverage_end),
        )

    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    row = next((item for item in wards if item.get("ward_code") == ward_code), None)
    if not row:
        raise HTTPException(status_code=404, detail="Ward not found")
    ward = _ward_payload(row)
    crime_types = _ward_type_trends_from_csv(
        os.path.join(OUTPUT_DIR, "ward_crime_type_trends.csv"),
        ward_code,
    )
    officials = _ward_officials_from_csv(WARD_OFFICIALS_PATH, ward_code)
    coverage_start, coverage_end = _coverage_from_wards(wards)
    return WardDetailResponse(
        ward=ward,
        rating_explain=_rating_explain(ward),
        crime_types=crime_types,
        officials=officials,
        source="csv",
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )


def _month_to_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m").date()
    except ValueError:
        return None


def _timeseries_from_csv(ward_code, metric, crime_type, start, end):
    ward_name = ""
    population = None
    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    for row in wards:
        if row.get("ward_code") == ward_code:
            ward_name = row.get("ward_name") or ""
            population = row.get("population")
            break

    counts = {}
    harms = {}
    candidates = [CLEANED_CRIME_PATH, CRIME_HISTORY_PATH, RAW_CRIME_PATH]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            code_key = (
                "WardCode"
                if "WardCode" in fieldnames
                else "WardCode_w"
                if "WardCode_w" in fieldnames
                else None
            )
            type_key = (
                "Crime type" if "Crime type" in fieldnames else "CrimeType" if "CrimeType" in fieldnames else None
            )
            month_key = "Month" if "Month" in fieldnames else "month" if "month" in fieldnames else None
            if not code_key or not type_key or not month_key:
                continue
            for row in reader:
                if str(row.get(code_key) or "").strip() != str(ward_code):
                    continue
                if crime_type and str(row.get(type_key) or "").strip().lower() != str(crime_type).strip().lower():
                    continue
                month = _normalize_month(row.get(month_key))
                if not month:
                    continue
                if start and month < start:
                    continue
                if end and month > end:
                    continue
                counts[month] = counts.get(month, 0) + 1
                harms[month] = harms.get(month, 0) + _harm_weight(row.get(type_key))
        if counts:
            break

    points, values = _build_timeseries_points(
        counts, harms, metric, population, start, end
    )

    return TimeSeriesResponse(
        ward_code=ward_code,
        ward_name=ward_name,
        metric=metric,
        crime_type=crime_type,
        points=points,
        summary=TimeSeriesSummary(**_series_summary(values)),
        source="csv",
        coverage_start=start,
        coverage_end=end,
    )


@app.get(
    "/api/v2/wards/{ward_code}/timeseries",
    tags=["v2", "timeseries"],
    response_model=TimeSeriesResponse,
)
def ward_timeseries(
    ward_code: str,
    metric: str = Query("rate", pattern="^(rate|harm|count)$"),
    crime_type: str | None = Query(
        None, alias="type", description="Filter by crime type."
    ),
    start: str | None = Query(None, description="Start month YYYY-MM."),
    end: str | None = Query(None, description="End month YYYY-MM."),
):
    start_date = _month_to_date(start)
    end_date = _month_to_date(end)

    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = _latest_crime_dataset(cursor)
                if not dataset:
                    return TimeSeriesResponse(
                        ward_code=ward_code,
                        ward_name="",
                        metric=metric,
                        crime_type=crime_type,
                        points=[],
                        summary=TimeSeriesSummary(window=3),
                        source="db",
                    )
                dataset_version = dataset.get("dataset_version")
                coverage_end = dataset.get("coverage_end")
                cursor.execute(
                    """
                    SELECT ward_name, population
                    FROM ward_metrics
                    WHERE dataset_version = %s AND ward_code = %s
                    ORDER BY coverage_end DESC NULLS LAST
                    LIMIT 1
                    """,
                    (dataset_version, ward_code),
                )
                ward_meta = cursor.fetchone() or {}
                ward_name = ward_meta.get("ward_name") or ""
                population = ward_meta.get("population")

                clauses = ["dataset_version = %s", "ward_code = %s"]
                params = [dataset_version, ward_code]
                if crime_type:
                    clauses.append("LOWER(crime_type) = LOWER(%s)")
                    params.append(crime_type)
                if start_date:
                    clauses.append("month >= %s")
                    params.append(start_date)
                if end_date:
                    clauses.append("month <= %s")
                    params.append(end_date)

                harm_case = _harm_case_sql()
                cursor.execute(
                    f"""
                    SELECT date_trunc('month', month)::date AS month,
                           COUNT(*) AS crime_count,
                           SUM({harm_case}) AS harm_total
                    FROM crimes
                    WHERE {' AND '.join(clauses)}
                    GROUP BY month
                    ORDER BY month
                    """,
                    params,
                )
                counts = {}
                harms = {}
                for row in cursor.fetchall():
                    month = _format_month(row.get("month"))
                    count = row.get("crime_count") or 0
                    harm_total = row.get("harm_total") or 0
                    if month:
                        counts[month] = count
                        harms[month] = harm_total
                end_key = end or _format_month(coverage_end)
                points, values = _build_timeseries_points(
                    counts, harms, metric, population, start, end_key
                )
        finally:
            conn.close()

        numeric = [value for value in values if value is not None]
        if points and len(numeric) >= 2:
            return TimeSeriesResponse(
                ward_code=ward_code,
                ward_name=ward_name,
                metric=metric,
                crime_type=crime_type,
                points=points,
                summary=TimeSeriesSummary(**_series_summary(values)),
                source="db",
                coverage_start=start,
                coverage_end=_format_month(coverage_end),
            )

    return _timeseries_from_csv(ward_code, metric, crime_type, start, end)


@app.get("/api/gap-report")
def gap_report():
    _, source, dataset = _load_wards()
    report = None
    if source == "db":
        dataset_version = dataset.get("dataset_version") if dataset else None
        coverage_end = dataset.get("coverage_end") if dataset else None
        report = _load_gap_report_from_db(dataset_version, coverage_end)
    if not report:
        report = _load_gap_report_from_csv(GAP_REPORT_PATH)
        if not report:
            return {}
        return {
            "checked_at": report.get("CheckedAt") or "",
            "history_latest": report.get("HistoryLatest") or "",
            "latest_available": report.get("LatestAvailable") or "",
            "gap_months": _parse_int(report.get("GapMonths")),
            "default_start_month": report.get("DefaultStartMonth") or "",
        }
    return report


@app.get("/ops/status")
def ops_status():
    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = _latest_dataset(cursor)
        finally:
            conn.close()

        if dataset:
            last_run = dataset.get("finished_at") or dataset.get("started_at")
            return {
                "status": dataset.get("status") or "completed",
                "dataset_version": dataset.get("dataset_version"),
                "coverage_start": _format_month(dataset.get("coverage_start")),
                "coverage_end": _format_month(dataset.get("coverage_end")),
                "rows_loaded": dataset.get("rows_loaded"),
                "last_run": last_run.isoformat() if last_run else None,
                "source": "db",
            }
        return {
            "status": "empty",
            "dataset_version": None,
            "coverage_start": None,
            "coverage_end": None,
            "rows_loaded": None,
            "last_run": None,
            "source": "db",
        }

    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    coverage_start, coverage_end = _coverage_from_wards(wards)
    return {
        "status": "csv",
        "dataset_version": None,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "rows_loaded": None,
        "last_run": None,
        "source": "csv",
    }


@app.get("/ops/jobs", tags=["ops"], response_model=OpsJobsResponse)
def ops_jobs(limit: int = Query(20, ge=1, le=200)):
    conn = _db_connect()
    if not conn:
        return OpsJobsResponse(source="csv", jobs=[])

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, dataset_version, coverage_start, coverage_end, status, source,
                       started_at, finished_at, rows_loaded, notes
                FROM job_runs
                ORDER BY started_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    log_base = os.getenv("CRIMEMAP_JOB_LOG_BASE")
    jobs = []
    for row in rows:
        log_url = None
        if log_base:
            log_url = f"{log_base.rstrip('/')}/{row.get('id')}"
        jobs.append(
            {
                "id": row.get("id"),
                "dataset_version": row.get("dataset_version"),
                "coverage_start": _format_month(row.get("coverage_start")),
                "coverage_end": _format_month(row.get("coverage_end")),
                "status": row.get("status"),
                "source": row.get("source"),
                "started_at": row.get("started_at").isoformat()
                if row.get("started_at")
                else None,
                "finished_at": row.get("finished_at").isoformat()
                if row.get("finished_at")
                else None,
                "rows_loaded": row.get("rows_loaded"),
                "notes": row.get("notes"),
                "log_url": log_url,
            }
        )
    return OpsJobsResponse(source="db", jobs=jobs)


@app.get("/ops/quality", tags=["ops"], response_model=OpsQualityResponse)
def ops_quality():
    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = _latest_dataset(cursor) or _latest_ward_dataset(cursor)
                if not dataset:
                    return OpsQualityResponse(source="db", confidence_counts={})
                dataset_version = dataset.get("dataset_version")
                coverage_end = dataset.get("coverage_end")
                coverage_start = dataset.get("coverage_start")
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN population IS NULL OR population = 0 THEN 1 ELSE 0 END) AS missing_population,
                        SUM(CASE WHEN months IS NULL OR months < 6 THEN 1 ELSE 0 END) AS short_history,
                        SUM(CASE WHEN {COVERAGE_CONFIDENCE_SQL} = 'high' THEN 1 ELSE 0 END) AS high_confidence,
                        SUM(CASE WHEN {COVERAGE_CONFIDENCE_SQL} = 'medium' THEN 1 ELSE 0 END) AS medium_confidence,
                        SUM(CASE WHEN {COVERAGE_CONFIDENCE_SQL} = 'low' THEN 1 ELSE 0 END) AS low_confidence
                    FROM ward_metrics
                    WHERE dataset_version = %s AND coverage_end = %s
                    """,
                    (dataset_version, coverage_end),
                )
                stats = cursor.fetchone() or {}
                cursor.execute(
                    """
                    SELECT COUNT(*) AS crime_rows,
                           SUM(
                               CASE
                                   WHEN longitude IS NULL OR latitude IS NULL THEN 1
                                   WHEN latitude NOT BETWEEN -90 AND 90 THEN 1
                                   WHEN longitude NOT BETWEEN -180 AND 180 THEN 1
                                   ELSE 0
                               END
                           ) AS invalid_coords
                    FROM crimes
                    WHERE dataset_version = %s
                    """,
                    (dataset_version,),
                )
                crime_stats = cursor.fetchone() or {}
                gap_report = _load_gap_report_from_db(dataset_version, coverage_end)
        finally:
            conn.close()

        total = stats.get("total") or 0
        missing = stats.get("missing_population") or 0
        short_history = stats.get("short_history") or 0
        return OpsQualityResponse(
            source="db",
            dataset_version=dataset_version,
            coverage_start=_format_month(coverage_start),
            coverage_end=_format_month(coverage_end),
            population_missing=missing,
            population_missing_pct=round(missing / total * 100, 2) if total else None,
            short_history=short_history,
            short_history_pct=round(short_history / total * 100, 2) if total else None,
            invalid_coords=crime_stats.get("invalid_coords") or 0,
            invalid_coords_pct=(
                round((crime_stats.get("invalid_coords") or 0) / crime_stats.get("crime_rows") * 100, 2)
                if crime_stats.get("crime_rows")
                else None
            ),
            crime_rows=crime_stats.get("crime_rows") or 0,
            ward_rows=total,
            confidence_counts={
                "high": stats.get("high_confidence") or 0,
                "medium": stats.get("medium_confidence") or 0,
                "low": stats.get("low_confidence") or 0,
            },
            gap_report=gap_report,
        )

    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    total = len(wards)
    missing = 0
    short_history = 0
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for row in wards:
        payload = _ward_payload(row)
        if "population_missing" in payload["coverage_flags"]:
            missing += 1
        if "short_history" in payload["coverage_flags"]:
            short_history += 1
        confidence = payload.get("coverage_confidence") or "low"
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
    coverage_start, coverage_end = _coverage_from_wards(wards)
    gap = _load_gap_report_from_csv(GAP_REPORT_PATH)
    gap_report = None
    if gap:
        gap_report = {
            "checked_at": gap.get("CheckedAt") or "",
            "history_latest": gap.get("HistoryLatest") or "",
            "latest_available": gap.get("LatestAvailable") or "",
            "gap_months": _parse_int(gap.get("GapMonths")),
            "default_start_month": gap.get("DefaultStartMonth") or "",
        }
    return OpsQualityResponse(
        source="csv",
        dataset_version=None,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        population_missing=missing,
        population_missing_pct=round(missing / total * 100, 2) if total else None,
        short_history=short_history,
        short_history_pct=round(short_history / total * 100, 2) if total else None,
        invalid_coords=None,
        invalid_coords_pct=None,
        crime_rows=None,
        ward_rows=total,
        confidence_counts=confidence_counts,
        gap_report=gap_report,
    )


@app.get("/api/v2/alerts/rules", tags=["v2", "alerts"], response_model=list[AlertRuleResponse])
def list_alert_rules(
    active: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            clauses = []
            params = []
            if active is not None:
                clauses.append("is_active = %s")
                params.append(active)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor.execute(
                f"""
                SELECT *
                FROM alert_rules
                {where}
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return [_alert_rule_row(row) for row in rows]


@app.post("/api/v2/alerts/rules", tags=["v2", "alerts"], response_model=AlertRuleResponse)
def create_alert_rule(payload: AlertRuleCreate):
    if payload.rule_type == "ward" and not payload.ward_code:
        raise HTTPException(status_code=400, detail="ward_code is required for ward rules")
    if payload.rule_type == "ward" and not payload.metric:
        raise HTTPException(status_code=400, detail="metric is required for ward rules")
    if payload.rule_type == "filter" and not (payload.filter_json or payload.metric):
        raise HTTPException(status_code=400, detail="filter_json or metric is required")

    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    muted_until = _parse_iso_datetime(payload.muted_until)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alert_rules (
                    name, description, rule_type, ward_code, metric, operator,
                    threshold_value, threshold_number, filter_json, trigger_on,
                    window_months, is_active, muted_until, notify_emails, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING *
                """,
                (
                    payload.name,
                    payload.description,
                    payload.rule_type,
                    payload.ward_code,
                    payload.metric,
                    payload.operator,
                    payload.threshold_value,
                    payload.threshold_number,
                    json.dumps(payload.filter_json) if payload.filter_json else None,
                    payload.trigger_on,
                    payload.window_months,
                    payload.is_active,
                    muted_until,
                    payload.notify_emails,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
    finally:
        conn.close()

    return _alert_rule_row(row)


@app.put("/api/v2/alerts/rules/{rule_id}", tags=["v2", "alerts"], response_model=AlertRuleResponse)
def update_alert_rule(rule_id: int, payload: AlertRuleUpdate):
    updates = []
    params = []
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    for key, value in fields.items():
        if key == "filter_json":
            updates.append("filter_json = %s")
            params.append(json.dumps(value) if value else None)
        elif key == "muted_until":
            updates.append("muted_until = %s")
            params.append(_parse_iso_datetime(value))
        else:
            updates.append(f"{key} = %s")
            params.append(value)
    updates.append("updated_at = NOW()")

    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE alert_rules
                SET {', '.join(updates)}
                WHERE id = %s
                RETURNING *
                """,
                params + [rule_id],
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Alert rule not found")
            conn.commit()
    finally:
        conn.close()

    return _alert_rule_row(row)


@app.delete("/api/v2/alerts/rules/{rule_id}", tags=["v2", "alerts"])
def delete_alert_rule(rule_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM alert_rules WHERE id = %s", (rule_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Alert rule not found")
            conn.commit()
    finally:
        conn.close()
    return {"status": "deleted"}


@app.post("/api/v2/alerts/rules/{rule_id}/mute", tags=["v2", "alerts"])
def mute_alert_rule(rule_id: int, payload: AlertMuteRequest):
    until = None
    if payload.until:
        until = _parse_iso_datetime(payload.until)
    elif payload.hours:
        until = datetime.now(timezone.utc) + timedelta(hours=payload.hours)

    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE alert_rules SET muted_until = %s, updated_at = NOW() WHERE id = %s",
                (until, rule_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Alert rule not found")
            conn.commit()
    finally:
        conn.close()
    return {"status": "muted", "muted_until": _serialize_datetime(until)}


@app.post("/api/v2/alerts/rules/{rule_id}/unmute", tags=["v2", "alerts"])
def unmute_alert_rule(rule_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE alert_rules SET muted_until = NULL, updated_at = NOW() WHERE id = %s",
                (rule_id,),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Alert rule not found")
            conn.commit()
    finally:
        conn.close()
    return {"status": "unmuted"}


@app.get(
    "/api/v2/alerts/events",
    tags=["v2", "alerts"],
    response_model=AlertEventListResponse,
)
def list_alert_events(
    status: str | None = Query(None),
    rule_id: int | None = Query(None),
    ward_code: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            clauses = []
            params = []
            if status:
                clauses.append("e.status = %s")
                params.append(status)
            if rule_id:
                clauses.append("e.alert_rule_id = %s")
                params.append(rule_id)
            if ward_code:
                clauses.append("e.ward_code = %s")
                params.append(ward_code)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM alert_events e
                {where}
                """,
                params,
            )
            total = cursor.fetchone().get("total") or 0

            cursor.execute(
                f"""
                SELECT e.*, r.name AS rule_name, r.rule_type AS rule_type
                FROM alert_events e
                JOIN alert_rules r ON r.id = e.alert_rule_id
                {where}
                ORDER BY e.triggered_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return AlertEventListResponse(
        items=[_alert_event_row(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        source="db",
    )


@app.post("/api/v2/alerts/events/{event_id}/acknowledge", tags=["v2", "alerts"])
def acknowledge_alert(event_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE alert_events
                SET status = 'acknowledged', acknowledged_at = NOW()
                WHERE id = %s
                RETURNING id
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Alert event not found")
            conn.commit()
    finally:
        conn.close()
    return {"status": "acknowledged", "id": event_id}


@app.post("/ops/alerts/evaluate", tags=["ops"])
def evaluate_alerts():
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        result = _evaluate_alerts(conn)
        conn.commit()
    finally:
        conn.close()
    return result


@app.get("/api/map")
def map_info():
    return {
        "wards_map_url": "/assets/wards_interactive_map.html",
        "exists": os.path.exists(WARDS_MAP_PATH),
    }
