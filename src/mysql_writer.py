import datetime as dt
import os
import sys

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


def _load_schema(cursor, schema_path):
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        cursor.execute(stmt)


def _chunked_iter(df, batch_size):
    for start in range(0, len(df), batch_size):
        yield df.iloc[start : start + batch_size]


def _require_columns(df, required, label):
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def connect_mysql(host, port, user, password, database):
    try:
        import mysql.connector
    except ImportError:
        print("mysql-connector-python is required. Install it with pip.", file=sys.stderr)
        raise

    config = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }
    return mysql.connector.connect(**config)


def insert_crime_events(cursor, df, batch_size):
    required = ["Month", "Longitude", "Latitude", "Location", "Crime type", "WardCode", "WARDNAME"]
    _require_columns(df, required, "crime_events")

    df = df.copy()
    df["Month"] = df["Month"].apply(_parse_month)
    df["Crime type"] = df["Crime type"].astype(str)
    df["Location"] = df["Location"].astype(str)
    df["WardCode"] = df["WardCode"].astype(str)
    df["WARDNAME"] = df["WARDNAME"].astype(str)

    sql = (
        "INSERT INTO crime_events "
        "(month, longitude, latitude, location, crime_type, ward_code, ward_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE ward_name=VALUES(ward_name)"
    )
    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = list(
            zip(
                chunk["Month"].tolist(),
                chunk["Longitude"].tolist(),
                chunk["Latitude"].tolist(),
                chunk["Location"].tolist(),
                chunk["Crime type"].tolist(),
                chunk["WardCode"].tolist(),
                chunk["WARDNAME"].tolist(),
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_ward_analysis(cursor, df, batch_size):
    required = [
        "WardCode",
        "WARDNAME",
        "Population",
        "NumberOfCrimes",
        "CrimeRatePer100kPeople",
        "RatePercentile",
        "RateRank",
        "HighCrimeRate",
        "TotalCrimes",
        "AvgMonthly",
        "TrendChange",
        "TrendPct",
        "TrendSlope",
        "YoYCurrent",
        "YoYPrior",
        "YoYChange",
        "TotalHarm",
        "HarmScorePer100k",
        "Months",
        "FirstMonth",
        "LastMonth",
    ]
    _require_columns(df, required, "ward_analysis")

    df = df.copy()
    df["WardCode"] = df["WardCode"].astype(str)
    df["WARDNAME"] = df["WARDNAME"].astype(str)

    sql = (
        "INSERT INTO ward_analysis "
        "(ward_code, ward_name, population, number_of_crimes, crime_rate_per_100k, "
        "rate_percentile, rate_rank, high_crime_rate, total_crimes, avg_monthly, "
        "trend_change, trend_pct, trend_slope, yoy_current, yoy_prior, yoy_change, "
        "total_harm, harm_score_per_100k, months, first_month, last_month) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "ward_name=VALUES(ward_name), population=VALUES(population), "
        "number_of_crimes=VALUES(number_of_crimes), "
        "crime_rate_per_100k=VALUES(crime_rate_per_100k), "
        "rate_percentile=VALUES(rate_percentile), rate_rank=VALUES(rate_rank), "
        "high_crime_rate=VALUES(high_crime_rate), total_crimes=VALUES(total_crimes), "
        "avg_monthly=VALUES(avg_monthly), trend_change=VALUES(trend_change), "
        "trend_pct=VALUES(trend_pct), trend_slope=VALUES(trend_slope), "
        "yoy_current=VALUES(yoy_current), yoy_prior=VALUES(yoy_prior), "
        "yoy_change=VALUES(yoy_change), total_harm=VALUES(total_harm), "
        "harm_score_per_100k=VALUES(harm_score_per_100k), months=VALUES(months), "
        "first_month=VALUES(first_month), last_month=VALUES(last_month)"
    )
    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = list(
            zip(
                chunk["WardCode"].tolist(),
                chunk["WARDNAME"].tolist(),
                chunk["Population"].tolist(),
                chunk["NumberOfCrimes"].tolist(),
                chunk["CrimeRatePer100kPeople"].tolist(),
                chunk["RatePercentile"].tolist(),
                chunk["RateRank"].tolist(),
                chunk["HighCrimeRate"].tolist(),
                chunk["TotalCrimes"].tolist(),
                chunk["AvgMonthly"].tolist(),
                chunk["TrendChange"].tolist(),
                chunk["TrendPct"].tolist(),
                chunk["TrendSlope"].tolist(),
                chunk["YoYCurrent"].tolist(),
                chunk["YoYPrior"].tolist(),
                chunk["YoYChange"].tolist(),
                chunk["TotalHarm"].tolist(),
                chunk["HarmScorePer100k"].tolist(),
                chunk["Months"].tolist(),
                chunk["FirstMonth"].tolist(),
                chunk["LastMonth"].tolist(),
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_ward_crime_type_trends(cursor, df, batch_size):
    required = [
        "WardCode",
        "WARDNAME",
        "Crime type",
        "TotalCrimes",
        "AvgMonthly",
        "TrendChange",
        "TrendPct",
        "TrendSlope",
        "Months",
        "FirstMonth",
        "LastMonth",
        "TrendDirection",
    ]
    _require_columns(df, required, "ward_crime_type_trends")

    df = df.copy()
    df["WardCode"] = df["WardCode"].astype(str)
    df["WARDNAME"] = df["WARDNAME"].astype(str)
    df["Crime type"] = df["Crime type"].astype(str)

    sql = (
        "INSERT INTO ward_crime_type_trends "
        "(ward_code, ward_name, crime_type, total_crimes, avg_monthly, trend_change, "
        "trend_pct, trend_slope, months, first_month, last_month, trend_direction) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "ward_name=VALUES(ward_name), total_crimes=VALUES(total_crimes), "
        "avg_monthly=VALUES(avg_monthly), trend_change=VALUES(trend_change), "
        "trend_pct=VALUES(trend_pct), trend_slope=VALUES(trend_slope), "
        "months=VALUES(months), first_month=VALUES(first_month), "
        "last_month=VALUES(last_month), trend_direction=VALUES(trend_direction)"
    )
    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = list(
            zip(
                chunk["WardCode"].tolist(),
                chunk["WARDNAME"].tolist(),
                chunk["Crime type"].tolist(),
                chunk["TotalCrimes"].tolist(),
                chunk["AvgMonthly"].tolist(),
                chunk["TrendChange"].tolist(),
                chunk["TrendPct"].tolist(),
                chunk["TrendSlope"].tolist(),
                chunk["Months"].tolist(),
                chunk["FirstMonth"].tolist(),
                chunk["LastMonth"].tolist(),
                chunk["TrendDirection"].tolist(),
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_gap_report(cursor, df, batch_size):
    required = ["CheckedAt", "HistoryLatest", "LatestAvailable", "GapMonths", "DefaultStartMonth"]
    _require_columns(df, required, "police_api_gap_report")

    df = df.copy()
    df["CheckedAt"] = pd.to_datetime(df["CheckedAt"], errors="coerce")

    sql = (
        "INSERT INTO police_api_gap_report "
        "(checked_at, history_latest, latest_available, gap_months, default_start_month) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    total = 0
    for chunk in _chunked_iter(df, batch_size):
        rows = list(
            zip(
                chunk["CheckedAt"].tolist(),
                chunk["HistoryLatest"].tolist(),
                chunk["LatestAvailable"].tolist(),
                chunk["GapMonths"].tolist(),
                chunk["DefaultStartMonth"].tolist(),
            )
        )
        cursor.executemany(sql, rows)
        total += len(rows)
    return total


def insert_log(cursor, source, started_at, finished_at, rows_loaded, notes=None):
    sql = (
        "INSERT INTO ingest_log (source, started_at, finished_at, rows_loaded, notes) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    cursor.execute(sql, (source, started_at, finished_at, rows_loaded, notes))


def load_from_csvs(
    host,
    port,
    user,
    password,
    database,
    schema_path,
    crime_events_path,
    ward_analysis_path,
    ward_trends_path,
    gap_report_path,
    batch_size=2000,
    skip_events=False,
    skip_ward_analysis=False,
    skip_ward_trends=False,
    skip_gap=False,
    source="csv_load",
):
    conn = connect_mysql(host, port, user, password, database)
    cursor = conn.cursor()
    _load_schema(cursor, schema_path)
    conn.commit()

    started_at = dt.datetime.now()
    total_rows = 0

    if not skip_events and crime_events_path and os.path.exists(crime_events_path):
        df = pd.read_csv(crime_events_path)
        total_rows += insert_crime_events(cursor, df, batch_size)
        conn.commit()

    if not skip_ward_analysis and ward_analysis_path and os.path.exists(ward_analysis_path):
        df = pd.read_csv(ward_analysis_path)
        total_rows += insert_ward_analysis(cursor, df, batch_size)
        conn.commit()

    if not skip_ward_trends and ward_trends_path and os.path.exists(ward_trends_path):
        df = pd.read_csv(ward_trends_path)
        total_rows += insert_ward_crime_type_trends(cursor, df, batch_size)
        conn.commit()

    if not skip_gap and gap_report_path and os.path.exists(gap_report_path):
        df = pd.read_csv(gap_report_path)
        total_rows += insert_gap_report(cursor, df, batch_size)
        conn.commit()

    finished_at = dt.datetime.now()
    insert_log(cursor, source, started_at, finished_at, total_rows)
    conn.commit()

    cursor.close()
    conn.close()
    return total_rows
