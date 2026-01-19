import numpy as np
import pandas as pd
import geopandas as gpd

HARM_KEYWORDS = {
    "violence": 10,
    "violent": 10,
    "assault": 10,
    "murder": 10,
    "homicide": 10,
    "robbery": 10,
    "burglary": 5,
    "theft": 1,
    "shoplifting": 1,
}


def filter_by_month_range(df, start_month=None, end_month=None, months_back=None):
    df = df.copy()
    if pd.api.types.is_datetime64_any_dtype(df["Month"]):
        df["Month"] = pd.to_datetime(df["Month"], errors="coerce")
    else:
        df["Month"] = pd.to_datetime(df["Month"] + "-01", errors="coerce")
    df = df.dropna(subset=["Month"])

    if months_back is not None:
        latest = df["Month"].max()
        if pd.isna(latest):
            return df.iloc[0:0]
        start_month = latest - pd.DateOffset(months=months_back - 1)
        end_month = latest

    if start_month:
        start = pd.to_datetime(f"{start_month}-01", errors="coerce")
        if not pd.isna(start):
            df = df[df["Month"] >= start]

    if end_month:
        end = pd.to_datetime(f"{end_month}-01", errors="coerce")
        if not pd.isna(end):
            df = df[df["Month"] <= end]

    return df


def attach_wards(df, shapefile_path, simplify_tolerance=None):
    wards = gpd.read_file(shapefile_path)
    wards = wards.rename(columns={"WardCode": "WardCode_w"})

    if simplify_tolerance:
        wards["geometry"] = wards["geometry"].simplify(simplify_tolerance)

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["Longitude"], df["Latitude"]),
        crs="EPSG:4326",
    )
    if gdf.crs is not None:
        wards = wards.to_crs(gdf.crs)

    joined = gpd.sjoin(gdf, wards, how="left", predicate="within")
    return joined, wards


def ward_counts(joined):
    counts = (
        joined.groupby(["WardCode_w", "WARDNAME"], observed=True)
        .size()
        .reset_index(name="CrimeCount")
    )
    return counts


def ward_change_between_halves(joined):
    months = sorted(joined["Month"].dropna().unique())
    if len(months) < 2:
        return pd.DataFrame(columns=["WardCode_w", "WARDNAME", "Change"])

    mid = len(months) // 2
    first_months = months[:mid]
    second_months = months[mid:]

    first = joined[joined["Month"].isin(first_months)]
    second = joined[joined["Month"].isin(second_months)]

    first_counts = ward_counts(first).set_index(["WardCode_w", "WARDNAME"])
    second_counts = ward_counts(second).set_index(["WardCode_w", "WARDNAME"])

    combined = first_counts.join(
        second_counts, how="outer", lsuffix="_first", rsuffix="_second"
    ).fillna(0)
    combined["Change"] = combined["CrimeCount_second"] - combined["CrimeCount_first"]

    result = combined.reset_index()[["WardCode_w", "WARDNAME", "Change"]]
    return result


def crime_type_change_between_halves(df):
    months = sorted(df["Month"].dropna().unique())
    if len(months) < 2:
        return pd.DataFrame(columns=["Crime type", "Change"])

    mid = len(months) // 2
    first_months = months[:mid]
    second_months = months[mid:]

    first = df[df["Month"].isin(first_months)]
    second = df[df["Month"].isin(second_months)]

    first_counts = (
        first.groupby("Crime type", observed=True).size().reset_index(name="Count_first")
    ).set_index("Crime type")
    second_counts = (
        second.groupby("Crime type", observed=True).size().reset_index(name="Count_second")
    ).set_index("Crime type")

    combined = first_counts.join(second_counts, how="outer").fillna(0)
    combined["Change"] = combined["Count_second"] - combined["Count_first"]
    return combined.reset_index()[["Crime type", "Change"]]


def monthly_counts(df, group_fields, full_months=None, max_cartesian_size=5_000_000):
    df = df.copy()
    df["Month"] = pd.to_datetime(df["Month"], errors="coerce")
    df = df.dropna(subset=["Month"])
    df["MonthPeriod"] = df["Month"].dt.to_period("M").dt.to_timestamp()
    counts = (
        df.groupby(group_fields + ["MonthPeriod"], observed=True)
        .size()
        .reset_index(name="Count")
    )
    if full_months is None or counts.empty:
        return counts

    months = pd.to_datetime(full_months)
    months = months.dropna().unique()
    if len(months) == 0:
        return counts

    groups = counts[group_fields].drop_duplicates()
    if len(groups) * len(months) > max_cartesian_size:
        return counts
    groups = groups.assign(_key=1)
    month_df = pd.DataFrame({"MonthPeriod": months})
    month_df = month_df.assign(_key=1)
    expanded = groups.merge(month_df, on="_key").drop(columns=["_key"])
    counts = expanded.merge(counts, on=group_fields + ["MonthPeriod"], how="left")
    counts["Count"] = counts["Count"].fillna(0).astype(int)
    return counts


