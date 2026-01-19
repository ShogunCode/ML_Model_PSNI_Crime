import argparse
import datetime as dt
import json
import logging
import os
import time
import urllib.error
import urllib.request

import geopandas as gpd
import pandas as pd


DEFAULT_API_URL = "https://data.police.uk/api/crimes-at-location?date={date}&lat={lat}&lng={lng}"
DEFAULT_POLY_URL = "https://data.police.uk/api/crimes-street/all-crime?date={date}&poly={poly}"
DEFAULT_START_MONTH = "2023-08"
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

logger = logging.getLogger(__name__)


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


def _month_diff(start, end):
    if start is None or end is None:
        return None
    return (end.year - start.year) * 12 + (end.month - start.month)


def _get_latest_month_from_csv(path):
    if not path or not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return _latest_month_in_df(df)


def _load_ward_centroids(shapefile_path, max_wards=None):
    wards = gpd.read_file(shapefile_path)
    if "WardCode" in wards.columns and "WardCode_w" not in wards.columns:
        wards = wards.rename(columns={"WardCode": "WardCode_w"})

    if "WardCode_w" in wards.columns and "WARDNAME" in wards.columns:
        wards = wards.drop_duplicates(subset=["WardCode_w", "WARDNAME"])

    if wards.crs is None:
        wards = wards.set_crs(epsg=4326)

    wards_proj = wards.to_crs(epsg=29902)
    centroids = wards_proj.geometry.centroid
    centroids_geo = gpd.GeoSeries(centroids, crs=wards_proj.crs).to_crs(epsg=4326)

    wards = wards.copy()
    wards["Latitude"] = centroids_geo.y
    wards["Longitude"] = centroids_geo.x

    cols = ["WardCode_w", "WARDNAME", "Latitude", "Longitude"]
    available = [col for col in cols if col in wards.columns]
    wards = wards[available].dropna(subset=["Latitude", "Longitude"])
    if max_wards:
        wards = wards.head(max_wards)
    return wards.reset_index(drop=True)


def _load_ward_polygons(shapefile_path, simplify_tolerance=None, max_wards=None):
    wards = gpd.read_file(shapefile_path)
    if "WardCode" in wards.columns and "WardCode_w" not in wards.columns:
        wards = wards.rename(columns={"WardCode": "WardCode_w"})

    if "WardCode_w" in wards.columns and "WARDNAME" in wards.columns:
        wards = wards.drop_duplicates(subset=["WardCode_w", "WARDNAME"])

    if wards.crs is None:
        wards = wards.set_crs(epsg=4326)

    if wards.crs.to_epsg() != 4326:
        wards = wards.to_crs(epsg=4326)

    if simplify_tolerance:
        wards["geometry"] = wards["geometry"].simplify(
            simplify_tolerance,
            preserve_topology=True,
        )

    cols = ["WardCode_w", "WARDNAME", "geometry"]
    available = [col for col in cols if col in wards.columns]
    wards = wards[available].dropna(subset=["geometry"])
    if max_wards:
        wards = wards.head(max_wards)
    return wards.reset_index(drop=True)


def _request_json(url, attempts=3, backoff=2, timeout=30):
    headers = {"User-Agent": "crime-map/1.0"}
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                last_error = exc
            elif exc.code in (400, 404):
                return {"error": f"http_{exc.code}"}
            else:
                raise
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(min(backoff ** attempt, 10))

    if last_error:
        raise last_error
    return {"error": "unknown"}


def _api_month_available(api_mode, api_url, poly_url, date_str, lat=None, lon=None, poly=None):
    if api_mode == "poly":
        if not poly:
            return False
        url = poly_url.format(date=date_str, poly=poly)
    else:
        url = api_url.format(date=date_str, lat=lat, lng=lon)
    try:
        payload = _request_json(url, attempts=2, backoff=2, timeout=20)
    except Exception:
        return False
    if isinstance(payload, dict) and payload.get("error"):
        return False
    return True


