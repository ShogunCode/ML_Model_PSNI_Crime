import os

import pandas as pd

from analytics import (
    attach_wards,
    crime_type_change_between_halves,
    crime_type_trend_metrics,
    filter_by_month_range,
    ward_change_between_halves,
    ward_counts,
    ward_trend_metrics,
)
from ingest_psni import ingest_psni_month
from hotspot_map import build_hotspot_map
from trend_report import build_trend_report


DEFAULT_HISTORY_PATH = os.path.join("data", "processed", "crime_history.csv")
DEFAULT_OUTPUT_MAP = os.path.join("outputs", "ward_hotspots_map.html")
DEFAULT_OUTPUT_CSV = os.path.join("outputs", "ward_hotspots_summary.csv")
DEFAULT_SHAPEFILE = os.path.join(
    "data",
    "raw",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012)",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012).shp",
)


def _prompt_time_window(default_months=24):
    print("Select time window:")
    print("1) Last N months")
    print("2) Start/End months")
    choice = input("Choice [1]: ").strip() or "1"

    if choice == "2":
        start = input("Start month (YYYY-MM): ").strip()
        end = input("End month (YYYY-MM): ").strip()
        return {"start_month": start or None, "end_month": end or None, "months_back": None}

    months_raw = input(f"Months back [{default_months}]: ").strip()
    months_back = int(months_raw) if months_raw else default_months
    return {"start_month": None, "end_month": None, "months_back": months_back}


def _load_filtered_history(history_path, window):
    if not os.path.exists(history_path):
        raise FileNotFoundError(f"Missing history file at {history_path}")
    history = pd.read_csv(history_path)
    history = history.dropna(subset=["Longitude", "Latitude", "Month"])
    return filter_by_month_range(
        history,
        start_month=window["start_month"],
        end_month=window["end_month"],
        months_back=window["months_back"],
    )


def _show_top_wards(history, shapefile_path, top_n=10):
    joined, _ = attach_wards(history, shapefile_path)
    counts = ward_counts(joined).sort_values("CrimeCount", ascending=False).head(top_n)
    print(counts.to_string(index=False))


def _show_largest_drops(history, shapefile_path, top_n=10):
    joined, _ = attach_wards(history, shapefile_path)
    changes = ward_change_between_halves(joined)
    drops = changes.sort_values("Change", ascending=True).head(top_n)
    print(drops.to_string(index=False))


def _show_crime_type_jumps(history, top_n=10):
    changes = crime_type_change_between_halves(history)
    jumps = changes.sort_values("Change", ascending=False).head(top_n)
    print(jumps.to_string(index=False))


def _show_trend_summary(history, shapefile_path, top_n=10):
    joined, _ = attach_wards(history, shapefile_path)
    ward_trends = ward_trend_metrics(joined)
    crime_type_trends = crime_type_trend_metrics(history)

    ward_rising = ward_trends.sort_values("TrendChange", ascending=False).head(top_n)
    ward_falling = ward_trends.sort_values("TrendChange", ascending=True).head(top_n)
    type_rising = crime_type_trends.sort_values("TrendChange", ascending=False).head(top_n)

    print("\nTop rising wards:")
    print(ward_rising.to_string(index=False))
    print("\nTop falling wards:")
    print(ward_falling.to_string(index=False))
    print("\nTop rising crime types:")
    print(type_rising.to_string(index=False))


def main():
    while True:
        print("\nCrime Data CLI")
        print("1) Ingest latest month")
        print("2) Build hotspot map")
        print("3) Show highest crime wards")
        print("4) Show largest drops in crime by ward")
        print("5) Show biggest jumps by crime type")
        print("6) Generate trend report (CSV)")
        print("7) Show trend summary")
        print("8) Exit")
        choice = input("Select an option: ").strip()

        if choice == "1":
            output_path, row_count = ingest_psni_month(
                date_str=None,
                output_path=os.path.join("data", "raw", "crime_data.csv"),
                archive_url="https://data.police.uk/data/archive/{date}.zip",
                history_path=DEFAULT_HISTORY_PATH,
            )
            print(f"Wrote {row_count} rows to {output_path}")
        elif choice == "2":
            window = _prompt_time_window()
            output_map, output_csv, row_count = build_hotspot_map(
                DEFAULT_HISTORY_PATH,
                DEFAULT_SHAPEFILE,
                DEFAULT_OUTPUT_MAP,
                DEFAULT_OUTPUT_CSV,
                start_month=window["start_month"],
                end_month=window["end_month"],
                months_back=window["months_back"],
                simplify_tolerance=0.0005,
            )
            print(f"Using {row_count} rows for the map.")
            print(f"Wrote {output_csv}")
            print(f"Wrote {output_map}")
        elif choice == "3":
            window = _prompt_time_window()
            history = _load_filtered_history(DEFAULT_HISTORY_PATH, window)
            _show_top_wards(history, DEFAULT_SHAPEFILE)
        elif choice == "4":
            window = _prompt_time_window()
            history = _load_filtered_history(DEFAULT_HISTORY_PATH, window)
            _show_largest_drops(history, DEFAULT_SHAPEFILE)
        elif choice == "5":
            window = _prompt_time_window()
            history = _load_filtered_history(DEFAULT_HISTORY_PATH, window)
            _show_crime_type_jumps(history)
        elif choice == "6":
            window = _prompt_time_window()
            months_back = window["months_back"]
            if window["start_month"] or window["end_month"]:
                months_back = None
            ward_output, type_output, row_count = build_trend_report(
                DEFAULT_HISTORY_PATH,
                DEFAULT_SHAPEFILE,
                os.path.join("outputs", "ward_trends.csv"),
                os.path.join("outputs", "crime_type_trends.csv"),
                start_month=window["start_month"],
                end_month=window["end_month"],
                months_back=months_back,
                simplify_tolerance=0.0005,
            )
            print(f"Using {row_count} rows for the report.")
            print(f"Wrote {ward_output}")
            print(f"Wrote {type_output}")
        elif choice == "7":
            window = _prompt_time_window()
            history = _load_filtered_history(DEFAULT_HISTORY_PATH, window)
            _show_trend_summary(history, DEFAULT_SHAPEFILE)
        elif choice == "8":
            break
        else:
            print("Invalid selection.")


if __name__ == "__main__":
    main()