def _full_month_range(series):
    if isinstance(series, pd.Series) and pd.api.types.is_categorical_dtype(series):
        series = series.astype(str)
    months = pd.to_datetime(series, errors="coerce")
    months = months.dropna()
    if months.empty:
        return None
    start = months.min().to_period("M").to_timestamp()
    end = months.max().to_period("M").to_timestamp()
    return pd.date_range(start, end, freq="MS")


def _lookup_population(key, group_fields, population_by_code):
    if not population_by_code:
        return None
    if "WardCode_w" in group_fields:
        idx = group_fields.index("WardCode_w")
    elif "WardCode" in group_fields:
        idx = group_fields.index("WardCode")
    else:
        return None
    if isinstance(key, tuple):
        code = key[idx]
    else:
        code = key
    return population_by_code.get(code)


def _trend_metrics(
    monthly_df,
    group_fields,
    population_by_code=None,
    rate_per=1000,
    window=3,
    full_months=None,
):
    results = []
    months = None
    if full_months is not None:
        months = pd.to_datetime(full_months)
        months = months.dropna().unique()
        if len(months) == 0:
            months = None
        else:
            months = np.sort(months)
    for key, group in monthly_df.groupby(group_fields, observed=True):
        group = group.sort_values("MonthPeriod")
        if months is not None:
            group = (
                group.set_index("MonthPeriod")
                .reindex(months, fill_value=0)
                .rename_axis("MonthPeriod")
                .reset_index()
            )
        counts = group["Count"].to_numpy(dtype=float)
        trend_values = counts
        population = _lookup_population(key, group_fields, population_by_code)
        if population is not None:
            try:
                population = float(population)
            except (TypeError, ValueError):
                population = None
        if population and population > 0:
            trend_values = (counts / population) * rate_per

        x = np.arange(len(trend_values))
        slope = float(np.polyfit(x, trend_values, 1)[0]) if len(trend_values) >= 2 else 0.0
        total = int(counts.sum()) if len(counts) else 0
        avg = float(counts.mean()) if len(counts) else 0.0

        span = min(window, len(trend_values))
        if span:
            first_avg = float(np.mean(trend_values[:span]))
            last_avg = float(np.mean(trend_values[-span:]))
        else:
            first_avg = 0.0
            last_avg = 0.0
        change = last_avg - first_avg
        pct_change = (change / first_avg * 100.0) if first_avg > 0 else 0.0

        if isinstance(key, tuple):
            row = list(key)
        else:
            row = [key]

        row.extend([total, avg, change, pct_change, slope, len(counts)])
        results.append(row)

    columns = (
        group_fields
        + [
            "TotalCrimes",
            "AvgMonthly",
            "TrendChange",
            "TrendPct",
            "TrendSlope",
            "Months",
        ]
    )
    return pd.DataFrame(results, columns=columns)


def _harm_weight(crime_type):
    text = str(crime_type).lower()
    for keyword, score in HARM_KEYWORDS.items():
        if keyword in text:
            return score
    return 1


def _yoy_change(monthly_df, group_fields):
    if monthly_df.empty:
        return pd.DataFrame(columns=group_fields + ["YoYCurrent", "YoYPrior", "YoYChange"])

    latest_month = monthly_df["MonthPeriod"].max()
    if pd.isna(latest_month):
        return pd.DataFrame(columns=group_fields + ["YoYCurrent", "YoYPrior", "YoYChange"])

    prior_month = latest_month - pd.DateOffset(years=1)
    current = monthly_df[monthly_df["MonthPeriod"] == latest_month]
    prior = monthly_df[monthly_df["MonthPeriod"] == prior_month]

    current = current[group_fields + ["Count"]].rename(columns={"Count": "YoYCurrent"})
    prior = prior[group_fields + ["Count"]].rename(columns={"Count": "YoYPrior"})

    merged = current.merge(prior, on=group_fields, how="left")
    merged["YoYChange"] = pd.NA
    valid = merged["YoYPrior"] > 0
    merged.loc[valid, "YoYChange"] = (
        (merged.loc[valid, "YoYCurrent"] - merged.loc[valid, "YoYPrior"])
        / merged.loc[valid, "YoYPrior"]
        * 100.0
    )
    return merged


