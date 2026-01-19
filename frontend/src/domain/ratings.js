export const RATING_RATE_WEIGHT = 0.7;
export const RATING_TREND_WEIGHT = 0.3;
export const TREND_UP_THRESHOLD = 0.05;
export const TREND_DOWN_THRESHOLD = -0.05;

export const normalizePercentile = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  if (num > 1) return num / 100;
  return num;
};

export const ratingScore = (
  ratePercentile,
  trendPercentile,
  rateWeight = RATING_RATE_WEIGHT,
  trendWeight = RATING_TREND_WEIGHT
) => {
  const rate = normalizePercentile(ratePercentile);
  if (rate === null) return null;
  const trend = normalizePercentile(trendPercentile) ?? 0;
  return Number(((rateWeight * rate + trendWeight * trend) * 100).toFixed(1));
};

export const ratingBand = (score) => {
  if (score === null || score === undefined) return "Unknown";
  if (score >= 85) return "High";
  if (score >= 70) return "Elevated";
  if (score >= 55) return "Watch";
  return "Stable";
};

export const trendDirection = (
  slope,
  upThreshold = TREND_UP_THRESHOLD,
  downThreshold = TREND_DOWN_THRESHOLD
) => {
  if (slope === null || slope === undefined) return "flat";
  if (slope > upThreshold) return "up";
  if (slope < downThreshold) return "down";
  return "flat";
};

export const bandClass = (band) => {
  const key = String(band || "").toLowerCase();
  if (key === "all") return "band-all";
  if (key === "high") return "band-high";
  if (key === "elevated") return "band-elevated";
  if (key === "watch") return "band-watch";
  if (key === "stable") return "band-stable";
  return "band-unknown";
};
