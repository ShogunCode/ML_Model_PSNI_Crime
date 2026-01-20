import csv
import json
import os
import re
import smtplib
from collections.abc import Mapping
from datetime import date, datetime, timezone, timedelta
from email.message import EmailMessage
from typing import Any, Literal

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
WARD_TYPE_TRENDS_PATH = os.path.join(OUTPUT_DIR, "ward_crime_type_trends.csv")
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

CORS_ORIGINS = os.getenv("CRIMEMAP_CORS_ORIGINS", "*")
CORS_ALLOW_ORIGINS = [
    origin.strip() for origin in CORS_ORIGINS.split(",") if origin.strip()
] or ["*"]

app = FastAPI(title="PSNI Crime Map API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials="*" not in CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=OUTPUT_DIR, check_dir=False), name="assets")

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
    officials: list[WardOfficial] = Field(default_factory=list)
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


def _row_to_dict(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


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
    if not row:
        return None
    return _row_to_dict(row)


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
    if not row:
        return None
    row_dict = _row_to_dict(row)
    row_dict["status"] = "completed"
    return row_dict


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
    if not row:
        return None
    row_dict = _row_to_dict(row)
    row_dict["status"] = "completed"
    return row_dict


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
        row_dict = _row_to_dict(row)
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
    row_dict = _row_to_dict(row)
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
        row_dict = _row_to_dict(row)
        rows.append(
            {
                "crime_type": row_dict.get("crime_type") or "",
                "crime_type_label": _crime_type_label(row_dict.get("crime_type")),
                "total_crimes": row_dict.get("total_crimes"),
                "avg_monthly": row_dict.get("avg_monthly"),
                "trend_change": row_dict.get("trend_change"),
                "trend_pct": row_dict.get("trend_pct"),
                "trend_slope": row_dict.get("trend_slope"),
                "months": row_dict.get("months"),
                "first_month": _format_month(row_dict.get("first_month")) or "",
                "last_month": _format_month(row_dict.get("last_month")) or "",
                "trend_direction": row_dict.get("trend_direction") or "",
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
        row_dict = _row_to_dict(row)
        officials.append(
            {
                "name": row_dict.get("official_name") or "",
                "role": row_dict.get("role") or "",
                "party": row_dict.get("party") or "",
                "email": row_dict.get("email") or "",
                "phone": row_dict.get("phone") or "",
                "source": row_dict.get("source") or "",
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
        payload = _ward_payload(_row_to_dict(row))
        if payload.get("ward_code"):
            payloads[payload["ward_code"]] = payload
    return payloads


def _alert_rule_row(row):
    # Ensure row is a dict
    if not isinstance(row, dict):
        row = dict(row) if hasattr(row, '__iter__') else {}
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
    # Ensure row is a dict
    if not isinstance(row, dict):
        row = dict(row) if hasattr(row, '__iter__') else row
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


def _month_to_date(value):
    month = _normalize_month(value)
    if not month:
        return None
    try:
        return datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        return None


def _load_ward_dataset():
    wards, dataset = _load_ward_analysis_from_db()
    if wards:
        return wards, dataset, "db"
    wards = _load_ward_analysis_from_csv(WARD_ANALYSIS_PATH)
    if wards:
        return wards, dataset, "csv"
    return [], dataset, "none"


def _gap_report_payload(row):
    if not row:
        return None
    checked_at = row.get("checked_at") or row.get("CheckedAt") or ""
    checked_at = _serialize_datetime(_parse_iso_datetime(checked_at)) or checked_at
    history_latest = row.get("history_latest") or row.get("HistoryLatest")
    latest_available = row.get("latest_available") or row.get("LatestAvailable")
    default_start = row.get("default_start_month") or row.get("DefaultStartMonth")
    gap_months = row.get("gap_months") or row.get("GapMonths")
    return {
        "checked_at": checked_at,
        "history_latest": _format_month(history_latest) or "",
        "latest_available": _format_month(latest_available) or "",
        "gap_months": _parse_int(gap_months),
        "default_start_month": _format_month(default_start) or "",
    }


def _map_info():
    return {
        "wards_map_url": f"/assets/{os.path.basename(WARDS_MAP_PATH)}",
        "exists": os.path.exists(WARDS_MAP_PATH),
    }


def _count_csv_rows(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            total = sum(1 for _ in handle)
    except OSError:
        return None
    if total <= 1:
        return 0
    return total - 1


def _quality_from_wards(wards):
    population_missing = 0
    short_history = 0
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for ward in wards:
        population = ward.get("population")
        months = ward.get("months")
        if population in (None, 0):
            population_missing += 1
        if months is None or months < 6:
            short_history += 1
        confidence = _coverage_confidence(population, months)
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
    total = len(wards)
    population_missing_pct = (population_missing / total * 100) if total else None
    short_history_pct = (short_history / total * 100) if total else None
    return {
        "population_missing": population_missing,
        "population_missing_pct": population_missing_pct,
        "short_history": short_history,
        "short_history_pct": short_history_pct,
        "confidence_counts": confidence_counts,
    }


def _sort_value(value, reverse):
    if isinstance(value, str):
        if not value:
            return "" if reverse else "~~~~"
        return value.lower()
    if value is None:
        return float("-inf") if reverse else float("inf")
    return value


def _find_ward(wards, ward_code):
    needle = str(ward_code or "").strip().lower()
    for ward in wards:
        code = str(ward.get("ward_code") or "").strip().lower()
        if code == needle:
            return ward
    return None


def _timeseries_from_csv(ward_code, crime_type=None):
    if not os.path.exists(CLEANED_CRIME_PATH):
        return {}, {}
    counts = {}
    harms = {}
    needle = str(ward_code or "").strip().lower()
    crime_text = str(crime_type or "").strip().lower() if crime_type else None
    with open(CLEANED_CRIME_PATH, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_code = str(row.get("WardCode") or row.get("ward_code") or "").strip().lower()
            if row_code != needle:
                continue
            row_crime = row.get("Crime type") or row.get("crime_type") or ""
            if crime_text and str(row_crime).strip().lower() != crime_text:
                continue
            month = _normalize_month(row.get("Month"))
            if not month:
                continue
            counts[month] = counts.get(month, 0) + 1
            harms[month] = harms.get(month, 0) + _harm_weight(row_crime)
    return counts, harms


def _timeseries_from_db(ward_code, crime_type=None):
    conn = _db_connect()
    if not conn:
        return {}, {}, None
    counts = {}
    harms = {}
    source = None
    try:
        with conn.cursor() as cursor:
            dataset = _latest_crime_dataset(cursor)
            if dataset:
                dataset = _row_to_dict(dataset)
            dataset_version = dataset.get("dataset_version") if dataset else None
            params = []
            where = [sql.SQL("ward_code = %s")]
            params.append(ward_code)
            if dataset_version:
                where.append(sql.SQL("dataset_version = %s"))
                params.append(dataset_version)
            if crime_type:
                where.append(sql.SQL("crime_type = %s"))
                params.append(crime_type)
            query = sql.SQL(
                "SELECT month, crime_type, COUNT(*) AS count "
                "FROM crimes "
                "WHERE {where} "
                "GROUP BY month, crime_type "
                "ORDER BY month"
            ).format(where=sql.SQL(" AND ").join(where))
            cursor.execute(query, params)
            for row in cursor.fetchall():
                row_dict = _row_to_dict(row)
                month = _format_month(row_dict.get("month"))
                if not month:
                    continue
                count = row_dict.get("count")
                if count is None:
                    continue
                counts[month] = counts.get(month, 0) + count
                harms[month] = harms.get(month, 0) + count * _harm_weight(row_dict.get("crime_type"))
            source = "db"
    except Exception:
        return {}, {}, None
    finally:
        conn.close()
    return counts, harms, source


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/summary")
def get_summary():
    wards, dataset, source = _load_ward_dataset()
    coverage_start, coverage_end = _coverage_from_wards(wards)
    latest_month = None
    if dataset:
        latest_month = _format_month(dataset.get("coverage_end"))
    if not latest_month:
        latest_month = coverage_end
    total_population = sum((ward.get("population") or 0) for ward in wards)
    total_crimes = 0
    high_crime_wards = 0
    rate_values = []
    band_counts = {}
    for ward in wards:
        total_crimes += ward.get("total_crimes") or ward.get("number_of_crimes") or 0
        if ward.get("high_crime_rate") or (ward.get("rating_band") or "").lower() == "high":
            high_crime_wards += 1
        rate = ward.get("crime_rate_per_100k")
        if rate is not None:
            rate_values.append(rate)
        band = ward.get("rating_band") or "Unknown"
        band_counts[band] = band_counts.get(band, 0) + 1
    for key in ("High", "Elevated", "Watch", "Stable"):
        band_counts.setdefault(key, 0)
    avg_rate = round(sum(rate_values) / len(rate_values), 2) if rate_values else None
    sparkline = _load_history_sparkline(CRIME_HISTORY_PATH)
    return {
        "latest_month": latest_month,
        "avg_rate_per_100k": avg_rate,
        "band_counts": band_counts,
        "total_wards": len(wards),
        "total_crimes": total_crimes,
        "total_population": total_population,
        "high_crime_wards": high_crime_wards,
        "sparkline": sparkline,
        "source": source,
    }


@app.get("/api/gap-report")
def get_gap_report():
    report = _load_gap_report_from_db()
    source = "db" if report else "csv"
    if not report:
        report = _gap_report_payload(_load_gap_report_from_csv(GAP_REPORT_PATH))
    if not report:
        return {"source": "none"}
    report["source"] = source
    return report


@app.get("/api/map")
def get_map_info():
    return _map_info()


@app.get("/api/wards")
def list_wards_legacy(
    q: str | None = None,
    band: str | None = None,
    coverage_confidence: str | None = None,
    min_rate_percentile: float | None = None,
    max_rate_percentile: float | None = None,
    min_trend_slope: float | None = None,
    max_trend_slope: float | None = None,
    min_yoy_change: float | None = None,
    max_yoy_change: float | None = None,
    sort: str = "rating_score",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
):
    return list_wards_v2(
        q=q,
        band=band,
        coverage_confidence=coverage_confidence,
        min_rate_percentile=min_rate_percentile,
        max_rate_percentile=max_rate_percentile,
        min_trend_slope=min_trend_slope,
        max_trend_slope=max_trend_slope,
        min_yoy_change=min_yoy_change,
        max_yoy_change=max_yoy_change,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v2/wards", response_model=WardListResponse)
def list_wards_v2(
    q: str | None = None,
    band: str | None = None,
    coverage_confidence: str | None = None,
    min_rate_percentile: float | None = None,
    max_rate_percentile: float | None = None,
    min_trend_slope: float | None = None,
    max_trend_slope: float | None = None,
    min_yoy_change: float | None = None,
    max_yoy_change: float | None = None,
    sort: str = "rating_score",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
):
    wards, dataset, source = _load_ward_dataset()
    rows = _filter_ward_rows(
        wards,
        query=q,
        band=band,
        min_rate_percentile=min_rate_percentile,
        max_rate_percentile=max_rate_percentile,
        min_trend_slope=min_trend_slope,
        max_trend_slope=max_trend_slope,
        min_yoy_change=min_yoy_change,
        max_yoy_change=max_yoy_change,
        coverage_confidence=coverage_confidence,
    )
    sort_key = sort or "rating_score"
    if sort_key in ("ward", "name"):
        sort_key = "ward_name"
    reverse = order == "desc"
    rows.sort(key=lambda item: _sort_value(item.get(sort_key), reverse), reverse=reverse)
    total = len(rows)
    items = rows[offset : offset + limit]
    coverage_start, coverage_end = _coverage_from_wards(wards)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort": sort_key,
        "order": order,
        "source": source,
        "filters": {
            "q": q,
            "band": band,
            "coverage_confidence": coverage_confidence,
            "min_rate_percentile": min_rate_percentile,
            "max_rate_percentile": max_rate_percentile,
            "min_trend_slope": min_trend_slope,
            "max_trend_slope": max_trend_slope,
            "min_yoy_change": min_yoy_change,
            "max_yoy_change": max_yoy_change,
        },
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
    }


@app.get("/api/v2/wards/{ward_code}", response_model=WardDetailResponse)
def get_ward_detail(ward_code: str, limit: int = Query(12, ge=1, le=50)):
    wards, dataset, source = _load_ward_dataset()
    ward = _find_ward(wards, ward_code)
    if not ward:
        raise HTTPException(status_code=404, detail="ward_not_found")
    payload = _ward_payload(ward)
    coverage_start, coverage_end = _coverage_from_wards(wards)
    coverage_start = _normalize_month(payload.get("first_month")) or coverage_start
    coverage_end = _normalize_month(payload.get("last_month")) or coverage_end
    crime_types = []
    officials = []
    if source == "db":
        conn = _db_connect()
        if conn:
            try:
                with conn.cursor() as cursor:
                    dataset_version = dataset.get("dataset_version") if dataset else None
                    coverage_end_db = dataset.get("coverage_end") if dataset else None
                    if not dataset_version or not coverage_end_db:
                        latest = _latest_ward_dataset(cursor)
                        if latest:
                            latest_dict = _row_to_dict(latest)
                            dataset_version = dataset_version or latest_dict.get("dataset_version")
                            coverage_end_db = coverage_end_db or latest_dict.get("coverage_end")
                    if dataset_version and coverage_end_db:
                        crime_types = _ward_type_trends_from_db(
                            cursor,
                            dataset_version,
                            coverage_end_db,
                            ward_code,
                            limit=limit,
                        )
                    officials = _ward_officials_from_db(cursor, ward_code)
            finally:
                conn.close()
    if not crime_types:
        crime_types = _ward_type_trends_from_csv(
            WARD_TYPE_TRENDS_PATH, ward_code, limit=limit
        )
    if not officials:
        officials = _ward_officials_from_csv(WARD_OFFICIALS_PATH, ward_code)
    return {
        "ward": payload,
        "rating_explain": _rating_explain(payload),
        "crime_types": crime_types,
        "officials": officials,
        "source": source,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
    }


@app.get("/api/v2/wards/{ward_code}/timeseries", response_model=TimeSeriesResponse)
def get_ward_timeseries(
    ward_code: str,
    metric: Literal["rate", "count", "harm"] = "rate",
    crime_type: str | None = Query(None, alias="type"),
    window: int = Query(3, ge=1, le=12),
):
    wards, dataset, source = _load_ward_dataset()
    ward = _find_ward(wards, ward_code)
    if not ward:
        raise HTTPException(status_code=404, detail="ward_not_found")
    payload = _ward_payload(ward)
    coverage_start, coverage_end = _coverage_from_wards(wards)
    coverage_start = _normalize_month(payload.get("first_month")) or coverage_start
    coverage_end = _normalize_month(payload.get("last_month")) or coverage_end
    counts, harms = _timeseries_from_csv(ward_code, crime_type)
    series_source = "csv" if counts else None
    if not counts:
        counts, harms, db_source = _timeseries_from_db(ward_code, crime_type)
        series_source = db_source or series_source
    points, values = _build_timeseries_points(
        counts,
        harms,
        metric,
        payload.get("population"),
        coverage_start,
        coverage_end,
    )
    summary = _series_summary(values, window=window)
    return {
        "ward_code": payload.get("ward_code"),
        "ward_name": payload.get("ward_name"),
        "metric": metric,
        "crime_type": crime_type,
        "points": points,
        "summary": summary,
        "source": series_source or source,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
    }


@app.get("/ops/status")
def get_ops_status():
    conn = _db_connect()
    if conn:
        try:
            with conn.cursor() as cursor:
                dataset = None
                try:
                    dataset = _latest_dataset(cursor)
                except Exception:
                    dataset = None
                if not dataset:
                    try:
                        dataset = _latest_ward_dataset(cursor)
                    except Exception:
                        dataset = None
                if not dataset:
                    try:
                        dataset = _latest_crime_dataset(cursor)
                    except Exception:
                        dataset = None
            if dataset:
                dataset = _row_to_dict(dataset)
                return {
                    "status": dataset.get("status") or "completed",
                    "dataset_version": dataset.get("dataset_version"),
                    "coverage_start": _format_month(dataset.get("coverage_start")),
                    "coverage_end": _format_month(dataset.get("coverage_end")),
                    "rows_loaded": dataset.get("rows_loaded"),
                    "last_run": _serialize_datetime(
                        dataset.get("finished_at") or dataset.get("started_at")
                    ),
                    "source": "db",
                }
        finally:
            conn.close()
    wards, dataset, source = _load_ward_dataset()
    coverage_start, coverage_end = _coverage_from_wards(wards)
    last_run = None
    if os.path.exists(WARD_ANALYSIS_PATH):
        last_run = datetime.fromtimestamp(
            os.path.getmtime(WARD_ANALYSIS_PATH), tz=timezone.utc
        ).isoformat()
    return {
        "status": "ready" if wards else "missing",
        "dataset_version": dataset.get("dataset_version") if dataset else None,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "rows_loaded": len(wards) if wards else None,
        "last_run": last_run,
        "source": source,
    }


@app.get("/ops/jobs", response_model=OpsJobsResponse)
def get_ops_jobs(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conn = _db_connect()
    if not conn:
        return {"source": "none", "jobs": []}
    jobs = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, dataset_version, coverage_start, coverage_end, status, source,
                       started_at, finished_at, rows_loaded, notes
                FROM job_runs
                ORDER BY started_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            for row in cursor.fetchall():
                row_dict = dict(row) if not isinstance(row, dict) else row
                jobs.append(
                    {
                        "id": row_dict.get("id"),
                        "dataset_version": row_dict.get("dataset_version"),
                        "coverage_start": _format_month(row_dict.get("coverage_start")),
                        "coverage_end": _format_month(row_dict.get("coverage_end")),
                        "status": row_dict.get("status"),
                        "source": row_dict.get("source"),
                        "started_at": _serialize_datetime(row_dict.get("started_at")),
                        "finished_at": _serialize_datetime(row_dict.get("finished_at")),
                        "rows_loaded": row_dict.get("rows_loaded"),
                        "notes": row_dict.get("notes"),
                        "log_url": None,
                    }
                )
    except Exception:
        return {"source": "none", "jobs": []}
    finally:
        conn.close()
    return {"source": "db", "jobs": jobs}


@app.get("/ops/quality", response_model=OpsQualityResponse)
def get_ops_quality():
    wards, dataset, source = _load_ward_dataset()
    coverage_start, coverage_end = _coverage_from_wards(wards)
    quality = _quality_from_wards(wards)
    crime_rows = None
    if source == "db":
        conn = _db_connect()
        if conn:
            try:
                with conn.cursor() as cursor:
                    dataset_version = dataset.get("dataset_version") if dataset else None
                    if dataset_version:
                        cursor.execute(
                            "SELECT COUNT(*) AS count FROM crimes WHERE dataset_version = %s",
                            (dataset_version,),
                        )
                    else:
                        cursor.execute("SELECT COUNT(*) AS count FROM crimes")
                    row = cursor.fetchone()
                    if row:
                        crime_rows = _row_to_dict(row).get("count")
            except Exception:
                crime_rows = None
            finally:
                conn.close()
    if crime_rows is None:
        crime_rows = _count_csv_rows(CLEANED_CRIME_PATH)
    invalid_coords = None
    invalid_coords_pct = None
    if invalid_coords is not None and crime_rows:
        invalid_coords_pct = invalid_coords / crime_rows * 100
    gap_report = None
    if source == "db":
        gap_report = _load_gap_report_from_db()
    if not gap_report:
        gap_report = _gap_report_payload(_load_gap_report_from_csv(GAP_REPORT_PATH))
    response = {
        "source": source,
        "dataset_version": dataset.get("dataset_version") if dataset else None,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "population_missing": quality["population_missing"],
        "population_missing_pct": quality["population_missing_pct"],
        "short_history": quality["short_history"],
        "short_history_pct": quality["short_history_pct"],
        "invalid_coords": invalid_coords,
        "invalid_coords_pct": invalid_coords_pct,
        "crime_rows": crime_rows,
        "ward_rows": len(wards) if wards else None,
        "confidence_counts": quality["confidence_counts"],
        "gap_report": gap_report,
    }
    return response


@app.get("/api/v2/alerts/rules", response_model=list[AlertRuleResponse])
def list_alert_rules(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conn = _db_connect()
    if not conn:
        return []
    rows = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, description, rule_type, ward_code, metric, operator,
                       threshold_value, threshold_number, filter_json, trigger_on,
                       window_months, is_active, muted_until, notify_emails,
                       created_at, updated_at
                FROM alert_rules
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [_alert_rule_row(row) for row in rows]


@app.get("/api/v2/alerts/events", response_model=AlertEventListResponse)
def list_alert_events(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conn = _db_connect()
    if not conn:
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "source": "none"}
    items = []
    total = 0
    try:
        with conn.cursor() as cursor:
            params = []
            where = ""
            if status:
                where = "WHERE e.status = %s"
                params.append(status)
            cursor.execute(
                f"""
                SELECT e.*, r.name AS rule_name, r.rule_type AS rule_type
                FROM alert_events e
                LEFT JOIN alert_rules r ON e.alert_rule_id = r.id
                {where}
                ORDER BY e.triggered_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            for row in cursor.fetchall():
                items.append(_alert_event_row(row))
            cursor.execute(
                f"SELECT COUNT(*) AS count FROM alert_events e {where}",
                params,
            )
            row = cursor.fetchone()
            if row:
                total = _row_to_dict(row).get("count") or 0
    finally:
        conn.close()
    return {"items": items, "total": total, "limit": limit, "offset": offset, "source": "db"}


@app.post("/api/v2/alerts/rules", response_model=AlertRuleResponse)
def create_alert_rule(payload: AlertRuleCreate):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
    data = payload.model_dump()
    notify_emails = _normalize_emails(data.get("notify_emails"))
    muted_until = _parse_iso_datetime(data.get("muted_until"))
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alert_rules (
                    name, description, rule_type, ward_code, metric, operator,
                    threshold_value, threshold_number, filter_json, trigger_on,
                    window_months, is_active, muted_until, notify_emails
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    data.get("name"),
                    data.get("description"),
                    data.get("rule_type"),
                    data.get("ward_code"),
                    data.get("metric"),
                    data.get("operator"),
                    data.get("threshold_value"),
                    data.get("threshold_number"),
                    json.dumps(data.get("filter_json")) if data.get("filter_json") else None,
                    data.get("trigger_on") or "enter",
                    data.get("window_months"),
                    data.get("is_active", True),
                    muted_until,
                    notify_emails,
                ),
            )
            row = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
    return _alert_rule_row(row)


@app.put("/api/v2/alerts/rules/{rule_id}", response_model=AlertRuleResponse)
def update_alert_rule(rule_id: int, payload: AlertRuleUpdate):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="no_fields_to_update")
    if "notify_emails" in data:
        data["notify_emails"] = _normalize_emails(data.get("notify_emails"))
    if "muted_until" in data:
        data["muted_until"] = _parse_iso_datetime(data.get("muted_until"))
    if "filter_json" in data:
        data["filter_json"] = (
            json.dumps(data.get("filter_json")) if data.get("filter_json") else None
        )
    data["id"] = rule_id
    fields = []
    for key in data:
        if key == "id":
            continue
        fields.append(sql.SQL("{} = {}").format(sql.Identifier(key), sql.Placeholder(key)))
    try:
        with conn.cursor() as cursor:
            query = sql.SQL(
                "UPDATE alert_rules SET {fields}, updated_at = NOW() "
                "WHERE id = %(id)s RETURNING *"
            ).format(fields=sql.SQL(", ").join(fields))
            cursor.execute(query, data)
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="alert_rule_not_found")
        conn.commit()
    finally:
        conn.close()
    return _alert_rule_row(row)


@app.delete("/api/v2/alerts/rules/{rule_id}")
def delete_alert_rule(rule_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM alert_rules WHERE id = %s RETURNING id", (rule_id,)
            )
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="alert_rule_not_found")
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted"}


@app.post("/api/v2/alerts/events/{event_id}/acknowledge")
def acknowledge_alert_event(event_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
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
            raise HTTPException(status_code=404, detail="alert_event_not_found")
        conn.commit()
    finally:
        conn.close()
    return {"status": "acknowledged"}


@app.post("/api/v2/alerts/rules/{rule_id}/mute")
def mute_alert_rule(rule_id: int, payload: AlertMuteRequest):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
    until = None
    if payload.until:
        until = _parse_iso_datetime(payload.until)
    elif payload.hours:
        until = datetime.now(timezone.utc) + timedelta(hours=payload.hours)
    if not until:
        raise HTTPException(status_code=400, detail="mute_until_required")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE alert_rules
                SET muted_until = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id
                """,
                (until, rule_id),
            )
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="alert_rule_not_found")
        conn.commit()
    finally:
        conn.close()
    return {"status": "muted"}


@app.post("/api/v2/alerts/rules/{rule_id}/unmute")
def unmute_alert_rule(rule_id: int):
    conn = _db_connect()
    if not conn:
        raise HTTPException(status_code=503, detail="alerts_not_configured")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE alert_rules
                SET muted_until = NULL, updated_at = NOW()
                WHERE id = %s
                RETURNING id
                """,
                (rule_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="alert_rule_not_found")
        conn.commit()
    finally:
        conn.close()
    return {"status": "unmuted"}
