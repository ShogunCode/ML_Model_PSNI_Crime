import argparse
import datetime as dt
import io
import os
import urllib.request
import urllib.error
import http.client
import time
import zipfile

import pandas as pd


DEFAULT_ARCHIVE_URL = "https://data.police.uk/data/archive/{date}.zip"
DEFAULT_OUTPUT_PATH = os.path.join("data", "raw", "crime_data.csv")
DEFAULT_HISTORY_PATH = os.path.join("data", "processed", "crime_history.csv")
FULL_COLUMNS = [
    "Crime ID",
    "Month",
    "Reported by",
    "Falls within",
    "Longitude",
    "Latitude",
    "Location",
    "LSOA code",
    "LSOA name",
    "Crime type",
    "Last outcome category",
    "Context",
]


def _latest_available_month(today=None):
    if today is None:
        today = dt.date.today()
    first_of_month = today.replace(day=1)
    last_month = first_of_month - dt.timedelta(days=1)
    return last_month.strftime("%Y-%m")

def _parse_month(value):
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError:
        return None


def _format_month(value):
    if value is None:
        return None
    return value.strftime("%Y-%m")


def _month_diff(start, end):
    if start is None or end is None:
        return None
    return (end.year - start.year) * 12 + (end.month - start.month)


def _next_month(value):
    if value is None:
        return None
    year = value.year + (value.month // 12)
    month = 1 if value.month == 12 else value.month + 1
    return dt.date(year, month, 1)

def _prev_month(value):
    if value is None:
        return None
    if value.month == 1:
        return dt.date(value.year - 1, 12, 1)
    return dt.date(value.year, value.month - 1, 1)


def _month_range(start, end):
    current = start
    while current and end and current <= end:
        yield current
        current = _next_month(current)


def _download_file(url, dest_path, attempts=3, chunk_size=1024 * 1024):
    last_error = None
    for attempt in range(1, attempts + 1):
        last_error = None
        try:
            with urllib.request.urlopen(url) as response, open(dest_path, "wb") as f:
                while True:
                    try:
                        chunk = response.read(chunk_size)
                    except http.client.IncompleteRead as exc:
                        last_error = exc
                        break
                    if not chunk:
                        return
                    f.write(chunk)
            if last_error is None:
                return
        except (urllib.error.URLError, http.client.IncompleteRead, OSError) as exc:
            last_error = exc

        if os.path.exists(dest_path):
            os.remove(dest_path)
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 10))

    if last_error:
        raise last_error


def _find_psni_street_member(zip_file):
    candidates = []
    for name in zip_file.namelist():
        lower = name.lower()
        if not lower.endswith(".csv"):
            continue
        if "psni" in lower and "street" in lower:
            candidates.append(name)
    if not candidates:
        raise ValueError("No PSNI street-level CSV found in archive.")
    if len(candidates) > 1:
        candidates.sort()
    return candidates[0]


def _load_psni_csv_from_archive(archive_path):
    with zipfile.ZipFile(archive_path, "r") as zip_file:
        member_name = _find_psni_street_member(zip_file)
        with zip_file.open(member_name) as member:
            data = member.read()
    return pd.read_csv(io.BytesIO(data))

def _archive_exists(url):
    head = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(head) as response:
            return response.status in (200, 206)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        if exc.code != 405:
            raise
    except urllib.error.URLError:
        return False

    get = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(get) as response:
            response.read(1)
            return response.status in (200, 206)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    except urllib.error.URLError:
        return False


def _resolve_latest_available_month(archive_url, latest_month, max_probe_months=24):
    target = _parse_month(latest_month)
    if target is None:
        return latest_month
    current = target
    for _ in range(max_probe_months + 1):
        month_str = _format_month(current)
        url = archive_url.format(date=month_str)
        if _archive_exists(url):
            return month_str
        current = _prev_month(current)
        if current is None:
            break
    return latest_month

