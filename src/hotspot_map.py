import argparse
import os

import folium
import pandas as pd

from analytics import attach_wards, filter_by_month_range


DEFAULT_HISTORY_PATH = os.path.join("data", "processed", "crime_history.csv")
DEFAULT_SHAPEFILE = os.path.join(
    "data",
    "raw",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012)",
    "OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012).shp",
)
DEFAULT_OUTPUT_MAP = os.path.join("outputs", "ward_hotspots_map.html")
DEFAULT_OUTPUT_CSV = os.path.join("outputs", "ward_hotspots_summary.csv")


def _aggregate_by_ward(df, shapefile_path, simplify_tolerance=None):
    joined, wards = attach_wards(df, shapefile_path, simplify_tolerance)
    counts = joined.groupby("WardCode_w").size().reset_index(name="CrimeCount")
    wards_with_counts = wards.merge(counts, on="WardCode_w", how="left")
    wards_with_counts["CrimeCount"] = wards_with_counts["CrimeCount"].fillna(0).astype(int)
    return wards_with_counts


def build_hotspot_map(
    history_path,
    shapefile_path,
    output_map_path,
    output_csv_path,
    start_month=None,
    end_month=None,
    months_back=None,
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

    wards_with_counts = _aggregate_by_ward(
        history,
        shapefile_path,
        simplify_tolerance=simplify_tolerance,
    )

    os.makedirs(os.path.dirname(output_map_path), exist_ok=True)
    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    wards_with_counts[["WardCode_w", "WARDNAME", "CrimeCount"]].to_csv(
        output_csv_path, index=False
    )

    mean_lat = wards_with_counts.geometry.centroid.y.mean()
    mean_lon = wards_with_counts.geometry.centroid.x.mean()
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=8, tiles="CartoDB Positron")

    folium.Choropleth(
        geo_data=wards_with_counts,
        data=wards_with_counts,
        columns=["WardCode_w", "CrimeCount"],
        key_on="feature.properties.WardCode_w",
        fill_color="YlOrRd",
        fill_opacity=0.7,
        line_opacity=0.2,
        nan_fill_color="white",
        legend_name="Crime Count",
    ).add_to(m)

    tooltip = folium.GeoJsonTooltip(fields=["WARDNAME", "CrimeCount"])
    folium.GeoJson(
        wards_with_counts,
        style_function=lambda x: {"fillOpacity": 0, "color": "transparent"},
        tooltip=tooltip,
    ).add_to(m)

    m.save(output_map_path)
    return output_map_path, output_csv_path, len(history)


def main():
    parser = argparse.ArgumentParser(
        description="Build an efficient ward-level hotspot map from historical crime data."
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
        "--output-map",
        default=DEFAULT_OUTPUT_MAP,
        help=f"Output map path. Defaults to {DEFAULT_OUTPUT_MAP}",
    )
    parser.add_argument(
        "--output-csv",
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output summary CSV path. Defaults to {DEFAULT_OUTPUT_CSV}",
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
    output_map, output_csv, row_count = build_hotspot_map(
        args.history,
        args.shapefile,
        args.output_map,
        args.output_csv,
        start_month=args.start,
        end_month=args.end,
        months_back=months_back,
        simplify_tolerance=args.simplify,
    )
    print(f"Using {row_count} rows for the map.")
    print(f"Wrote {output_csv}")
    print(f"Wrote {output_map}")


if __name__ == "__main__":
    main()
