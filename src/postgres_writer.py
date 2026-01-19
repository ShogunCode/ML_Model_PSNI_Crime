import datetime as dt
import os
import sys
import urllib.request

import pandas as pd


def _parse_month(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m").date().replace(day=1)
    except ValueError:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date().replace(day=1)


def _coerce_int(value):
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value):
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_text(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text else None


def _coerce_text_keep_empty(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _coerce_bool(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "t"):
        return True
    if text in ("false", "0", "no", "n", "f"):
        return False
    return None


def _chunked_iter(df, batch_size):
    for start in range(0, len(df), batch_size):
        yield df.iloc[start : start + batch_size]


def _require_columns(df, required, label):
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def _normalize_url(url):
    if not url:
        return url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_url(url, host, port, user, password, database):
    if url:
        return _normalize_url(url)
    host = host or os.getenv("POSTGRES_HOST", "localhost")
    port = port or int(os.getenv("POSTGRES_PORT", "5432"))
    user = user or os.getenv("POSTGRES_USER", "postgres")
    password = password if password is not None else os.getenv("POSTGRES_PASSWORD", "")
    database = database or os.getenv("POSTGRES_DB", "crimemap")
    return {
        "host": host,
        "port": port,
        "dbname": database,
        "user": user,
        "password": password,
    }


def connect_postgres(url=None, host=None, port=None, user=None, password=None, database=None):
    try:
        import psycopg
    except ImportError:
        print("psycopg is required. Install it with pip.", file=sys.stderr)
        raise

    conninfo = _database_url(url, host, port, user, password, database)
    if isinstance(conninfo, str):
        return psycopg.connect(_normalize_url(conninfo))
    return psycopg.connect(**conninfo)


def _coverage_from_series(series):
    if series is None:
        return None, None
    months = series.apply(_parse_month).dropna()
    if months.empty:
        return None, None
    return months.min(), months.max()


def _determine_coverage(ward_analysis_df=None, crime_df=None, gap_df=None):
    if ward_analysis_df is not None:
        start, end = _coverage_from_series(ward_analysis_df.get("FirstMonth"))
        _, last = _coverage_from_series(ward_analysis_df.get("LastMonth"))
        if start or last:
            return start or last, last or start

    if crime_df is not None and "Month" in crime_df.columns:
        start, end = _coverage_from_series(crime_df["Month"])
        if start or end:
            return start, end

    if gap_df is not None:
        history_latest = _coverage_from_series(gap_df.get("HistoryLatest"))[1]
        latest_available = _coverage_from_series(gap_df.get("LatestAvailable"))[1]
        default_start = _coverage_from_series(gap_df.get("DefaultStartMonth"))[0]
        coverage_end = latest_available or history_latest
        coverage_start = default_start
        if coverage_start or coverage_end:
            return coverage_start, coverage_end

    return None, None


def _normalize_dataset_version(dataset_version, coverage_end):
    if dataset_version:
        return dataset_version
    env_version = os.getenv("CRIMEMAP_DATASET_VERSION")
    if env_version:
        return env_version
    if coverage_end:
        return coverage_end.strftime("%Y-%m")
    return dt.date.today().strftime("%Y-%m")


def insert_job_run(cursor, dataset_version, coverage_start, coverage_end, source, started_at):
    sql = (
        "INSERT INTO job_runs "
        "(dataset_version, coverage_start, coverage_end, status, source, started_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id"
    )
    cursor.execute(
        sql,
        (
            dataset_version,
            coverage_start,
            coverage_end,
            "running",
            source,
            started_at,
        ),
    )
    return cursor.fetchone()[0]


def finalize_job_run(cursor, job_id, status, finished_at, rows_loaded, notes=None):
    sql = (
        "UPDATE job_runs SET status=%s, finished_at=%s, rows_loaded=%s, notes=%s "
        "WHERE id=%s"
    )
    cursor.execute(sql, (status, finished_at, rows_loaded, notes, job_id))


def upsert_wards(cursor, df, batch_size):
    required = ["WardCode", "WARDNAME"]
    _require_columns(df, required, "wards")

    df = df.copy()
    df = df.dropna(subset=["WardCode"])
    df["WardCode"] = df["WardCode"].astype(str).str.strip()
    df = df[df["WardCode"] != ""]

    population = df["Population"] if "Population" in df.columns else None
    if population is None:
        population = pd.Series([None] * len(df), index=df.index)

    sql = (
        "INSERT INTO wards (ward_code, ward_name, population, updated_at) "
        "VALUES (%s, %s, %s, NOW()) "
        "ON CONFLICT (ward_code) DO UPDATE SET "
        "ward_name=EXCLUDED.ward_name, "
        "population=EXCLUDED.population, "
        "updated_at=NOW()"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        pop_values = [_coerce_int(value) for value in population.loc[chunk.index].tolist()]
        rows = list(
            zip(
                chunk["WardCode"].tolist(),
                chunk["WARDNAME"].apply(_coerce_text).tolist(),
                pop_values,
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_crimes(cursor, df, dataset_version, batch_size):
    required = ["Month", "Longitude", "Latitude", "Location", "Crime type", "WardCode", "WARDNAME"]
    _require_columns(df, required, "crimes")

    df = df.copy()
    df = df.dropna(subset=["WardCode", "Crime type", "Month"])
    df["WardCode"] = df["WardCode"].astype(str).str.strip()
    df = df[df["WardCode"] != ""]
    df["Crime type"] = df["Crime type"].astype(str).str.strip()
    df = df[df["Crime type"] != ""]
    df["Month"] = df["Month"].apply(_parse_month)
    df = df.dropna(subset=["Month", "Longitude", "Latitude"])

    sql = (
        "INSERT INTO crimes "
        "(dataset_version, month, longitude, latitude, location, crime_type, ward_code, "
        "ward_name, geom) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
        "CASE WHEN %s IS NOT NULL AND %s IS NOT NULL "
        "THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END) "
        "ON CONFLICT (dataset_version, month, longitude, latitude, location, crime_type, ward_code) "
        "DO UPDATE SET ward_name=EXCLUDED.ward_name"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = []
        for _, row in chunk.iterrows():
            lon = _coerce_float(row.get("Longitude"))
            lat = _coerce_float(row.get("Latitude"))
            rows.append(
                (
                    dataset_version,
                    _parse_month(row.get("Month")),
                    lon,
                    lat,
                    _coerce_text_keep_empty(row.get("Location")),
                    _coerce_text_keep_empty(row.get("Crime type")),
                    _coerce_text(row.get("WardCode")),
                    _coerce_text(row.get("WARDNAME")),
                    lon,
                    lat,
                    lon,
                    lat,
                )
            )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_ward_metrics(cursor, df, dataset_version, coverage_start, coverage_end, batch_size):
    required = ["WardCode", "WARDNAME"]
    _require_columns(df, required, "ward_metrics")

    df = df.copy()
    df = df.dropna(subset=["WardCode"])
    df["WardCode"] = df["WardCode"].astype(str).str.strip()
    df = df[df["WardCode"] != ""]

    def column(name, default=None):
        if name in df.columns:
            return df[name]
        return pd.Series([default] * len(df), index=df.index)

    first_month = column("FirstMonth").apply(_parse_month)
    last_month = column("LastMonth").apply(_parse_month)
    annualized = column("AnnualizedCrimeRatePer100k")
    if "CrimeRatePer100kPeople" in df.columns:
        crime_rate = df["CrimeRatePer100kPeople"]
    else:
        crime_rate = annualized

    sql = (
        "INSERT INTO ward_metrics ("
        "dataset_version, coverage_start, coverage_end, ward_code, ward_name, population, "
        "number_of_crimes, crime_rate_per_100k, rate_percentile, rate_rank, high_crime_rate, "
        "total_crimes, avg_monthly, trend_change, trend_pct, trend_slope, yoy_current, "
        "yoy_prior, yoy_change, total_harm, harm_score_per_100k, months, first_month, "
        "last_month, rating_score, rating_band, trend_percentile, "
        "annualized_crime_rate_per_100k) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (dataset_version, coverage_end, ward_code) DO UPDATE SET "
        "coverage_start=EXCLUDED.coverage_start, "
        "ward_name=EXCLUDED.ward_name, population=EXCLUDED.population, "
        "number_of_crimes=EXCLUDED.number_of_crimes, "
        "crime_rate_per_100k=EXCLUDED.crime_rate_per_100k, "
        "rate_percentile=EXCLUDED.rate_percentile, rate_rank=EXCLUDED.rate_rank, "
        "high_crime_rate=EXCLUDED.high_crime_rate, total_crimes=EXCLUDED.total_crimes, "
        "avg_monthly=EXCLUDED.avg_monthly, trend_change=EXCLUDED.trend_change, "
        "trend_pct=EXCLUDED.trend_pct, trend_slope=EXCLUDED.trend_slope, "
        "yoy_current=EXCLUDED.yoy_current, yoy_prior=EXCLUDED.yoy_prior, "
        "yoy_change=EXCLUDED.yoy_change, total_harm=EXCLUDED.total_harm, "
        "harm_score_per_100k=EXCLUDED.harm_score_per_100k, months=EXCLUDED.months, "
        "first_month=EXCLUDED.first_month, last_month=EXCLUDED.last_month, "
        "rating_score=EXCLUDED.rating_score, rating_band=EXCLUDED.rating_band, "
        "trend_percentile=EXCLUDED.trend_percentile, "
        "annualized_crime_rate_per_100k=EXCLUDED.annualized_crime_rate_per_100k, "
        "updated_at=NOW()"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = []
        for idx, row in chunk.iterrows():
            rows.append(
                (
                    dataset_version,
                    coverage_start,
                    coverage_end,
                    _coerce_text(row.get("WardCode")),
                    _coerce_text(row.get("WARDNAME")),
                    _coerce_int(row.get("Population")),
                    _coerce_int(row.get("NumberOfCrimes")),
                    _coerce_float(crime_rate.loc[idx]),
                    _coerce_float(row.get("RatePercentile")),
                    _coerce_int(row.get("RateRank")),
                    _coerce_bool(row.get("HighCrimeRate")),
                    _coerce_int(row.get("TotalCrimes")),
                    _coerce_float(row.get("AvgMonthly")),
                    _coerce_float(row.get("TrendChange")),
                    _coerce_float(row.get("TrendPct")),
                    _coerce_float(row.get("TrendSlope")),
                    _coerce_int(row.get("YoYCurrent")),
                    _coerce_int(row.get("YoYPrior")),
                    _coerce_float(row.get("YoYChange")),
                    _coerce_float(row.get("TotalHarm")),
                    _coerce_float(row.get("HarmScorePer100k")),
                    _coerce_int(row.get("Months")),
                    first_month.loc[idx] if idx in first_month.index else None,
                    last_month.loc[idx] if idx in last_month.index else None,
                    _coerce_float(row.get("RatingScore")),
                    _coerce_text(row.get("RatingBand")),
                    _coerce_float(row.get("TrendPercentile")),
                    _coerce_float(annualized.loc[idx]),
                )
            )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_ward_type_metrics(
    cursor, df, dataset_version, coverage_start, coverage_end, batch_size
):
    required = ["WardCode", "WARDNAME", "Crime type"]
    _require_columns(df, required, "ward_type_metrics")

    df = df.copy()
    df = df.dropna(subset=["WardCode", "Crime type"])
    df["WardCode"] = df["WardCode"].astype(str).str.strip()
    df = df[df["WardCode"] != ""]

    sql = (
        "INSERT INTO ward_type_metrics ("
        "dataset_version, coverage_start, coverage_end, ward_code, ward_name, crime_type, "
        "total_crimes, avg_monthly, trend_change, trend_pct, trend_slope, months, "
        "first_month, last_month, trend_direction) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (dataset_version, coverage_end, ward_code, crime_type) DO UPDATE SET "
        "coverage_start=EXCLUDED.coverage_start, "
        "ward_name=EXCLUDED.ward_name, total_crimes=EXCLUDED.total_crimes, "
        "avg_monthly=EXCLUDED.avg_monthly, trend_change=EXCLUDED.trend_change, "
        "trend_pct=EXCLUDED.trend_pct, trend_slope=EXCLUDED.trend_slope, "
        "months=EXCLUDED.months, first_month=EXCLUDED.first_month, "
        "last_month=EXCLUDED.last_month, trend_direction=EXCLUDED.trend_direction, "
        "updated_at=NOW()"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = []
        for _, row in chunk.iterrows():
            rows.append(
                (
                    dataset_version,
                    coverage_start,
                    coverage_end,
                    _coerce_text(row.get("WardCode")),
                    _coerce_text(row.get("WARDNAME")),
                    _coerce_text(row.get("Crime type")),
                    _coerce_int(row.get("TotalCrimes")),
                    _coerce_float(row.get("AvgMonthly")),
                    _coerce_float(row.get("TrendChange")),
                    _coerce_float(row.get("TrendPct")),
                    _coerce_float(row.get("TrendSlope")),
                    _coerce_int(row.get("Months")),
                    _parse_month(row.get("FirstMonth")),
                    _parse_month(row.get("LastMonth")),
                    _coerce_text(row.get("TrendDirection")),
                )
            )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def upsert_ward_officials(cursor, df, batch_size):
    required = ["ward_code", "official_name"]
    _require_columns(df, required, "ward_officials")

    df = df.copy()
    df = df.dropna(subset=["ward_code", "official_name"])
    df["ward_code"] = df["ward_code"].astype(str).str.strip()
    df = df[df["ward_code"] != ""]

    def column(name):
        if name in df.columns:
            return df[name]
        return pd.Series([None] * len(df), index=df.index)

    sql = (
        "INSERT INTO ward_officials "
        "(ward_code, official_name, role, party, email, phone, source, source_id, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()) "
        "ON CONFLICT (ward_code, official_name, role, party, email) DO UPDATE SET "
        "phone=EXCLUDED.phone, source=EXCLUDED.source, source_id=EXCLUDED.source_id, "
        "updated_at=NOW()"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = list(
            zip(
                chunk["ward_code"].apply(_coerce_text).tolist(),
                chunk["official_name"].apply(_coerce_text).tolist(),
                column("role").apply(_coerce_text).tolist(),
                column("party").apply(_coerce_text).tolist(),
                column("email").apply(_coerce_text_keep_empty).tolist(),
                column("phone").apply(_coerce_text).tolist(),
                column("source").apply(_coerce_text).tolist(),
                column("source_id").apply(_coerce_text).tolist(),
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_gap_report(cursor, df, dataset_version, coverage_end, batch_size):
    required = ["CheckedAt", "HistoryLatest", "LatestAvailable", "GapMonths", "DefaultStartMonth"]
    _require_columns(df, required, "gap_report")

    df = df.copy()
    df["CheckedAt"] = pd.to_datetime(df["CheckedAt"], errors="coerce")

    sql = (
        "INSERT INTO gap_report "
        "(dataset_version, coverage_end, checked_at, history_latest, latest_available, "
        "gap_months, default_start_month) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (dataset_version, coverage_end) DO UPDATE SET "
        "checked_at=EXCLUDED.checked_at, history_latest=EXCLUDED.history_latest, "
        "latest_available=EXCLUDED.latest_available, gap_months=EXCLUDED.gap_months, "
        "default_start_month=EXCLUDED.default_start_month"
    )

    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = []
        for _, row in chunk.iterrows():
            rows.append(
                (
                    dataset_version,
                    coverage_end,
                    row.get("CheckedAt"),
                    _parse_month(row.get("HistoryLatest")),
                    _parse_month(row.get("LatestAvailable")),
                    _coerce_int(row.get("GapMonths")),
                    _parse_month(row.get("DefaultStartMonth")),
                )
            )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def load_from_csvs(
    url=None,
    host=None,
    port=None,
    user=None,
    password=None,
    database=None,
    crime_events_path=None,
    ward_analysis_path=None,
    ward_trends_path=None,
    ward_officials_path=None,
    gap_report_path=None,
    batch_size=2000,
    skip_events=False,
    skip_ward_analysis=False,
    skip_ward_trends=False,
    skip_ward_officials=False,
    skip_gap=False,
    dataset_version=None,
    coverage_start=None,
    coverage_end=None,
    source="csv_load",
):
    ward_df = None
    crime_df = None
    gap_df = None
    trends_df = None
    officials_df = None

    if not skip_events and crime_events_path and os.path.exists(crime_events_path):
        crime_df = pd.read_csv(crime_events_path)
    if not skip_ward_analysis and ward_analysis_path and os.path.exists(ward_analysis_path):
        ward_df = pd.read_csv(ward_analysis_path)
    if not skip_ward_trends and ward_trends_path and os.path.exists(ward_trends_path):
        trends_df = pd.read_csv(ward_trends_path)
    if not skip_ward_officials and ward_officials_path and os.path.exists(ward_officials_path):
        officials_df = pd.read_csv(ward_officials_path)
    if not skip_gap and gap_report_path and os.path.exists(gap_report_path):
        gap_df = pd.read_csv(gap_report_path)

    if coverage_start:
        coverage_start = _parse_month(coverage_start)
    if coverage_end:
        coverage_end = _parse_month(coverage_end)

    if not coverage_start or not coverage_end:
        derived_start, derived_end = _determine_coverage(ward_df, crime_df, gap_df)
        coverage_start = coverage_start or derived_start
        coverage_end = coverage_end or derived_end

    if coverage_end is None:
        coverage_end = dt.date.today().replace(day=1)

    dataset_version = _normalize_dataset_version(dataset_version, coverage_end)

    conn = connect_postgres(
        url=url,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    cursor = conn.cursor()

    started_at = dt.datetime.now(dt.timezone.utc)
    job_id = insert_job_run(
        cursor,
        dataset_version=dataset_version,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        source=source,
        started_at=started_at,
    )
    conn.commit()

    total_rows = 0
    try:
        if ward_df is not None:
            total_rows += upsert_wards(cursor, ward_df, batch_size)
            conn.commit()
            total_rows += insert_ward_metrics(
                cursor,
                ward_df,
                dataset_version,
                coverage_start,
                coverage_end,
                batch_size,
            )
            conn.commit()

        if crime_df is not None:
            total_rows += insert_crimes(cursor, crime_df, dataset_version, batch_size)
            conn.commit()

        if trends_df is not None:
            total_rows += insert_ward_type_metrics(
                cursor,
                trends_df,
                dataset_version,
                coverage_start,
                coverage_end,
                batch_size,
            )
            conn.commit()

        if officials_df is not None:
            total_rows += upsert_ward_officials(cursor, officials_df, batch_size)
            conn.commit()

        if gap_df is not None and coverage_end is not None:
            total_rows += insert_gap_report(
                cursor,
                gap_df,
                dataset_version,
                coverage_end,
                batch_size,
            )
            conn.commit()

        finished_at = dt.datetime.now(dt.timezone.utc)
        finalize_job_run(cursor, job_id, "completed", finished_at, total_rows)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        finished_at = dt.datetime.now(dt.timezone.utc)
        finalize_job_run(cursor, job_id, "failed", finished_at, total_rows, str(exc))
        conn.commit()
        cursor.close()
        conn.close()
        raise

    cursor.close()
    conn.close()

    _trigger_alert_evaluator()
    return total_rows


def _trigger_alert_evaluator():
    if os.getenv("CRIMEMAP_ALERTS_EVAL", "true").lower() == "false":
        return
    base_url = os.getenv("CRIMEMAP_ALERTS_URL") or os.getenv("CRIMEMAP_API_URL")
    if not base_url:
        return
    endpoint = base_url.rstrip("/") + "/ops/alerts/evaluate"
    req = urllib.request.Request(endpoint, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
    except Exception:
        return
