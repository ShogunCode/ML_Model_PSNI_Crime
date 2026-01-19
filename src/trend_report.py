import argparse
import os

import pandas as pd

from analytics import (
    attach_wards,
    crime_type_trend_metrics,
    filter_by_month_range,
    ward_trend_metrics,
)


DEFAULT_HISTORY_PATH = os.path.join("data", "processed", "crime_history.csv")
DEFAULT_SHAPEFILE = os.path.join(
    "data",
    "raw",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012)",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012).shp",
)
DEFAULT_WARD_OUTPUT = os.path.join("outputs", "ward_trends.csv")
DEFAULT_TYPE_OUTPUT = os.path.join("outputs", "crime_type_trends.csv")


def build_trend_report(
    history_path,
    shapefile_path,
    ward_output,
    type_output,
    start_month=None,
    end_month=None,
    months_back=24,
    simplify_tolerance=None,
):
    history = pd.read_csv(history_path)
    history = history.dropna(subset=["Longitude", "Latitude", "Month"])
    history = filter_by_month_range(
        history,
        start_month=start_month,
        end_month=end_month,
        months_back=months_back,
    )

    joined, _ = attach_wards(history, shapefile_path, simplify_tolerance)
    ward_trends = ward_trend_metrics(joined)
    crime_type_trends = crime_type_trend_metrics(history)

    os.makedirs(os.path.dirname(ward_output), exist_ok=True)
    os.makedirs(os.path.dirname(type_output), exist_ok=True)
    ward_trends.to_csv(ward_output, index=False)
    crime_type_trends.to_csv(type_output, index=False)

    return ward_output, type_output, len(history)


def main():
    parser = argparse.ArgumentParser(
        description="Generate trend reports for wards and crime types."
    )
    parser.add_argument(
        "--history",
        default=DEFAULT_HISTORY_PATH,
        help=f"Historical CSV path. Defaults to {DEFAULT_HISTORY_PATH}",
    )
    parser.add_argument(
        "--shapefile",
        default=DEFAULT_SHAPEFILE,
        help=f"Ward shapefile path. Defaults to {DEFAULT_SHAPEFILE}",
    )
    parser.add_argument(
        "--ward-output",
        default=DEFAULT_WARD_OUTPUT,
        help=f"Ward trend output CSV. Defaults to {DEFAULT_WARD_OUTPUT}",
    )
    parser.add_argument(
        "--type-output",
        default=DEFAULT_TYPE_OUTPUT,
        help=f"Crime type trend output CSV. Defaults to {DEFAULT_TYPE_OUTPUT}",
    )
    parser.add_argument(
        "--start",
        help="Start month in YYYY-MM format (inclusive).",
    )
    parser.add_argument(
        "--end",
        help="End month in YYYY-MM format (inclusive).",
    )
    parser.add_argument(
        "--months-back",
        type=int,
        default=24,
        help="Use the latest N months instead of explicit start/end.",
    )
    parser.add_argument(
        "--simplify",
        type=float,
        default=0.0005,
        help="Geometry simplification tolerance (in CRS units).",
    )

    args = parser.parse_args()
    months_back = args.months_back
    if args.start or args.end:
        months_back = None

    ward_output, type_output, row_count = build_trend_report(
        args.history,
        args.shapefile,
        args.ward_output,
        args.type_output,
        start_month=args.start,
        end_month=args.end,
        months_back=months_back,
        simplify_tolerance=args.simplify,
    )
    print(f"Using {row_count} rows for the report.")
    print(f"Wrote {ward_output}")
    print(f"Wrote {type_output}")


if __name__ == "__main__":
    main()