def _resolve_latest_api_month(
    api_mode,
    api_url,
    poly_url,
    latest_month,
    lat=None,
    lon=None,
    poly=None,
    max_probe_months=24,
):
    target = _parse_month(latest_month)
    if target is None:
        return latest_month
    current = target
    for _ in range(max_probe_months + 1):
        month_str = _format_month(current)
        if _api_month_available(
            api_mode,
            api_url,
            poly_url,
            month_str,
            lat=lat,
            lon=lon,
            poly=poly,
        ):
            return month_str
        current = _prev_month(current)
        if current is None:
            break
    return latest_month


def _geometry_to_poly(geometry, max_points=12):
    if geometry is None or geometry.is_empty:
        return None

    geom = geometry
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area, default=None)
        if geom is None:
            return None

    if geom.geom_type != "Polygon":
        return None

    coords = list(geom.exterior.coords)
    if len(coords) < 3:
        return None

    if coords[0] == coords[-1]:
        coords = coords[:-1]

    if max_points and len(coords) > max_points:
        step = len(coords) / float(max_points)
        coords = [coords[int(i * step)] for i in range(max_points)]

    points = [f"{lat:.6f},{lon:.6f}" for lon, lat in coords]
    return ":".join(points)


def _normalise_raw_rows(crimes, fallback_month=None):
    rows = []
    for crime in crimes:
        location = crime.get("location") or {}
        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is None or lon is None:
            continue
        street = location.get("street") or {}
        rows.append(
            {
                "Month": crime.get("month") or fallback_month,
                "Longitude": float(lon),
                "Latitude": float(lat),
                "Location": street.get("name", ""),
                "Crime type": crime.get("category", ""),
            }
        )
    return rows


def _normalise_history_rows(crimes, fallback_month=None):
    rows = []
    for crime in crimes:
        location = crime.get("location") or {}
        lat = location.get("latitude")
        lon = location.get("longitude")
        street = location.get("street") or {}
        outcome = crime.get("outcome_status") or {}
        lat_val = ""
        lon_val = ""
        if lat is not None and lon is not None:
            try:
                lat_val = float(lat)
                lon_val = float(lon)
            except (TypeError, ValueError):
                lat_val = ""
                lon_val = ""
        rows.append(
            {
                "Crime ID": crime.get("persistent_id") or crime.get("id") or "",
                "Month": crime.get("month") or fallback_month,
                "Reported by": "",
                "Falls within": "",
                "Longitude": lon_val,
                "Latitude": lat_val,
                "Location": street.get("name", ""),
                "LSOA code": "",
                "LSOA name": "",
                "Crime type": crime.get("category", ""),
                "Last outcome category": outcome.get("category", ""),
                "Context": crime.get("context", ""),
            }
        )
    return rows


