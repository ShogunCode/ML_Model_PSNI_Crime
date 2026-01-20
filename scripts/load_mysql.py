import argparse
import os
import sys


def main():
    root_dir = os.path.dirname(os.path.dirname(__file__))
    sys.path.insert(0, root_dir)
    from src.mysql_writer import load_from_csvs

    parser = argparse.ArgumentParser(description="Load CSV outputs into MySQL.")
    parser.add_argument("--host", default=os.getenv("MYSQL_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("MYSQL_USER", "root"))
    parser.add_argument("--password", default=os.getenv("MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("MYSQL_DATABASE", "crimemap"))
    parser.add_argument("--schema", default=os.path.join("db", "schema.sql"))
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
        "--gap-report",
        default=os.path.join("outputs", "police_api_gap_report.csv"),
        help="CSV path for the police API gap report.",
    )
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--skip-events", action="store_true")
    parser.add_argument("--skip-ward-analysis", action="store_true")
    parser.add_argument("--skip-ward-trends", action="store_true")
    parser.add_argument("--skip-gap", action="store_true")

    args = parser.parse_args()

    total_rows = load_from_csvs(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        schema_path=args.schema,
        crime_events_path=args.crime_events,
        ward_analysis_path=args.ward_analysis,
        ward_trends_path=args.ward_crime_type_trends,
        gap_report_path=args.gap_report,
        batch_size=args.batch_size,
        skip_events=args.skip_events,
        skip_ward_analysis=args.skip_ward_analysis,
        skip_ward_trends=args.skip_ward_trends,
        skip_gap=args.skip_gap,
        source="csv_load",
    )

    print(f"Loaded {total_rows} rows into MySQL.")


if __name__ == "__main__":
    main()
