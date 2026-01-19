from dataprocessing import clean_data, load_and_preprocess_data
from officials import fetch_opencouncildata_officials, fetch_ward_officials
from clustering import (
    apply_dbscan,
    identify_high_density_areas,
    get_cluster_centers,
    count_crimes_per_cluster,
    merge_cluster_info,
    dbscan_sweep,
    calculate_cluster_metrics,
)
from visual import (
    plot_cluster_centers,
    plot_crime_heatmap,
    plot_interactive_ward_map,
)
from analytics import build_ward_analysis, build_ward_crime_type_trends
from ingest_psni import update_psni_history, check_history_gap
from ingest_police_api import update_police_api_history, check_police_api_gap
from mysql_writer import load_from_csvs
from utils import optimise_data_types, calculate_crime_rate, handle_missing_values
from utils import (
    validate_required_columns,
    validate_coordinate_ranges,
    summarize_population_coverage,
)
import os
import logging
import argparse
import shlex
import bisect
import geopandas as gpd
import pandas as pd
import datetime as dt

# TODO - build pipeline for PSNI website
# TODO - ROI crime data
# TODO - deploy to web
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# TODO - add requirements.txt
logger = logging.getLogger(__name__)

DEFAULT_COUNCIL_EMAIL_SOURCES = [
    "https://antrimandnewtownabbey.gov.uk/councillors/",
    "https://www.ardsandnorthdown.gov.uk/council/your-councillors",
    "https://www.armaghbanbridgecraigavon.gov.uk/councillors/",
    "https://minutes.belfastcity.gov.uk/mgMemberIndex.aspx?bcr=1",
    "https://www.causewaycoastandglens.gov.uk/council/councillors",
    "https://meetings.derrycityandstrabanedistrict.com/mgMemberIndex.aspx?bcr=1",
    "https://www.fermanaghomagh.com/your-council/councillors/",
    "https://www.lisburncastlereagh.gov.uk/en/council-and-performance/councillors-and-committees",
    "https://www.midandeastantrim.gov.uk/council/councillors",
    "https://mid-ulster.cmis-ni.org/midulster/Councillors.aspx",
    "https://www.newrymournedown.org/your-councillors",
]

def _parse_csv_list(value, cast):
    if value is None:
        return []
    items = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            items.append(cast(raw))
        except ValueError:
            continue
    return items

def _build_sweep_params(args, cluster_level, expand=False):
    eps_list = _parse_csv_list(args.sweep_eps_km, float) or [0.5, 1.0, 1.5, 2.0, 3.0]
    min_samples_list = _parse_csv_list(args.sweep_min_samples, int) or [5, 10, 20, 30, 50]

    if expand:
        if cluster_level == "ward":
            extra_eps = [4.0, 5.0, 7.5, 10.0, 15.0]
            extra_min = [2, 3, 4, 5]
        else:
            extra_eps = [0.25, 0.35, 0.75, 1.25, 2.5]
            extra_min = [2, 3, 4, 5, 8]
        eps_list = sorted(set(eps_list + extra_eps))
        min_samples_list = sorted(set(min_samples_list + extra_min))

    return eps_list, min_samples_list

def _best_sweep_row(sweep_df):
    if sweep_df is None or sweep_df.empty:
        return None
    candidates = sweep_df[sweep_df["cluster_count"] > 0].copy()
    if candidates.empty:
        return None
    candidates = candidates.sort_values(
        by=["cluster_count", "noise_share", "avg_cluster_size"],
        ascending=[False, True, False],
    )
    return candidates.iloc[0]

def _score_metrics(metrics):
    return (
        int(metrics.get("cluster_count", 0)),
        -float(metrics.get("noise_share", 1.0)),
        float(metrics.get("avg_cluster_size", 0.0)),
    )

def _percentile(values, value):
    if value is None or (isinstance(value, float) and pd.isna(value)) or not values:
        return None
    idx = bisect.bisect_right(values, value)
    return idx / len(values)

def _rating_band(score):
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return "Unknown"
    if score >= 85:
        return "High"
    if score >= 70:
        return "Elevated"
    if score >= 55:
        return "Watch"
    return "Stable"

def _add_rating_bands(df):
    if df is None or df.empty:
        return df
    data = df.copy()
    rates = pd.to_numeric(data.get("CrimeRatePer100kPeople"), errors="coerce")
    slopes = pd.to_numeric(data.get("TrendSlope"), errors="coerce")
    rate_values = sorted(rates.dropna().tolist())
    slope_values = sorted(slopes.dropna().tolist())

    if "RatePercentile" in data.columns:
        rate_percentile = pd.to_numeric(data["RatePercentile"], errors="coerce")
    else:
        rate_percentile = pd.Series([pd.NA] * len(data), index=data.index)

    if rate_percentile.notna().any():
        fallback = rates.apply(lambda v: _percentile(rate_values, v))
        rate_percentile = rate_percentile.where(rate_percentile.notna(), fallback)
    else:
        rate_percentile = rates.apply(lambda v: _percentile(rate_values, v))

    trend_percentile = slopes.apply(lambda v: _percentile(slope_values, v))
    rating_score = (0.7 * rate_percentile.fillna(0) + 0.3 * trend_percentile.fillna(0)) * 100
    rating_score = rating_score.round(1)
    rating_score = rating_score.where(rate_percentile.notna())
    data["RatingScore"] = rating_score
    data["RatingBand"] = rating_score.apply(_rating_band)
    return data