def update_police_api_history(
    shapefile_path,
    output_path=DEFAULT_OUTPUT_PATH,
    history_path=DEFAULT_HISTORY_PATH,
    api_url=DEFAULT_API_URL,
    poly_url=DEFAULT_POLY_URL,
    api_mode="poly",
    start_month=None,
    end_month=None,
    probe_latest=True,
    max_probe_months=24,
    request_delay=0.2,
    max_wards=None,
    poly_max_points=12,
    poly_simplify_tolerance=0.0005,
    default_start_month=DEFAULT_START_MONTH,
):
    if api_mode == "poly":
        wards = _load_ward_polygons(
            shapefile_path,
            simplify_tolerance=poly_simplify_tolerance,
            max_wards=max_wards,
        )
    else:
        wards = _load_ward_centroids(shapefile_path, max_wards=max_wards)
    if wards.empty:
        return {
            "months_added": 0,
            "crimes_added": 0,
            "history_latest_before": None,
            "history_latest_after": None,
            "latest_available": None,
            "missing_months_count": 0,
        }

    latest_available = end_month or _latest_available_month()
    if probe_latest:
        probe_lat = None
        probe_lon = None
        probe_poly = None
        if api_mode == "poly":
            probe_poly = _geometry_to_poly(
                wards.iloc[0]["geometry"],
                max_points=poly_max_points,
            )
        else:
            probe_lat = wards.iloc[0]["Latitude"]
            probe_lon = wards.iloc[0]["Longitude"]
        latest_available = _resolve_latest_api_month(
            api_mode,
            api_url,
            poly_url,
            latest_available,
            lat=probe_lat,
            lon=probe_lon,
            poly=probe_poly,
            max_probe_months=max_probe_months,
        )

    history_latest = _get_latest_month_from_csv(history_path)
    if history_latest is None:
        history_latest = _get_latest_month_from_csv(output_path)

    start_date = _parse_month(start_month)
    if start_date is None:
        history_date = _parse_month(history_latest)
        if history_date is None:
            start_date = _parse_month(default_start_month) or _parse_month(latest_available)
        else:
            start_date = _next_month(history_date)

    end_date = _parse_month(latest_available)
    if start_date is None or end_date is None or start_date > end_date:
        return {
            "months_added": 0,
            "crimes_added": 0,
            "history_latest_before": history_latest,
            "history_latest_after": history_latest,
            "latest_available": latest_available,
            "missing_months_count": 0,
        }

    months = [_format_month(m) for m in _month_range(start_date, end_date)]
    if not months:
        return {
            "months_added": 0,
            "crimes_added": 0,
            "history_latest_before": history_latest,
            "history_latest_after": history_latest,
            "latest_available": latest_available,
            "missing_months_count": 0,
        }

    history_chunks = []
    last_raw_df = None
    last_successful_month = None
    missing_months = []
    crimes_added = 0

    for month in months:
        logger.info("Fetching police API data for %s (%s wards)", month, len(wards))
        month_rows = []
        month_history_rows = []
        for _, ward in wards.iterrows():
            if api_mode == "poly":
                poly = _geometry_to_poly(
                    ward["geometry"],
                    max_points=poly_max_points,
                )
                if not poly:
                    continue
                url = poly_url.format(date=month, poly=poly)
            else:
                lat = ward["Latitude"]
                lon = ward["Longitude"]
                url = api_url.format(date=month, lat=lat, lng=lon)
            try:
                payload = _request_json(url)
            except Exception:
                continue

            if isinstance(payload, dict) and payload.get("error"):
                continue

            crimes = payload if isinstance(payload, list) else []
            month_rows.extend(_normalise_raw_rows(crimes, fallback_month=month))
            month_history_rows.extend(_normalise_history_rows(crimes, fallback_month=month))
            if request_delay:
                time.sleep(request_delay)

        if not month_rows:
            logger.warning("No crimes returned for %s", month)
            missing_months.append(month)
            continue

        raw_df = pd.DataFrame(month_rows)
        raw_df = raw_df.drop_duplicates()
        history_df = pd.DataFrame(month_history_rows)
        history_df = _normalise_history_columns(history_df)
        history_chunks.append(history_df)
        last_raw_df = raw_df
        last_successful_month = month
        crimes_added += len(raw_df)

    if history_path and history_chunks:
        if os.path.exists(history_path):
            existing = pd.read_csv(history_path)
            combined = pd.concat([existing] + history_chunks, ignore_index=True)
        else:
            combined = pd.concat(history_chunks, ignore_index=True)

        if "Crime ID" in combined.columns:
            combined = combined.drop_duplicates(subset=["Crime ID"])
        else:
            combined = combined.drop_duplicates(
                subset=["Month", "Longitude", "Latitude", "Location", "Crime type"]
            )

        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        combined.to_csv(history_path, index=False)

    if last_raw_df is not None and output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        last_raw_df.to_csv(output_path, index=False)

    history_latest_after = last_successful_month or history_latest

    return {
        "months_added": len(history_chunks),
        "crimes_added": crimes_added,
        "history_latest_before": history_latest,
        "history_latest_after": history_latest_after,
        "latest_available": latest_available,
        "missing_months_count": len(missing_months),
    }