def _latest_month_in_df(df):
    if "Month" not in df.columns:
        return None
    months = pd.to_datetime(df["Month"], errors="coerce")
    if months.isna().all():
        return None
    latest = months.max()
    if pd.isna(latest):
        return None
    return latest.strftime("%Y-%m")


def _normalise_columns(df):
    expected = [
        "Month",
        "Longitude",
        "Latitude",
        "Location",
        "Crime type",
    ]
    missing = [name for name in expected if name not in df.columns]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing expected columns: {missing_list}")
    return df[expected].copy()


def _normalise_history_columns(df):
    for name in FULL_COLUMNS:
        if name not in df.columns:
            df[name] = ""
    return df[FULL_COLUMNS].copy()


def ingest_psni_month(date_str, output_path, archive_url, history_path=None):
    if not date_str:
        date_str = _latest_available_month()

    archive_dir = os.path.join("data", "raw")
    os.makedirs(archive_dir, exist_ok=True)
    df = None

    archive_url = archive_url.format(date=date_str)
    archive_path = os.path.join(archive_dir, f"psni-{date_str}.zip")
    _download_file(archive_url, archive_path)
    df = _load_psni_csv_from_archive(archive_path)
    os.remove(archive_path)

    history_df = _normalise_history_columns(df)
    df = _normalise_columns(df)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)
    if history_path:
        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        if os.path.exists(history_path):
            existing = pd.read_csv(history_path)
            combined = pd.concat([existing, history_df], ignore_index=True)
        else:
            combined = history_df

        if "Crime ID" in combined.columns:
            combined = combined.drop_duplicates(subset=["Crime ID"])
        else:
            combined = combined.drop_duplicates(
                subset=["Month", "Longitude", "Latitude", "Location", "Crime type"]
            )

        combined.to_csv(history_path, index=False)
    return output_path, len(df)

def get_latest_history_month(history_path):
    if not history_path or not os.path.exists(history_path):
        return None
    df = pd.read_csv(history_path)
    return _latest_month_in_df(df)

def get_latest_month_from_csv(csv_path):
    if not csv_path or not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path)
    return _latest_month_in_df(df)


def check_history_gap(history_path, latest_available=None):
    latest_available = latest_available or _latest_available_month()
    history_latest = get_latest_history_month(history_path)
    gap_months = None
    if history_latest:
        gap_months = _month_diff(
            _parse_month(history_latest),
            _parse_month(latest_available),
        )
    return {
        "history_latest": history_latest,
        "latest_available": latest_available,
        "gap_months": gap_months,
    }


