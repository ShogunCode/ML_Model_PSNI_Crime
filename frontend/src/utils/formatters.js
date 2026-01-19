export const formatNumber = (value) => {
  if (value === null || value === undefined) return "N/A";
  if (Number.isNaN(value)) return "N/A";
  return new Intl.NumberFormat().format(value);
};

export const formatRate = (value) => {
  if (value === null || value === undefined) return "N/A";
  if (Number.isNaN(value)) return "N/A";
  return `${Number(value).toFixed(2)}`;
};

export const formatPercent = (value) => {
  if (value === null || value === undefined) return "N/A";
  if (Number.isNaN(value)) return "N/A";
  const num = Number(value);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(1)}%`;
};