def check_police_api_gap(
    shapefile_path,
    output_path=DEFAULT_OUTPUT_PATH,
    history_path=DEFAULT_HISTORY_PATH,
    api_url=DEFAULT_API_URL,
    poly_url=DEFAULT_POLY_URL,
    api_mode="poly",
    probe_latest=True,
    max_probe_months=24,
    poly_max_points=12,
    poly_simplify_tolerance=0.0005,
    default_start_month=DEFAULT_START_MONTH,
):
    history_latest = _get_latest_month_from_csv(history_path)
    if history_latest is None:
        history_latest = _get_latest_month_from_csv(output_path)

    latest_available = _latest_available_month()
    if probe_latest:
        probe_lat = None
        probe_lon = None
        probe_poly = None
        if api_mode == "poly":
            wards = _load_ward_polygons(
                shapefile_path,
                simplify_tolerance=poly_simplify_tolerance,
                max_wards=1,
            )
            if not wards.empty:
                probe_poly = _geometry_to_poly(
                    wards.iloc[0]["geometry"],
                    max_points=poly_max_points,
                )
        else:
            wards = _load_ward_centroids(shapefile_path, max_wards=1)
            if not wards.empty:
                probe_lat = wards.iloc[0]["Latitude"]
                probe_lon = wards.iloc[0]["Longitude"]

        latest_available = _resolve_latest_api_month(
            api_mode,
            api_url,
            poly_url,
            latest_available,
            lat=probe_lat,
            lon=probe_lon,
            poly=probe_poly,
            max_probe_months=max_probe_months,
        )

    gap_months = _month_diff(
        _parse_month(history_latest),
        _parse_month(latest_available),
    )

    return {
        "history_latest": history_latest,
        "latest_available": latest_available,
        "gap_months": gap_months,
        "default_start_month": default_start_month,
    }


def _normalise_history_columns(df):
    for name in FULL_COLUMNS:
        if name not in df.columns:
            df[name] = ""
    return df[FULL_COLUMNS].copy()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest crime data from the data.police.uk API using ward centroids or ward polygons."
    )
    parser.add_argument(
        "--shapefile",
        required=True,
        help="Ward shapefile path for centroid generation.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output CSV path. Defaults to {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--history",
        default=DEFAULT_HISTORY_PATH,
        help=f"Historical CSV path. Defaults to {DEFAULT_HISTORY_PATH}",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="API URL template with {date}, {lat}, {lng} placeholders.",
    )
    parser.add_argument(
        "--poly-url",
        default=DEFAULT_POLY_URL,
        help="API URL template with {date}, {poly} placeholders.",
    )
    parser.add_argument(
        "--api-mode",
        choices=["location", "poly"],
        default="poly",
        help="Use point lookups (location) or polygon lookups (poly).",
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
        help="Probe backwards to find the latest available API month.",
    )
    parser.add_argument(
        "--max-probe-months",
        type=int,
        default=24,
        help="How many months back to probe for available data.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.2,
        help="Delay (seconds) between API requests.",
    )
    parser.add_argument(
        "--poly-max-points",
        type=int,
        default=12,
        help="Maximum polygon vertices passed to the API.",
    )
    parser.add_argument(
        "--poly-simplify",
        type=float,
        default=0.0005,
        help="Geometry simplification tolerance for polygon queries.",
    )
    parser.add_argument(
        "--default-start-month",
        default=DEFAULT_START_MONTH,
        help="Fallback start month when no history exists (YYYY-MM).",
    )
    parser.add_argument(
        "--max-wards",
        type=int,
        help="Limit the number of wards to fetch (useful for testing).",
    )

    args = parser.parse_args()
    info = update_police_api_history(
        shapefile_path=args.shapefile,
        output_path=args.output,
        history_path=args.history,
        api_url=args.api_url,
        poly_url=args.poly_url,
        api_mode=args.api_mode,
        start_month=args.start_month,
        end_month=args.end_month,
        probe_latest=args.probe_latest,
        max_probe_months=args.max_probe_months,
        request_delay=args.request_delay,
        max_wards=args.max_wards,
        poly_max_points=args.poly_max_points,
        poly_simplify_tolerance=args.poly_simplify,
        default_start_month=args.default_start_month,
    )
    print(
        "API update: added {months_added} month(s), {crimes_added} rows. "
        "Latest in history {history_latest_before} -> {history_latest_after} "
        "(latest available {latest_available}).".format(**info)
    )


if __name__ == "__main__":
    main()