def update_psni_history(
    output_path=DEFAULT_OUTPUT_PATH,
    history_path=DEFAULT_HISTORY_PATH,
    archive_url=DEFAULT_ARCHIVE_URL,
    start_month=None,
    end_month=None,
    probe_latest=True,
    max_probe_months=24,
):
    latest_available = end_month or _latest_available_month()
    if probe_latest:
        latest_available = _resolve_latest_available_month(
            archive_url,
            latest_available,
            max_probe_months=max_probe_months,
        )
    history_latest = get_latest_history_month(history_path)
    if history_latest is None:
        history_latest = get_latest_month_from_csv(output_path)

    start_date = _parse_month(start_month)
    if start_date is None:
        history_date = _parse_month(history_latest)
        if history_date is None:
            start_date = _parse_month(latest_available)
        else:
            start_date = _next_month(history_date)

    end_date = _parse_month(latest_available)
    if start_date is None or end_date is None or start_date > end_date:
        return {
            "months_added": 0,
            "history_latest_before": history_latest,
            "history_latest_after": history_latest,
            "latest_available": latest_available,
        }

    months = [_format_month(m) for m in _month_range(start_date, end_date)]
    if not months:
        return {
            "months_added": 0,
            "history_latest_before": history_latest,
            "history_latest_after": history_latest,
            "latest_available": latest_available,
        }

    archive_dir = os.path.join("data", "raw")
    os.makedirs(archive_dir, exist_ok=True)

    existing = None
    if history_path and os.path.exists(history_path):
        existing = pd.read_csv(history_path)

    history_chunks = []
    last_df = None
    last_successful = None
    missing_month = None
    missing_months = []
    for month in months:
        url = archive_url.format(date=month)
        archive_path = os.path.join(archive_dir, f"psni-{month}.zip")
        try:
            _download_file(url, archive_path)
            df = _load_psni_csv_from_archive(archive_path)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                if missing_month is None:
                    missing_month = month
                missing_months.append(month)
                continue
            raise
        except (ValueError, zipfile.BadZipFile, http.client.IncompleteRead, urllib.error.URLError, OSError):
            if missing_month is None:
                missing_month = month
            missing_months.append(month)
            continue
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)
        history_chunks.append(_normalise_history_columns(df))
        last_df = _normalise_columns(df)
        last_successful = month

    if history_path:
        if not history_chunks:
            return {
                "months_added": 0,
                "history_latest_before": history_latest,
                "history_latest_after": history_latest,
                "latest_available": latest_available,
                "missing_month": missing_month,
                "missing_months_count": len(missing_months),
            }
        if existing is None:
            combined = pd.concat(history_chunks, ignore_index=True)
        else:
            combined = pd.concat([existing] + history_chunks, ignore_index=True)

        if "Crime ID" in combined.columns:
            combined = combined.drop_duplicates(subset=["Crime ID"])
        else:
            combined = combined.drop_duplicates(
                subset=["Month", "Longitude", "Latitude", "Location", "Crime type"]
            )

        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        combined.to_csv(history_path, index=False)

    if last_df is not None and output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        last_df.to_csv(output_path, index=False)

    history_latest_after = last_successful or history_latest
    return {
        "months_added": len(history_chunks),
        "history_latest_before": history_latest,
        "history_latest_after": history_latest_after,
        "latest_available": latest_available,
        "missing_month": missing_month,
        "missing_months_count": len(missing_months),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Download the latest PSNI street-level crime data archive and write a CSV."
    )
    parser.add_argument(
        "--date",
        help="Month to download in YYYY-MM format. Defaults to latest available month.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output CSV path. Defaults to {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--archive-url",
        default=DEFAULT_ARCHIVE_URL,
        help="Archive URL template with {date} placeholder.",
    )
    parser.add_argument(
        "--history",
        default=DEFAULT_HISTORY_PATH,
        help=f"Historical CSV path. Defaults to {DEFAULT_HISTORY_PATH}",
    )
    parser.add_argument(
        "--update-history",
        action="store_true",
        help="Update history by downloading all missing months up to the latest archive.",
    )
    parser.add_argument(
        "--start-month",
        help="Start month in YYYY-MM format for history updates.",
    )
    parser.add_argument(
        "--end-month",
        help="End month in YYYY-MM format for history updates.",
    )
    parser.add_argument(
        "--probe-latest",
        action="store_true",
        help="Probe backwards to find the latest available archive month.",
    )
    parser.add_argument(
        "--max-probe-months",
        type=int,
        default=24,
        help="How many months back to probe for available archives.",
    )

    args = parser.parse_args()
    if args.update_history:
        info = update_psni_history(
            output_path=args.output,
            history_path=args.history,
            archive_url=args.archive_url,
            start_month=args.start_month,
            end_month=args.end_month,
            probe_latest=args.probe_latest,
            max_probe_months=args.max_probe_months,
        )
        print(
            "History update: added {months_added} month(s). "
            "Latest in history {history_latest_before} -> {history_latest_after} "
            "(latest available {latest_available}).".format(**info)
        )
    else:
        output_path, row_count = ingest_psni_month(
            args.date,
            args.output,
            args.archive_url,
            args.history,
        )
        print(f"Wrote {row_count} rows to {output_path}")


if __name__ == "__main__":
    main()