def ward_trend_metrics(joined, population_by_code=None, rate_per=1000, full_months=None):
    monthly = monthly_counts(joined, ["WardCode_w", "WARDNAME"], full_months=full_months)
    return _trend_metrics(
        monthly,
        ["WardCode_w", "WARDNAME"],
        population_by_code=population_by_code,
        rate_per=rate_per,
        full_months=full_months,
    )


def crime_type_trend_metrics(df, full_months=None):
    monthly = monthly_counts(df, ["Crime type"], full_months=full_months)
    return _trend_metrics(monthly, ["Crime type"], full_months=full_months)


def ward_rate_analysis(wards_df, high_rate_quantile=0.8):
    dedupe_cols = []
    if "WardCode_w" in wards_df.columns:
        dedupe_cols = ["WardCode_w", "WARDNAME"]
    elif "WardCode" in wards_df.columns:
        dedupe_cols = ["WardCode", "WARDNAME"]
    if dedupe_cols:
        wards_df = wards_df.drop_duplicates(subset=dedupe_cols)

    columns = [
        "WardCode_w",
        "WARDNAME",
        "Population",
        "NumberOfCrimes",
        "CrimeRatePer100kPeople",
    ]
    available = [col for col in columns if col in wards_df.columns]
    analysis = wards_df[available].copy()

    if "WardCode_w" in analysis.columns:
        analysis = analysis.rename(columns={"WardCode_w": "WardCode"})

    rate_col = "CrimeRatePer100kPeople"
    if rate_col not in analysis.columns:
        analysis[rate_col] = pd.NA

    rates = pd.to_numeric(analysis[rate_col], errors="coerce")
    if rates.notna().any():
        analysis["RatePercentile"] = rates.rank(pct=True)
        analysis["RateRank"] = rates.rank(ascending=False, method="min").astype("Int64")
        threshold = rates.quantile(high_rate_quantile)
        analysis["HighCrimeRate"] = rates >= threshold
    else:
        analysis["RatePercentile"] = pd.NA
        analysis["RateRank"] = pd.NA
        analysis["HighCrimeRate"] = False

    return analysis