def _load_processed_merged(path):
    df = pd.read_csv(path)
    if "geometry" not in df.columns:
        return gpd.GeoDataFrame(df)

    try:
        geometry = gpd.GeoSeries.from_wkt(df["geometry"])
    except Exception as exc:
        logger.warning("Failed to parse geometry from %s: %s", path, exc)
        return gpd.GeoDataFrame(df)

    df = df.drop(columns=["geometry"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    return gdf

def _safe_to_csv(df, path, label):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        df.to_csv(path, index=False)
        logger.info("%s saved to '%s'", label, path)
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = f"{base}_{stamp}{ext or '.csv'}"
        df.to_csv(fallback, index=False)
        logger.warning(
            "%s path '%s' is locked; wrote '%s' instead.",
            label,
            path,
            fallback,
        )
        return fallback


def _csv_has_data(path):
    if not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) == 0:
            return False
    except OSError:
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    return True
    except OSError:
        return False
    return False

def build_parser():
    parser = argparse.ArgumentParser(
        description="Run the PSNI crime clustering pipeline."
    )
    parser.add_argument(
        "--shapefile-path",
        default="data/raw/OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012)/OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012).shp",
    )
    parser.add_argument("--crime-data-path", default="data/raw/crime_data.csv")
    parser.add_argument(
        "--crime-history-path",
        default="data/processed/crime_history.csv",
        help="Historical crime CSV path used for full-period analysis.",
    )
    parser.add_argument(
        "--population-data-path",
        default="data/raw/census-2021-ms-a01.xlsx",
    )
    parser.add_argument("--cleaned-crime-data-path", default="data/processed/cleaned_crime_data.csv")
    parser.add_argument("--merged-data-path", default="data/processed/merged_data.csv")
    parser.add_argument("--cluster-info-path", default="outputs/cluster_info.csv")
    parser.add_argument("--centers-map-path", default="outputs/cluster_centers_map.html")
    parser.add_argument("--heatmap-path", default="outputs/crime_heatmap.html")
    parser.add_argument("--wards-map-path", default="outputs/wards_interactive_map.html")
    parser.add_argument("--ward-analysis-path", default="outputs/ward_crime_analysis.csv")
    parser.add_argument(
        "--ward-crime-type-trends-path",
        default="outputs/ward_crime_type_trends.csv",
        help="Output CSV for ward-by-crime-type trend metrics.",
    )
    parser.add_argument(
        "--ward-officials-path",
        default="outputs/ward_officials.csv",
        help="Output CSV for ward elected officials.",
    )
    parser.add_argument(
        "--officials-api-url",
        default=os.getenv("CRIMEMAP_OFFICIALS_API_URL"),
        help="API endpoint that returns ward officials data.",
    )
    parser.add_argument(
        "--officials-api-key",
        default=os.getenv("CRIMEMAP_OFFICIALS_API_KEY"),
        help="API key for the officials endpoint (Bearer token).",
    )
    parser.add_argument(
        "--officials-api-timeout",
        type=int,
        default=int(os.getenv("CRIMEMAP_OFFICIALS_API_TIMEOUT", "15")),
        help="Timeout (seconds) for officials API calls.",
    )
    parser.add_argument(
        "--officials-source",
        choices=["api", "opencouncildata"],
        default=os.getenv("CRIMEMAP_OFFICIALS_SOURCE", "api"),
        help="Officials data source to use (api or opencouncildata).",
    )
    parser.add_argument(
        "--officials-opencouncil-base-url",
        default=os.getenv(
            "CRIMEMAP_OFFICIALS_OPENCOUNCIL_URL",
            "https://opencouncildata.co.uk/nicouncil.php",
        ),
        help="Base URL for opencouncildata council pages.",
    )
    parser.add_argument(
        "--officials-opencouncil-start",
        type=int,
        default=int(os.getenv("CRIMEMAP_OFFICIALS_OPENCOUNCIL_START", "1001")),
        help="Start council ID for opencouncildata scraping.",
    )
    parser.add_argument(
        "--officials-opencouncil-end",
        type=int,
        default=int(os.getenv("CRIMEMAP_OFFICIALS_OPENCOUNCIL_END", "1010")),
        help="End council ID for opencouncildata scraping.",
    )
    parser.add_argument(
        "--officials-opencouncil-year",
        type=int,
        default=int(os.getenv("CRIMEMAP_OFFICIALS_OPENCOUNCIL_YEAR", "0")),
        help="Year parameter for opencouncildata (0 = latest).",
    )
    parser.add_argument(
        "--officials-opencouncil-debug-dir",
        default=os.getenv("CRIMEMAP_OFFICIALS_OPENCOUNCIL_DEBUG_DIR"),
        help="Optional dir to write opencouncildata HTML snapshots.",
    )
    parser.add_argument(
        "--officials-council-email-disable",
        action="store_true",
        help="Skip scraping council sites for councillor emails.",
    )
    parser.add_argument(
        "--officials-council-email-source",
        action="append",
        default=[],
        help="Council councillor list URL to scrape for emails. Repeatable.",
    )
    parser.add_argument(
        "--officials-council-email-debug-dir",
        default=os.getenv("CRIMEMAP_OFFICIALS_COUNCIL_EMAIL_DEBUG_DIR"),
        help="Optional dir to write council email HTML snapshots.",
    )
    parser.add_argument(
        "--officials-council-email-local-dir",
        default=os.getenv("CRIMEMAP_OFFICIALS_COUNCIL_EMAIL_LOCAL_DIR"),
        help="Optional dir of saved council HTML pages to parse for emails.",
    )
    parser.add_argument(
        "--dea-shapefile-path",
        default=(
            "data/raw/DEA_Shapefile/OSNI_Open_Data_Largescale_Boundaries_"
            "District_Electoral_Areas_(2012)/"
            "OSNI_Open_Data_Largescale_Boundaries_District_Electoral_Areas_(2012).shp"
        ),
        help="DEA shapefile path used to map DEA names to ward codes.",
    )
    parser.add_argument(
        "--high-rate-quantile",
        type=float,
        default=0.8,
        help="Quantile threshold for marking high crime rates (0-1).",
    )
    parser.add_argument(
        "--update-psni",
        action="store_true",
        help="Download missing PSNI months and update crime_data.csv before processing.",
    )
    parser.add_argument(
        "--update-police-api",
        action="store_true",
        help="Download missing months using the data.police.uk API before processing.",
    )
    parser.add_argument(
        "--psni-history-path",
        default="data/processed/crime_history.csv",
        help="History CSV path used for PSNI updates and gap checks.",
    )
    parser.add_argument(
        "--psni-archive-url",
        default="https://data.police.uk/data/archive/{date}.zip",
        help="Archive URL template with {date} placeholder.",
    )
    parser.add_argument(
        "--psni-start-month",
        help="Start month in YYYY-MM format for PSNI history updates.",
    )
    parser.add_argument(
        "--psni-end-month",
        help="End month in YYYY-MM format for PSNI history updates.",
    )
    parser.add_argument(
        "--psni-probe-latest",
        action="store_true",
        help="Probe backwards to find the latest available PSNI archive month.",
    )
    parser.add_argument(
        "--psni-max-probe-months",
        type=int,
        default=24,
        help="How many months back to probe for available PSNI archives.",
    )
    parser.add_argument(
        "--api-history-path",
        default="data/processed/crime_history.csv",
        help="History CSV path used for police API updates.",
    )
    parser.add_argument(
        "--api-url",
        default="https://data.police.uk/api/crimes-at-location?date={date}&lat={lat}&lng={lng}",
        help="API URL template with {date}, {lat}, {lng} placeholders.",
    )
    parser.add_argument(
        "--api-mode",
        choices=["location", "poly"],
        default="poly",
        help="Use point lookups (location) or polygon lookups (poly) for the API.",
    )
    parser.add_argument(
        "--api-poly-url",
        default="https://data.police.uk/api/crimes-street/all-crime?date={date}&poly={poly}",
        help="Polygon API URL template with {date}, {poly} placeholders.",
    )
    parser.add_argument(
        "--api-start-month",
        help="Start month in YYYY-MM format for police API updates.",
    )
    parser.add_argument(
        "--api-end-month",
        help="End month in YYYY-MM format for police API updates.",
    )
    parser.add_argument(
        "--api-probe-latest",
        action="store_true",
        help="Probe backwards to find the latest available API month.",
    )
    parser.add_argument(
        "--api-max-probe-months",
        type=int,
        default=24,
        help="How many months back to probe for available API data.",
    )
    parser.add_argument(
        "--api-request-delay",
        type=float,
        default=0.2,
        help="Delay (seconds) between police API requests.",
    )
    parser.add_argument(
        "--api-poly-max-points",
        type=int,
        default=12,
        help="Maximum polygon vertices passed to the API.",
    )
    parser.add_argument(
        "--api-poly-simplify",
        type=float,
        default=0.0005,
        help="Geometry simplification tolerance for polygon queries.",
    )
    parser.add_argument(
        "--api-default-start-month",
        default="2023-08",
        help="Fallback start month when no history exists (YYYY-MM).",
    )
    parser.add_argument(
        "--api-gap-report-path",
        default="outputs/police_api_gap_report.csv",
        help="Output CSV path for the API gap report.",
    )
    parser.add_argument(
        "--api-max-wards",
        type=int,
        help="Limit number of wards to fetch (useful for testing).",
    )
    parser.add_argument(
        "--use-processed",
        action="store_true",
        help="Reuse existing processed CSVs to skip repeated cleaning/merging steps.",
    )
    parser.add_argument(
        "--use-history-for-analysis",
        action="store_true",
        help="Use the crime history CSV (all months) instead of the latest-month CSV.",
    )
    parser.add_argument(
        "--write-mysql",
        action="store_true",
        help="Load pipeline outputs into MySQL after processing.",
    )
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "localhost"))
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"))
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", ""))
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "crimemap"))
    parser.add_argument("--mysql-schema", default=os.path.join("db", "schema.sql"))
    parser.add_argument("--mysql-batch-size", type=int, default=2000)
    parser.add_argument("--mysql-skip-events", action="store_true")
    parser.add_argument("--mysql-skip-ward-analysis", action="store_true")
    parser.add_argument("--mysql-skip-ward-trends", action="store_true")
    parser.add_argument("--mysql-skip-gap", action="store_true")
    parser.add_argument(
        "--write-postgres",
        action="store_true",
        help="Load pipeline outputs into Postgres after processing.",
    )
    parser.add_argument("--postgres-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--postgres-host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument(
        "--postgres-port", type=int, default=int(os.getenv("POSTGRES_PORT", "5432"))
    )
    parser.add_argument("--postgres-user", default=os.getenv("POSTGRES_USER", "postgres"))
    parser.add_argument("--postgres-password", default=os.getenv("POSTGRES_PASSWORD", ""))
    parser.add_argument("--postgres-database", default=os.getenv("POSTGRES_DB", "crimemap"))
    parser.add_argument("--postgres-batch-size", type=int, default=2000)
    parser.add_argument("--postgres-skip-events", action="store_true")
    parser.add_argument("--postgres-skip-ward-analysis", action="store_true")
    parser.add_argument("--postgres-skip-ward-trends", action="store_true")
    parser.add_argument("--postgres-skip-ward-officials", action="store_true")
    parser.add_argument("--postgres-skip-gap", action="store_true")
    parser.add_argument(
        "--dataset-version",
        default=os.getenv("CRIMEMAP_DATASET_VERSION"),
        help="Dataset version label stored with DB loads.",
    )
    parser.add_argument(
        "--coverage-start",
        default=os.getenv("CRIMEMAP_COVERAGE_START"),
        help="Override coverage start month (YYYY-MM) for DB loads.",
    )
    parser.add_argument(
        "--coverage-end",
        default=os.getenv("CRIMEMAP_COVERAGE_END"),
        help="Override coverage end month (YYYY-MM) for DB loads.",
    )
    parser.add_argument("--eps-km", type=float, default=0.5)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument(
        "--cluster-level",
        choices=["ward", "point"],
        default="ward",
        help="Cluster at ward centroids or at individual crime points."
    )
    parser.add_argument(
        "--sweep-dbscan",
        action="store_true",
        help="Run a DBSCAN parameter sweep and save a report."
    )
    parser.add_argument(
        "--sweep-eps-km",
        default="0.5,1.0,1.5,2.0,3.0",
        help="Comma-separated eps (km) values for sweep."
    )
    parser.add_argument(
        "--sweep-min-samples",
        default="5,10,20,30,50",
        help="Comma-separated min_samples values for sweep."
    )
    parser.add_argument(
        "--dbscan-sweep-path",
        default="outputs/dbscan_sweep.csv",
        help="Output CSV for DBSCAN sweep metrics."
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Run once without the interactive prompt."
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run the full pipeline once and exit."
    )
    return parser

