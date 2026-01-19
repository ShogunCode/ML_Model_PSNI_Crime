import bisect

RATING_RATE_WEIGHT = 0.7
RATING_TREND_WEIGHT = 0.3
TREND_UP_THRESHOLD = 0.05
TREND_DOWN_THRESHOLD = -0.05


def percentile(values, value):
    if value is None or not values:
        return None
    idx = bisect.bisect_right(values, value)
    return idx / len(values)


def normalize_percentile(value):
    if value in (None, ""):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num > 1:
        return num / 100.0
    return num


def rating_score(
    rate_percentile,
    trend_percentile,
    rate_weight=RATING_RATE_WEIGHT,
    trend_weight=RATING_TREND_WEIGHT,
):
    rate = normalize_percentile(rate_percentile)
    if rate is None:
        return None
    trend = normalize_percentile(trend_percentile) or 0.0
    score = (rate_weight * rate) + (trend_weight * trend)
    return round(score * 100, 1)


def rating_band(score):
    if score is None:
        return "Unknown"
    if score >= 85:
        return "High"
    if score >= 70:
        return "Elevated"
    if score >= 55:
        return "Watch"
    return "Stable"


def trend_direction(
    slope,
    up_threshold=TREND_UP_THRESHOLD,
    down_threshold=TREND_DOWN_THRESHOLD,
):
    if slope is None:
        return "flat"
    if slope > up_threshold:
        return "up"
    if slope < down_threshold:
        return "down"
    return "flat"