def build_ward_analysis(wards_df, crime_df, high_rate_quantile=0.8):
    analysis = ward_rate_analysis(wards_df, high_rate_quantile=high_rate_quantile)

    if crime_df is None:
        return analysis

    if "Month" not in crime_df.columns:
        return analysis

    required = ["WardCode", "WARDNAME", "Month"]
    if not set(required).issubset(crime_df.columns):
        return analysis

    trend_input = crime_df[required].copy()
    trend_input = trend_input.dropna(subset=["WardCode", "WARDNAME", "Month"])
    if trend_input.empty:
        return analysis

    trend_input = trend_input.rename(columns={"WardCode": "WardCode_w"})
    population_by_code = {}
    if "WardCode" in analysis.columns and "Population" in analysis.columns:
        population_by_code = (
            analysis[["WardCode", "Population"]]
            .dropna(subset=["WardCode"])
            .set_index("WardCode")["Population"]
            .to_dict()
        )
    full_months = _full_month_range(trend_input["Month"])
    trends = ward_trend_metrics(
        trend_input,
        population_by_code=population_by_code,
        rate_per=1000,
        full_months=full_months,
    )
    monthly = monthly_counts(trend_input, ["WardCode_w", "WARDNAME"], full_months=full_months)
    spans = (
        monthly.groupby(["WardCode_w", "WARDNAME"], observed=True)["MonthPeriod"]
        .agg(["min", "max"])
        .reset_index()
        .rename(columns={"min": "FirstMonth", "max": "LastMonth"})
    )
    yoy = _yoy_change(monthly, ["WardCode_w", "WARDNAME"])
    trends = trends.merge(spans, on=["WardCode_w", "WARDNAME"], how="left")
    for col in ("FirstMonth", "LastMonth"):
        if col in trends.columns:
            trends[col] = trends[col].dt.strftime("%Y-%m")

    trends = trends.rename(columns={"WardCode_w": "WardCode"})
    analysis = analysis.merge(trends, on=["WardCode", "WARDNAME"], how="left")
    if not yoy.empty:
        yoy = yoy.rename(columns={"WardCode_w": "WardCode"})
        analysis = analysis.merge(yoy, on=["WardCode", "WARDNAME"], how="left")
    else:
        analysis["YoYCurrent"] = pd.NA
        analysis["YoYPrior"] = pd.NA
        analysis["YoYChange"] = pd.NA
    if "TotalCrimes" in analysis.columns:
        total = pd.to_numeric(analysis["TotalCrimes"], errors="coerce")
    else:
        total = pd.Series([pd.NA] * len(analysis), index=analysis.index)
    if "Months" in analysis.columns:
        months = pd.to_numeric(analysis["Months"], errors="coerce")
    else:
        months = pd.Series([pd.NA] * len(analysis), index=analysis.index)
    if "Population" in analysis.columns:
        population = pd.to_numeric(analysis["Population"], errors="coerce")
    else:
        population = pd.Series([pd.NA] * len(analysis), index=analysis.index)
    annualized = (total / months) * 12
    annualized_rate = (annualized / population) * 100000
    annualized_rate = annualized_rate.where((months > 0) & (population > 0))
    analysis["AnnualizedCrimeRatePer100k"] = annualized_rate
    analysis["CrimeRatePer100kPeople"] = annualized_rate

    rates = pd.to_numeric(analysis["CrimeRatePer100kPeople"], errors="coerce")
    if rates.notna().any():
        analysis["RatePercentile"] = rates.rank(pct=True)
        analysis["RateRank"] = rates.rank(ascending=False, method="min").astype("Int64")
        threshold = rates.quantile(high_rate_quantile)
        analysis["HighCrimeRate"] = rates >= threshold
    else:
        analysis["RatePercentile"] = pd.NA
        analysis["RateRank"] = pd.NA
        analysis["HighCrimeRate"] = False

    if "Crime type" in crime_df.columns:
        harm_input = crime_df[["WardCode", "WARDNAME", "Crime type"]].copy()
        harm_input = harm_input.dropna(subset=["WardCode", "WARDNAME", "Crime type"])
        if not harm_input.empty:
            harm_input["HarmWeight"] = harm_input["Crime type"].apply(_harm_weight)
            harm_totals = (
                harm_input.groupby(["WardCode", "WARDNAME"], observed=True)["HarmWeight"]
                .sum()
                .reset_index()
                .rename(columns={"HarmWeight": "TotalHarm"})
            )
            analysis = analysis.merge(harm_totals, on=["WardCode", "WARDNAME"], how="left")
        else:
            analysis["TotalHarm"] = pd.NA
    else:
        analysis["TotalHarm"] = pd.NA

    total_harm = pd.to_numeric(analysis["TotalHarm"], errors="coerce").fillna(0)
    analysis["TotalHarm"] = total_harm
    harm_annualized = (total_harm / months) * 12
    harm_score = (harm_annualized / population) * 100000
    harm_score = harm_score.where((months > 0) & (population > 0))
    analysis["HarmScorePer100k"] = harm_score
    return analysis


def build_ward_crime_type_trends(crime_df, population_by_code=None):
    required = ["WardCode", "WARDNAME", "Crime type", "Month"]
    if crime_df is None or not set(required).issubset(crime_df.columns):
        return pd.DataFrame()

    trend_input = crime_df[required].copy()
    trend_input = trend_input.dropna(subset=["WardCode", "WARDNAME", "Crime type", "Month"])
    if trend_input.empty:
        return pd.DataFrame()

    full_months = _full_month_range(trend_input["Month"])
    monthly = monthly_counts(
        trend_input,
        ["WardCode", "WARDNAME", "Crime type"],
        full_months=full_months,
    )
    trends = _trend_metrics(
        monthly,
        ["WardCode", "WARDNAME", "Crime type"],
        population_by_code=population_by_code,
        rate_per=1000,
        full_months=full_months,
    )

    spans = (
        monthly.groupby(["WardCode", "WARDNAME", "Crime type"], observed=True)["MonthPeriod"]
        .agg(["min", "max"])
        .reset_index()
        .rename(columns={"min": "FirstMonth", "max": "LastMonth"})
    )
    trends = trends.merge(spans, on=["WardCode", "WARDNAME", "Crime type"], how="left")
    for col in ("FirstMonth", "LastMonth"):
        if col in trends.columns:
            trends[col] = trends[col].dt.strftime("%Y-%m")

    if "TrendSlope" in trends.columns:
        trend_dir = pd.cut(
            trends["TrendSlope"],
            bins=[-float("inf"), -1e-6, 1e-6, float("inf")],
            labels=["negative", "flat", "positive"],
        )
        trends["TrendDirection"] = trend_dir.astype(str)

    return trends