def build_interface():
    parser = build_parser()
    return parser.parse_args()

def run_pipeline(args):
    # file paths
    shapefile_path = args.shapefile_path
    crime_data_path = args.crime_data_path
    population_data_path = args.population_data_path
    cleaned_crime_data_path = args.cleaned_crime_data_path
    merged_data_path = args.merged_data_path
    cluster_info_path = args.cluster_info_path
    use_processed = args.use_processed
    analysis_crime_path = crime_data_path
    use_history_for_analysis = args.use_history_for_analysis or args.update_police_api

    # make sure output directories exist
    os.makedirs('data/processed', exist_ok=True)
    os.makedirs('outputs', exist_ok=True)

    if args.update_police_api:
        if args.update_psni:
            logger.warning(
                "Both --update-police-api and --update-psni were set; using API update only."
            )
        gap = check_police_api_gap(
            shapefile_path=shapefile_path,
            output_path=crime_data_path,
            history_path=args.api_history_path,
            api_url=args.api_url,
            poly_url=args.api_poly_url,
            api_mode=args.api_mode,
            probe_latest=args.api_probe_latest,
            max_probe_months=args.api_max_probe_months,
            poly_max_points=args.api_poly_max_points,
            poly_simplify_tolerance=args.api_poly_simplify,
            default_start_month=args.api_default_start_month,
        )
        gap_df = pd.DataFrame([{
            "CheckedAt": dt.datetime.now().isoformat(timespec="seconds"),
            "HistoryLatest": gap["history_latest"],
            "LatestAvailable": gap["latest_available"],
            "GapMonths": gap["gap_months"],
            "DefaultStartMonth": gap["default_start_month"],
        }])
        _safe_to_csv(gap_df, args.api_gap_report_path, "Police API gap report")

        should_update = True
        if args.api_start_month or args.api_end_month:
            should_update = True
        elif gap["history_latest"] and gap["gap_months"] is not None and gap["gap_months"] <= 0:
            should_update = False
            logger.info("Police API history is up to date; skipping API update.")

        if should_update:
            info = update_police_api_history(
                shapefile_path=shapefile_path,
                output_path=crime_data_path,
                history_path=args.api_history_path,
                api_url=args.api_url,
                poly_url=args.api_poly_url,
                api_mode=args.api_mode,
                start_month=args.api_start_month,
                end_month=args.api_end_month,
                probe_latest=args.api_probe_latest,
                max_probe_months=args.api_max_probe_months,
                request_delay=args.api_request_delay,
                max_wards=args.api_max_wards,
                poly_max_points=args.api_poly_max_points,
                poly_simplify_tolerance=args.api_poly_simplify,
                default_start_month=args.api_default_start_month,
            )
            logger.info(
                "Police API update: added %s month(s), %s rows. Latest in history %s -> %s (latest available %s).",
                info["months_added"],
                info["crimes_added"],
                info["history_latest_before"],
                info["history_latest_after"],
                info["latest_available"],
            )
            if info.get("missing_months_count"):
                logger.warning(
                    "Police API returned no data for %s month(s) in the update range.",
                    info["missing_months_count"],
                )
            if use_processed:
                logger.warning(
                    "Police API data updated but --use-processed is set; processed data may be stale."
                )
    elif args.update_psni:
        probe_latest = args.psni_probe_latest or not os.path.exists(args.psni_history_path)
        info = update_psni_history(
            output_path=crime_data_path,
            history_path=args.psni_history_path,
            archive_url=args.psni_archive_url,
            start_month=args.psni_start_month,
            end_month=args.psni_end_month,
            probe_latest=probe_latest,
            max_probe_months=args.psni_max_probe_months,
        )
        logger.info(
            "PSNI update: added %s month(s). Latest in history %s -> %s (latest available %s).",
            info["months_added"],
            info["history_latest_before"],
            info["history_latest_after"],
            info["latest_available"],
        )
        if info.get("missing_month"):
            logger.warning(
                "PSNI archive missing for %s; skipped.",
                info["missing_month"],
            )
        if info.get("missing_months_count"):
            logger.warning(
                "PSNI archive missing for %s month(s) in the update range.",
                info["missing_months_count"],
            )
        if use_processed:
            logger.warning(
                "PSNI data updated but --use-processed is set; processed data may be stale."
            )
    else:
        gap = check_history_gap(args.psni_history_path)
        if gap["gap_months"] is None:
            logger.info(
                "PSNI history gap check skipped (missing history or Month column)."
            )
        elif gap["gap_months"] > 0:
            logger.warning(
                "PSNI history gap: latest %s vs latest available %s (%s month(s) behind).",
                gap["history_latest"],
                gap["latest_available"],
                gap["gap_months"],
            )

    if use_history_for_analysis:
        if os.path.exists(args.crime_history_path):
            analysis_crime_path = args.crime_history_path
            logger.info(
                "Using history CSV for analysis: '%s'",
                analysis_crime_path,
            )
        else:
            logger.warning(
                "History CSV not found at '%s'; falling back to '%s'.",
                args.crime_history_path,
                analysis_crime_path,
            )
    elif not _csv_has_data(analysis_crime_path) and _csv_has_data(args.crime_history_path):
        analysis_crime_path = args.crime_history_path
        logger.warning(
            "Crime data CSV '%s' is empty; falling back to history CSV '%s'.",
            crime_data_path,
            analysis_crime_path,
        )
    elif not _csv_has_data(analysis_crime_path):
        raise ValueError(
            f"Crime data CSV '{analysis_crime_path}' is empty. "
            "Run with --update-police-api or provide a populated --crime-data-path."
        )

    if use_processed and os.path.exists(cleaned_crime_data_path):
        logger.info("Loading cleaned crime data from '%s'", cleaned_crime_data_path)
        cleaned_crime_data = pd.read_csv(cleaned_crime_data_path)
    else:
        logger.info("Starting the data cleaning process")
        cleaned_crime_data = clean_data(
            shapefile_path,
            analysis_crime_path,
            cleaned_crime_data_path,
        )
        logger.info("Optimising data types")
        cleaned_crime_data = optimise_data_types(cleaned_crime_data)
        cleaned_crime_data.to_csv(cleaned_crime_data_path, index=False)
        logger.info("Handling missing values")
        cleaned_crime_data = handle_missing_values(
            cleaned_crime_data,
            strategy='mean',
        )

    if use_processed and os.path.exists(merged_data_path):
        logger.info("Loading merged data from '%s'", merged_data_path)
        merged_data = _load_processed_merged(merged_data_path)
    else:
        logger.info("Loading and preprocessing data for model")
        merged_data = load_and_preprocess_data(
            shapefile_path,
            cleaned_crime_data_path,
            population_data_path,
            output_csv=merged_data_path,
        )

    logger.info("Calculating crime rates")
    
    # Calculate crime rate per 100,000 people
    if (
        "CrimeRatePer100kPeople" not in merged_data.columns
        or merged_data["CrimeRatePer100kPeople"].isna().any()
    ):
        merged_data['CrimeRatePer100kPeople'] = merged_data.apply(
            lambda row: calculate_crime_rate(row['NumberOfCrimes'], row['Population']),
            axis=1
        )

    logger.info("Optimising merged data types")
    
    # Optimise data types for the merged data
    if not use_processed or not os.path.exists(merged_data_path):
        merged_data = optimise_data_types(merged_data)
        merged_data.to_csv(merged_data_path, index=False)

    logger.info("Getting coordinates for clustering")
    
    # Validate cleaned crime data
    missing_cols = validate_required_columns(
        cleaned_crime_data,
        ["Latitude", "Longitude", "WardCode", "WARDNAME"],
        "cleaned_crime_data",
    )
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")
    coord_issues = validate_coordinate_ranges(cleaned_crime_data)
    for issue in coord_issues:
        logger.warning(issue)

    # Validate population coverage
    coverage = summarize_population_coverage(merged_data, population_col="Population")
    if coverage["missing_share"] > 0.1:
        logger.warning(
            "Population coverage issue: %s missing (%.1f%%).",
            coverage["missing_count"],
            coverage["missing_share"] * 100,
        )

    cluster_level = args.cluster_level

    # Extract coordinates in (lat, lon) order for haversine
    if cluster_level == "ward":
        wards_geo = merged_data.copy()
        wards_proj = wards_geo.to_crs(epsg=29902)
        centroids = wards_proj.geometry.centroid
        centroids_geo = gpd.GeoSeries(centroids, crs=wards_proj.crs).to_crs(epsg=4326)
        wards_geo["Latitude"] = centroids_geo.y
        wards_geo["Longitude"] = centroids_geo.x
        coordinates = wards_geo[["Latitude", "Longitude"]].values
    else:
        coordinates = cleaned_crime_data[["Latitude", "Longitude"]].values

    if args.sweep_dbscan:
        eps_list, min_samples_list = _build_sweep_params(args, args.cluster_level, expand=False)
        sweep_df = dbscan_sweep(coordinates, eps_list, min_samples_list)
        sweep_df.to_csv(args.dbscan_sweep_path, index=False)
        logger.info("DBSCAN sweep saved to '%s'", args.dbscan_sweep_path)
    else:
        sweep_df = None

    logger.info("Applying DBSCAN clustering")
    
    # apply DBSCAN algo
    labels = apply_dbscan(coordinates, eps_km=args.eps_km, min_samples=args.min_samples)
    metrics = calculate_cluster_metrics(labels)
    logger.info(
        "DBSCAN metrics: clusters=%s noise=%.1f%% avg_size=%.1f",
        metrics["cluster_count"],
        metrics["noise_share"] * 100,
        metrics["avg_cluster_size"],
    )

    if metrics["cluster_count"] == 0 and sweep_df is None:
        logger.warning(
            "No clusters found with eps_km=%s min_samples=%s; running a DBSCAN sweep.",
            args.eps_km,
            args.min_samples,
        )
        eps_list, min_samples_list = _build_sweep_params(args, args.cluster_level, expand=True)
        sweep_df = dbscan_sweep(coordinates, eps_list, min_samples_list)
        sweep_df.to_csv(args.dbscan_sweep_path, index=False)
        logger.info("DBSCAN sweep saved to '%s'", args.dbscan_sweep_path)

    if sweep_df is not None:
        best = _best_sweep_row(sweep_df)
        if best is not None:
            best_metrics = {
                "cluster_count": int(best["cluster_count"]),
                "noise_share": float(best["noise_share"]),
                "avg_cluster_size": float(best["avg_cluster_size"]),
            }
            if _score_metrics(best_metrics) > _score_metrics(metrics):
                best_eps = float(best["eps_km"])
                best_min_samples = int(best["min_samples"])
                logger.info(
                    "Applying best DBSCAN parameters from sweep: eps_km=%s min_samples=%s",
                    best_eps,
                    best_min_samples,
                )
                labels = apply_dbscan(
                    coordinates,
                    eps_km=best_eps,
                    min_samples=best_min_samples,
                )
                metrics = calculate_cluster_metrics(labels)
                logger.info(
                    "DBSCAN metrics after sweep: clusters=%s noise=%.1f%% avg_size=%.1f",
                    metrics["cluster_count"],
                    metrics["noise_share"] * 100,
                    metrics["avg_cluster_size"],
                )
            else:
                logger.info("Keeping initial DBSCAN parameters; sweep did not improve metrics.")
        else:
            logger.warning("DBSCAN sweep did not find any parameters that yield clusters.")

    if metrics["cluster_count"] == 0 and cluster_level == "ward":
        logger.warning(
            "No ward-level clusters found after sweep; retrying at point level."
        )
        cluster_level = "point"
        coordinates = cleaned_crime_data[["Latitude", "Longitude"]].values
        eps_list, min_samples_list = _build_sweep_params(args, cluster_level, expand=True)
        sweep_df = dbscan_sweep(coordinates, eps_list, min_samples_list)
        sweep_df.to_csv(args.dbscan_sweep_path, index=False)
        logger.info("DBSCAN sweep saved to '%s'", args.dbscan_sweep_path)
        best = _best_sweep_row(sweep_df)
        if best is not None:
            best_eps = float(best["eps_km"])
            best_min_samples = int(best["min_samples"])
            labels = apply_dbscan(
                coordinates,
                eps_km=best_eps,
                min_samples=best_min_samples,
            )
            metrics = calculate_cluster_metrics(labels)
            logger.info(
                "Point-level DBSCAN metrics: clusters=%s noise=%.1f%% avg_size=%.1f",
                metrics["cluster_count"],
                metrics["noise_share"] * 100,
                metrics["avg_cluster_size"],
            )
        else:
            logger.warning("Point-level sweep did not find any parameters that yield clusters.")

    logger.info("Identifying high crime areas")
    
    # identify high-density crime areas
    if cluster_level == "ward":
        ward_clusters = wards_geo.copy()
        ward_clusters["Cluster"] = labels
        clusters = ward_clusters[ward_clusters["Cluster"] != -1].copy()
    else:
        clusters = identify_high_density_areas(cleaned_crime_data, labels)

    logger.info("Calculating cluster centers and crime numbers")
    
    # cluster centers and counts
    cluster_centers = get_cluster_centers(clusters)
    if cluster_level == "ward":
        cluster_counts = clusters.groupby("Cluster")["NumberOfCrimes"].sum().reset_index()
        cluster_counts.columns = ["Cluster", "CrimeCount"]
        ward_counts = clusters["Cluster"].value_counts().reset_index()
        ward_counts.columns = ["Cluster", "WardCount"]
        cluster_info = merge_cluster_info(cluster_centers, cluster_counts)
        cluster_info = cluster_info.merge(ward_counts, on="Cluster", how="left")
    else:
        cluster_counts = count_crimes_per_cluster(clusters)
        cluster_info = merge_cluster_info(cluster_centers, cluster_counts)

    # save the cluster info to csv file
    _safe_to_csv(cluster_info, cluster_info_path, "Cluster information")

    logger.info("Building ward-level crime analysis")
    ward_analysis = build_ward_analysis(
        merged_data,
        cleaned_crime_data,
        high_rate_quantile=args.high_rate_quantile,
    )
    ward_analysis = _add_rating_bands(ward_analysis)
    _safe_to_csv(ward_analysis, args.ward_analysis_path, "Ward analysis")

    if args.officials_source == "opencouncildata":
        logger.info("Fetching ward officials from opencouncildata")
        council_email_sources = []
        if not args.officials_council_email_disable:
            if args.officials_council_email_local_dir:
                local_dir = args.officials_council_email_local_dir
                if os.path.isdir(local_dir):
                    council_email_sources = [
                        os.path.join(local_dir, filename)
                        for filename in sorted(os.listdir(local_dir))
                        if filename.lower().endswith(".html")
                    ]
                else:
                    logger.warning(
                        "Council email local dir not found: %s", local_dir
                    )
            else:
                council_email_sources = (
                    args.officials_council_email_source or DEFAULT_COUNCIL_EMAIL_SOURCES
                )
        try:
            officials_df = fetch_opencouncildata_officials(
                base_url=args.officials_opencouncil_base_url,
                council_start=args.officials_opencouncil_start,
                council_end=args.officials_opencouncil_end,
                year=args.officials_opencouncil_year,
                ward_analysis=ward_analysis,
                ward_shapefile_path=args.shapefile_path,
                dea_shapefile_path=args.dea_shapefile_path,
                timeout=args.officials_api_timeout,
                debug_dir=args.officials_opencouncil_debug_dir,
                council_email_sources=council_email_sources,
                council_email_debug_dir=args.officials_council_email_debug_dir,
            )
            if officials_df is not None and not officials_df.empty:
                _safe_to_csv(officials_df, args.ward_officials_path, "Ward officials")
            else:
                logger.warning("No ward officials returned from opencouncildata.")
        except Exception as exc:
            logger.warning("Officials scrape failed: %s", exc)
    elif args.officials_api_url:
        logger.info("Fetching ward officials from API")
        try:
            officials_df = fetch_ward_officials(
                api_url=args.officials_api_url,
                ward_analysis=ward_analysis,
                api_key=args.officials_api_key,
                timeout=args.officials_api_timeout,
            )
            if officials_df is not None and not officials_df.empty:
                _safe_to_csv(officials_df, args.ward_officials_path, "Ward officials")
            else:
                logger.warning("No ward officials returned from API.")
        except Exception as exc:
            logger.warning("Officials API failed: %s", exc)

    population_by_code = {}
    if "WardCode" in ward_analysis.columns and "Population" in ward_analysis.columns:
        population_by_code = (
            ward_analysis[["WardCode", "Population"]]
            .dropna(subset=["WardCode"])
            .set_index("WardCode")["Population"]
            .to_dict()
        )

    logger.info("Building ward crime type trends")
    ward_crime_type_trends = build_ward_crime_type_trends(
        cleaned_crime_data,
        population_by_code=population_by_code,
    )
    _safe_to_csv(
        ward_crime_type_trends,
        args.ward_crime_type_trends_path,
        "Ward crime type trends",
    )

    logger.info("Generating maps and visuals!")
    
    # visuals - build interface for them next 
    # TODO
    if "WardCode" in ward_analysis.columns:
        code_col = None
        if "WardCode_w" in merged_data.columns:
            code_col = "WardCode_w"
        elif "WardCode" in merged_data.columns:
            code_col = "WardCode"
        if code_col:
            if "CrimeRatePer100kPeople" in ward_analysis.columns:
                rate_lookup = (
                    ward_analysis[["WardCode", "CrimeRatePer100kPeople"]]
                    .dropna(subset=["WardCode"])
                    .assign(WardCode=lambda df: df["WardCode"].astype(str))
                    .set_index("WardCode")["CrimeRatePer100kPeople"]
                    .to_dict()
                )
                mapped = merged_data[code_col].astype(str).map(rate_lookup)
                merged_data["AnnualizedCrimeRatePer100k"] = mapped
                existing = merged_data.get("CrimeRatePer100kPeople")
                if existing is not None:
                    merged_data["CrimeRatePer100kPeople"] = mapped.where(
                        mapped.notna(), existing
                    )
                else:
                    merged_data["CrimeRatePer100kPeople"] = mapped
            if "RatingBand" in ward_analysis.columns:
                band_lookup = (
                    ward_analysis[["WardCode", "RatingBand"]]
                    .dropna(subset=["WardCode"])
                    .assign(WardCode=lambda df: df["WardCode"].astype(str))
                    .set_index("WardCode")["RatingBand"]
                    .to_dict()
                )
                merged_data["RatingBand"] = merged_data[code_col].astype(str).map(band_lookup)

    plot_cluster_centers(cluster_info, output_path=args.centers_map_path)
    plot_crime_heatmap(cleaned_crime_data, output_path=args.heatmap_path)
    plot_interactive_ward_map(merged_data, output_path=args.wards_map_path)

    if args.write_mysql:
        if not use_history_for_analysis:
            logger.warning(
                "MySQL load uses '%s'; consider --use-history-for-analysis for full history.",
                cleaned_crime_data_path,
            )
        try:
            rows_loaded = load_from_csvs(
                host=args.mysql_host,
                port=args.mysql_port,
                user=args.mysql_user,
                password=args.mysql_password,
                database=args.mysql_database,
                schema_path=args.mysql_schema,
                crime_events_path=cleaned_crime_data_path,
                ward_analysis_path=args.ward_analysis_path,
                ward_trends_path=args.ward_crime_type_trends_path,
                gap_report_path=args.api_gap_report_path,
                batch_size=args.mysql_batch_size,
                skip_events=args.mysql_skip_events,
                skip_ward_analysis=args.mysql_skip_ward_analysis,
                skip_ward_trends=args.mysql_skip_ward_trends,
                skip_gap=args.mysql_skip_gap,
                source="pipeline",
            )
            logger.info("MySQL load complete: %s rows.", rows_loaded)
        except Exception as exc:
            logger.warning("MySQL load failed: %s", exc)

    if args.write_postgres:
        if not use_history_for_analysis:
            logger.warning(
                "Postgres load uses '%s'; consider --use-history-for-analysis for full history.",
                cleaned_crime_data_path,
            )
        try:
            from postgres_writer import load_from_csvs as load_postgres

            rows_loaded = load_postgres(
                url=args.postgres_url,
                host=args.postgres_host,
                port=args.postgres_port,
                user=args.postgres_user,
                password=args.postgres_password,
                database=args.postgres_database,
                crime_events_path=cleaned_crime_data_path,
                ward_analysis_path=args.ward_analysis_path,
                ward_trends_path=args.ward_crime_type_trends_path,
                ward_officials_path=args.ward_officials_path,
                gap_report_path=args.api_gap_report_path,
                batch_size=args.postgres_batch_size,
                skip_events=args.postgres_skip_events,
                skip_ward_analysis=args.postgres_skip_ward_analysis,
                skip_ward_trends=args.postgres_skip_ward_trends,
                skip_ward_officials=args.postgres_skip_ward_officials,
                skip_gap=args.postgres_skip_gap,
                dataset_version=args.dataset_version,
                coverage_start=args.coverage_start,
                coverage_end=args.coverage_end,
                source="pipeline",
            )
            logger.info("Postgres load complete: %s rows.", rows_loaded)
        except Exception as exc:
            logger.warning("Postgres load failed: %s", exc)

    # success message!
    logger.info("All tasks completed successfully.")


def prompt_loop(parser, base_args):
    while True:
        choice = input("Run all (A), run flags (F), or exit (E)? ").strip().lower()
        if choice in ("a", "all", ""):
            run_pipeline(base_args)
            return
        if choice in ("e", "exit", "q", "quit"):
            return
        if choice in ("f", "flags"):
            while True:
                flag_line = input("Enter flags (or 'exit' to return): ").strip()
                if flag_line.lower() in ("exit", "e", "back", "quit", "q"):
                    break
                try:
                    args = parser.parse_args(shlex.split(flag_line))
                except SystemExit:
                    print("Invalid flags. Try again.")
                    continue
                run_pipeline(args)
            continue
        print("Please enter A, F, or E.")

def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.no_prompt or args.run_all:
        run_pipeline(args)
        return
    prompt_loop(parser, args)

if __name__ == '__main__':
    main()
