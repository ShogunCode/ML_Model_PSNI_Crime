import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)

from src.postgres_writer import load_from_csvs


def main():
    parser = argparse.ArgumentParser(description="Load CSV outputs into Postgres.")
    parser.add_argument("--url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("POSTGRES_PORT", "5432")))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "postgres"))
    parser.add_argument("--password", default=os.getenv("POSTGRES_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("POSTGRES_DB", "crimemap"))
    parser.add_argument(
        "--crime-events",
        default=os.path.join("data", "processed", "cleaned_crime_data.csv"),
        help="CSV path with ward-mapped crime events.",
    )
    parser.add_argument(
        "--ward-analysis",
        default=os.path.join("outputs", "ward_crime_analysis.csv"),
        help="CSV path for ward analysis output.",
    )
    parser.add_argument(
        "--ward-crime-type-trends",
        default=os.path.join("outputs", "ward_crime_type_trends.csv"),
        help="CSV path for ward crime type trends.",
    )
    parser.add_argument(
        "--ward-officials",
        default=os.path.join("outputs", "ward_officials.csv"),
        help="CSV path for ward officials.",
    )
    parser.add_argument(
        "--gap-report",
        default=os.path.join("outputs", "police_api_gap_report.csv"),
        help="CSV path for the police API gap report.",
    )
    parser.add_argument("--dataset-version", default=os.getenv("CRIMEMAP_DATASET_VERSION"))
    parser.add_argument("--coverage-start", default=os.getenv("CRIMEMAP_COVERAGE_START"))
    parser.add_argument("--coverage-end", default=os.getenv("CRIMEMAP_COVERAGE_END"))
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--skip-events", action="store_true")
    parser.add_argument("--skip-ward-analysis", action="store_true")
    parser.add_argument("--skip-ward-trends", action="store_true")
    parser.add_argument("--skip-ward-officials", action="store_true")
    parser.add_argument("--skip-gap", action="store_true")

    args = parser.parse_args()

    total_rows = load_from_csvs(
        url=args.url,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        crime_events_path=args.crime_events,
        ward_analysis_path=args.ward_analysis,
        ward_trends_path=args.ward_crime_type_trends,
        ward_officials_path=args.ward_officials,
        gap_report_path=args.gap_report,
        batch_size=args.batch_size,
        skip_events=args.skip_events,
        skip_ward_analysis=args.skip_ward_analysis,
        skip_ward_trends=args.skip_ward_trends,
        skip_ward_officials=args.skip_ward_officials,
        skip_gap=args.skip_gap,
        dataset_version=args.dataset_version,
        coverage_start=args.coverage_start,
        coverage_end=args.coverage_end,
        source="csv_load",
    )

    print(f"Loaded {total_rows} rows into Postgres.")


if __name__ == "__main__":
    main()
