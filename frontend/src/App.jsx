import { useEffect, useMemo, useState } from "react";
import { api, assets, API_BASE } from "./api/client.js";
import StatusMessage from "./components/StatusMessage.jsx";
import { bandClass } from "./domain/ratings.js";
import { formatNumber, formatPercent, formatRate } from "./utils/formatters.js";

const buildSparklinePoints = (values, width = 80, height = 24, padding = 2) => {
  if (!values || values.length < 2) return "";
  const numeric = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (numeric.length < 2) return "";
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  const range = max - min || 1;
  const step = (width - padding * 2) / (values.length - 1);
  return values
    .map((value, idx) => {
      const val = Number(value);
      const safe = Number.isFinite(val) ? val : min;
      const x = padding + step * idx;
      const y =
        height -
        padding -
        ((safe - min) / range) * (height - padding * 2);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
};

const SERIES_VIEWBOX = { width: 520, height: 160, padding: 12 };

const buildSeriesPath = (points, width = 520, height = 160, padding = 12) => {
  if (!points || points.length < 2) return "";
  const values = points
    .map((point) => Number(point.value))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = (width - padding * 2) / (points.length - 1);
  return points
    .map((point, idx) => {
      const value = Number(point.value);
      const safe = Number.isFinite(value) ? value : min;
      const x = padding + step * idx;
      const y =
        height - padding - ((safe - min) / range) * (height - padding * 2);
      return `${idx === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
};

const buildSeriesCoordinates = (
  points,
  width = SERIES_VIEWBOX.width,
  height = SERIES_VIEWBOX.height,
  padding = SERIES_VIEWBOX.padding
) => {
  if (!points || points.length < 2) return [];
  const values = points
    .map((point) => Number(point.value))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = (width - padding * 2) / (points.length - 1);
  return points.map((point, idx) => {
    const raw = Number(point.value);
    const safe = Number.isFinite(raw) ? raw : min;
    const x = padding + step * idx;
    const y = height - padding - ((safe - min) / range) * (height - padding * 2);
    return {
      month: point.month,
      value: Number.isFinite(raw) ? raw : null,
      x,
      y,
    };
  });
};

const partyLogoFor = (party) => {
  if (!party) return null;
  const text = party.toLowerCase();
  if (text.includes("sinn")) {
    return { src: "/party_logos/sinn_fein.svg", alt: "Sinn Fein" };
  }
  if (text.includes("democratic unionist") || /\bdup\b/.test(text)) {
    return { src: "/party_logos/dup.svg", alt: "Democratic Unionist Party" };
  }
  if (text.includes("ulster unionist") || /\buup\b/.test(text)) {
    return { src: "/party_logos/uup.svg", alt: "Ulster Unionist Party" };
  }
  if (text.includes("social democratic") || text.includes("sdlp")) {
    return { src: "/party_logos/sdlp.svg", alt: "SDLP" };
  }
  if (text.includes("alliance")) {
    return { src: "/party_logos/alliance.svg", alt: "Alliance" };
  }
  if (text.includes("people before profit")) {
    return { src: "/party_logos/pbp.svg", alt: "People Before Profit" };
  }
  if (text.includes("traditional unionist") || /\btuv\b/.test(text)) {
    return { src: "/party_logos/tuv.svg", alt: "Traditional Unionist Voice" };
  }
  if (text.includes("independent") || text.includes("other")) {
    return { src: "/party_logos/independent.svg", alt: "Independent" };
  }
  return null;
};

const InfoTip = ({ text }) => (
  <span className="info-icon" tabIndex="0" aria-label="More info">
    i
    <span className="info-tooltip">{text}</span>
  </span>
);

const resolveMapUrl = (url) => {
  if (!url) return "";
  if (url.startsWith("http")) return url;
  return `${API_BASE}${url}`;
};

export default function App() {
  const [activeView, setActiveView] = useState("overview");
  const [summary, setSummary] = useState(null);
  const [gapReport, setGapReport] = useState(null);
  const [opsStatus, setOpsStatus] = useState(null);
  const [opsQuality, setOpsQuality] = useState(null);
  const [opsJobs, setOpsJobs] = useState([]);
  const [mapInfo, setMapInfo] = useState({
    wards_map_url: assets.wardsMapUrl,
    exists: true,
  });
  const [alertRules, setAlertRules] = useState([]);
  const [alertEvents, setAlertEvents] = useState([]);
  const [alertLoading, setAlertLoading] = useState(false);
  const [alertError, setAlertError] = useState(null);
  const [newAlert, setNewAlert] = useState({
    name: "",
    rule_type: "ward",
    ward_code: "",
    metric: "rating_band",
    operator: "eq",
    threshold: "High",
    trigger_on: "enter",
    notify_emails: "",
  });
  const [status, setStatus] = useState("loading");
  const [logoReady, setLogoReady] = useState(true);

  const [wardRows, setWardRows] = useState([]);
  const [wardTotal, setWardTotal] = useState(0);
  const [wardLoading, setWardLoading] = useState(false);
  const [wardError, setWardError] = useState(null);

  const [selectedWardCode, setSelectedWardCode] = useState(null);
  const [selectedTrendWardCode, setSelectedTrendWardCode] = useState(null);
  const [wardDetail, setWardDetail] = useState(null);
  const [trendWardDetail, setTrendWardDetail] = useState(null);
  const [wardDetailLoading, setWardDetailLoading] = useState(false);
  const [wardDetailError, setWardDetailError] = useState(null);

  const [timeseries, setTimeseries] = useState({
    points: [],
    summary: null,
    metric: "rate",
    crime_type: null,
  });
  const [timeseriesLoading, setTimeseriesLoading] = useState(false);

  const [searchTerm, setSearchTerm] = useState("");
  const [bandFilter, setBandFilter] = useState("all");
  const [coverageFilter, setCoverageFilter] = useState("all");
  const [minRatePercentile, setMinRatePercentile] = useState("");
  const [minTrendSlope, setMinTrendSlope] = useState("");
  const [minYoYChange, setMinYoYChange] = useState("");
  const [metricFocus, setMetricFocus] = useState("rate");
  const [trendMetric, setTrendMetric] = useState("count");
  const [sortKey, setSortKey] = useState("rating");
  const [sortDir, setSortDir] = useState("desc");
  const [pageIndex, setPageIndex] = useState(0);
  const [crimeTypeFocus, setCrimeTypeFocus] = useState("all");
  const [trendWardOptions, setTrendWardOptions] = useState([]);
  const [trendWardLoading, setTrendWardLoading] = useState(false);
  const [trendWardError, setTrendWardError] = useState(null);
  const [seriesHoverIndex, setSeriesHoverIndex] = useState(null);
  const pageSize = 50;

  useEffect(() => {
    setStatus("loading");
    Promise.allSettled([
      api.getSummary(),
      api.getGapReport(),
      api.getMap(),
      api.getOpsStatus(),
    ]).then((results) => {
      const [summaryResult, gapResult, mapResult, opsResult] = results;
      if (summaryResult.status === "fulfilled") {
        setSummary(summaryResult.value);
        setStatus("ready");
      } else {
        setStatus("error");
      }
      if (gapResult.status === "fulfilled") {
        setGapReport(gapResult.value || null);
      }
      if (mapResult.status === "fulfilled") {
        setMapInfo((prev) => mapResult.value || prev);
      }
      if (opsResult.status === "fulfilled") {
        setOpsStatus(opsResult.value || null);
      }
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api.getOpsQuality(controller.signal)
      .then((data) => setOpsQuality(data || null))
      .catch(() => null);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (activeView !== "gap") return;
    const controller = new AbortController();
    api.getOpsJobs(20, controller.signal)
      .then((data) => setOpsJobs(data?.jobs || []))
      .catch(() => null);
    return () => controller.abort();
  }, [activeView]);

  const loadAlerts = (signal) => {
    setAlertLoading(true);
    setAlertError(null);
    return Promise.allSettled([
      api.listAlertRules({ limit: 200, signal }),
      api.listAlertEvents({ status: "open", limit: 50, signal }),
    ])
      .then(([rulesResult, eventsResult]) => {
        if (rulesResult.status === "fulfilled") {
          setAlertRules(rulesResult.value || []);
        }
        if (eventsResult.status === "fulfilled") {
          setAlertEvents(eventsResult.value?.items || []);
        }
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          setAlertError(error.message);
        }
      })
      .finally(() => setAlertLoading(false));
  };

  useEffect(() => {
    if (activeView !== "alerts") return;
    const controller = new AbortController();
    loadAlerts(controller.signal);
    return () => controller.abort();
  }, [activeView]);

  useEffect(() => {
    setPageIndex(0);
  }, [
    searchTerm,
    bandFilter,
    coverageFilter,
    minRatePercentile,
    minTrendSlope,
    minYoYChange,
    sortKey,
    sortDir,
    metricFocus,
  ]);

  const sortParam = useMemo(() => {
    if (sortKey === "ward") return "ward_name";
    if (sortKey === "metric") {
      if (metricFocus === "yoy") return "yoy_change";
      if (metricFocus === "harm") return "harm_score_per_100k";
      return "crime_rate_per_100k";
    }
    if (sortKey === "trend" || sortKey === "trajectory") return "trend_slope";
    if (sortKey === "rating") return "rating_score";
    return "rating_score";
  }, [sortKey, metricFocus]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (searchTerm.trim()) params.set("q", searchTerm.trim());
    if (bandFilter !== "all") params.set("band", bandFilter);
    if (coverageFilter !== "all")
      params.set("coverage_confidence", coverageFilter);
    if (minRatePercentile) params.set("min_rate_percentile", minRatePercentile);
    if (minTrendSlope) params.set("min_trend_slope", minTrendSlope);
    if (minYoYChange) params.set("min_yoy_change", minYoYChange);
    params.set("sort", sortParam);
    params.set("order", sortDir);
    params.set("limit", pageSize);
    params.set("offset", pageIndex * pageSize);

    setWardLoading(true);
    setWardError(null);
    api
      .listWards(params, controller.signal)
      .then((data) => {
        setWardRows(data?.items || []);
        setWardTotal(data?.total || 0);
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          setWardError(error.message);
        }
      })
      .finally(() => setWardLoading(false));
    return () => controller.abort();
  }, [
    searchTerm,
    bandFilter,
    coverageFilter,
    minRatePercentile,
    minTrendSlope,
    minYoYChange,
    sortParam,
    sortDir,
    pageIndex,
  ]);

  useEffect(() => {
    if (activeView !== "trends") return;
    if (trendWardOptions.length) return;
    const controller = new AbortController();
    setTrendWardLoading(true);
    setTrendWardError(null);
    const loadAllWards = async () => {
      const limit = 200;
      let offset = 0;
      let total = 0;
      const all = [];
      do {
        const params = new URLSearchParams();
        params.set("limit", String(limit));
        params.set("offset", String(offset));
        params.set("sort", "ward");
        params.set("order", "asc");
        const data = await api.listWards(params, controller.signal);
        const items = data?.items || [];
        total = data?.total || items.length;
        all.push(...items);
        offset += limit;
        if (!items.length) break;
      } while (offset < total);
      return all;
    };

    loadAllWards()
      .then((items) => {
        const seen = new Set();
        const unique = [];
        items.forEach((item) => {
          if (!item || !item.ward_code) return;
          if (seen.has(item.ward_code)) return;
          seen.add(item.ward_code);
          unique.push(item);
        });
        unique.sort((a, b) =>
          (a.ward_name || "").localeCompare(b.ward_name || "")
        );
        setTrendWardOptions(unique);
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          setTrendWardError(error.message);
        }
      })
      .finally(() => setTrendWardLoading(false));
    return () => controller.abort();
  }, [activeView, trendWardOptions.length]);

  useEffect(() => {
    if (!wardRows.length) return;
    const exists = wardRows.some((row) => row.ward_code === selectedWardCode);
    if (!selectedWardCode || !exists) {
      setSelectedWardCode(wardRows[0].ward_code);
    }
  }, [wardRows, selectedWardCode]);

  useEffect(() => {
    if (!selectedWardCode) return;
    const controller = new AbortController();
    setWardDetailLoading(true);
    setWardDetailError(null);
    api
      .getWardDetail(selectedWardCode, controller.signal)
      .then((data) => setWardDetail(data || null))
      .catch((error) => {
        if (error.name !== "AbortError") {
          setWardDetailError(error.message);
        }
      })
      .finally(() => setWardDetailLoading(false));
    return () => controller.abort();
  }, [selectedWardCode]);

  useEffect(() => {
    if (!selectedTrendWardCode) {
      setTrendWardDetail(null);
      return;
    }
    const controller = new AbortController();
    api
      .getWardDetail(selectedTrendWardCode, controller.signal)
      .then((data) => setTrendWardDetail(data || null))
      .catch(() => null);
    return () => controller.abort();
  }, [selectedTrendWardCode]);

  useEffect(() => {
    if (!selectedTrendWardCode) return;
    setCrimeTypeFocus("all");
  }, [selectedTrendWardCode]);

  const timeseriesMetric =
    metricFocus === "harm" ? "harm" : metricFocus === "yoy" ? "count" : "rate";
  const activeSeriesMetric =
    activeView === "trends" ? trendMetric : timeseriesMetric;
  const activeCrimeType =
    activeView === "trends" && crimeTypeFocus !== "all"
      ? crimeTypeFocus
      : null;

  useEffect(() => {
    const activeWardCode =
      activeView === "trends" ? selectedTrendWardCode : selectedWardCode;
    if (!activeWardCode) return;
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set("metric", activeSeriesMetric);
    if (activeCrimeType) params.set("type", activeCrimeType);
    setTimeseriesLoading(true);
    api
      .getWardTimeseries(activeWardCode, params, controller.signal)
      .then((data) => setTimeseries(data || null))
      .catch(() => null)
      .finally(() => setTimeseriesLoading(false));
    return () => controller.abort();
  }, [
    activeView,
    selectedTrendWardCode,
    selectedWardCode,
    activeSeriesMetric,
    activeCrimeType,
  ]);

  const watchlist = useMemo(() => wardRows, [wardRows]);
  const directory = useMemo(() => wardRows, [wardRows]);
  const selectedWard =
    wardDetail?.ward ||
    watchlist.find((row) => row.ward_code === selectedWardCode) ||
    null;

  const bandCounts = summary?.band_counts || {};
  const coveragePct = useMemo(() => {
    if (!opsQuality?.confidence_counts) return null;
    const counts = opsQuality.confidence_counts || {};
    const total =
      (counts.high || 0) + (counts.medium || 0) + (counts.low || 0);
    if (!total) return null;
    const missing = opsQuality.population_missing ?? 0;
    return ((total - missing) / total) * 100;
  }, [opsQuality]);

  const timeWindow = useMemo(() => {
    if (opsStatus?.coverage_start || opsStatus?.coverage_end) {
      return {
        first: opsStatus.coverage_start || "N/A",
        last: opsStatus.coverage_end || "N/A",
      };
    }
    if (opsQuality?.coverage_start || opsQuality?.coverage_end) {
      return {
        first: opsQuality.coverage_start || "N/A",
        last: opsQuality.coverage_end || "N/A",
      };
    }
    return { first: "N/A", last: "N/A" };
  }, [opsStatus, opsQuality]);

  const opsFeed = [
    {
      label: "History latest",
      value: gapReport?.history_latest || summary?.latest_month || "N/A",
    },
    {
      label: "Latest available",
      value: gapReport?.latest_available || "N/A",
    },
    {
      label: "Population missing",
      value:
        opsQuality?.population_missing_pct !== null &&
        opsQuality?.population_missing_pct !== undefined
          ? `${opsQuality.population_missing_pct.toFixed(1)}%`
          : "N/A",
    },
    {
      label: "Short coverage",
      value:
        opsQuality?.short_history_pct !== null &&
        opsQuality?.short_history_pct !== undefined
          ? `${opsQuality.short_history_pct.toFixed(1)}%`
          : "N/A",
    },
  ];

  const bandFilters = ["all", "High", "Elevated", "Watch", "Stable"];
  const metricOptions = [
    { id: "rate", label: "Rate" },
    { id: "yoy", label: "YoY" },
    { id: "harm", label: "Harm" },
  ];
  const alertMetricOptions = [
    { id: "rating_band", label: "Rating band", type: "text" },
    { id: "crime_rate_per_100k", label: "Rate / 100k", type: "number" },
    { id: "trend_slope", label: "Trend slope", type: "number" },
    { id: "yoy_change", label: "YoY change %", type: "number" },
    { id: "harm_score_per_100k", label: "Harm / 100k", type: "number" },
    { id: "rate_percentile", label: "Rate percentile", type: "number" },
    { id: "coverage_confidence", label: "Coverage confidence", type: "text" },
  ];
  const alertOperatorOptions = {
    text: [
      { id: "eq", label: "is" },
      { id: "neq", label: "is not" },
      { id: "contains", label: "contains" },
    ],
    number: [
      { id: "gte", label: ">=" },
      { id: "lte", label: "<=" },
      { id: "gt", label: ">" },
      { id: "lt", label: "<" },
    ],
  };

  const metricLabel =
    metricFocus === "yoy"
      ? "YoY change"
      : metricFocus === "harm"
        ? "Harm / 100k"
        : "Annualized / 100k";

  const metricValue = (row) => {
    if (!row) return "N/A";
    if (metricFocus === "yoy") return formatPercent(row.yoy_change);
    if (metricFocus === "harm") return formatNumber(row.harm_score_per_100k);
    return formatNumber(row.crime_rate_per_100k);
  };

  const metricSparkline = summary?.sparkline || [];
  const sparklinePoints = useMemo(
    () => buildSparklinePoints(metricSparkline),
    [metricSparkline]
  );

  const seriesPoints = timeseries?.points || [];
  const seriesPath = useMemo(
    () => buildSeriesPath(seriesPoints),
    [seriesPoints]
  );
  const seriesChartPoints = useMemo(
    () => buildSeriesCoordinates(seriesPoints),
    [seriesPoints]
  );
  const seriesRange = useMemo(() => {
    const values = seriesPoints
      .map((point) => Number(point.value))
      .filter((value) => Number.isFinite(value));
    if (values.length < 2) return null;
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const steps = 4;
    const step = (max - min) / steps;
    const ticks = Array.from({ length: steps + 1 }, (_, idx) => min + step * idx);
    return { min, max, ticks };
  }, [seriesPoints]);
  const seriesYAxisTicks = useMemo(() => {
    if (!seriesRange) return [];
    const { min, max, ticks } = seriesRange;
    const range = max - min || 1;
    const height = SERIES_VIEWBOX.height;
    const padding = SERIES_VIEWBOX.padding;
    return ticks.map((value) => ({
      value,
      y: height - padding - ((value - min) / range) * (height - padding * 2),
    }));
  }, [seriesRange]);
  const seriesXAxisLabels = useMemo(() => {
    if (!seriesPoints.length) return { start: "", end: "" };
    return {
      start: seriesPoints[0].month || "",
      end: seriesPoints[seriesPoints.length - 1].month || "",
    };
  }, [seriesPoints]);
  const seriesSummary = timeseries?.summary;
  const seriesLabel =
    activeSeriesMetric === "count"
      ? "Monthly incidents"
      : activeSeriesMetric === "harm"
        ? "Harm / 100k"
        : "Rate / 100k";
  const formatSeriesValue = (value) => {
    if (value === null || value === undefined) return "N/A";
    if (activeSeriesMetric === "count") return formatNumber(value);
    return formatRate(value);
  };
  const seriesHoverPoint =
    seriesHoverIndex !== null ? seriesChartPoints[seriesHoverIndex] : null;
  const seriesHoverChange = useMemo(() => {
    if (!seriesHoverPoint || seriesHoverIndex === null || seriesHoverIndex < 1) {
      return null;
    }
    const prev = seriesChartPoints[seriesHoverIndex - 1];
    if (!prev || seriesHoverPoint.value === null || prev.value === null) {
      return null;
    }
    return seriesHoverPoint.value - prev.value;
  }, [seriesHoverPoint, seriesHoverIndex, seriesChartPoints]);
  const seriesInsight = useMemo(() => {
    if (!seriesSummary || seriesSummary.latest_avg === null) {
      return "Not enough history to compare yet.";
    }
    if (seriesSummary.prior_avg === null) {
      return `Latest ${seriesSummary.window}-mo avg: ${formatSeriesValue(
        seriesSummary.latest_avg
      )}.`;
    }
    const changeLabel = formatSeriesValue(seriesSummary.change);
    const pctLabel =
      seriesSummary.pct_change !== null
        ? formatPercent(seriesSummary.pct_change)
        : "N/A";
    return `Last ${seriesSummary.window}-mo avg ${formatSeriesValue(
      seriesSummary.latest_avg
    )} vs prior ${formatSeriesValue(seriesSummary.prior_avg)} (${changeLabel}, ${pctLabel}).`;
  }, [seriesSummary, activeSeriesMetric]);

  const handleSeriesHover = (event) => {
    if (!seriesChartPoints.length) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const scaleX = SERIES_VIEWBOX.width / rect.width;
    const x = (event.clientX - rect.left) * scaleX;
    const step =
      (SERIES_VIEWBOX.width - SERIES_VIEWBOX.padding * 2) /
      (seriesChartPoints.length - 1);
    const rawIndex = Math.round((x - SERIES_VIEWBOX.padding) / step);
    const clamped = Math.max(
      0,
      Math.min(seriesChartPoints.length - 1, rawIndex)
    );
    setSeriesHoverIndex(clamped);
  };

  const seriesTooltipStyle = seriesHoverPoint
    ? {
        left: `${(seriesHoverPoint.x / SERIES_VIEWBOX.width) * 100}%`,
        top: `${(seriesHoverPoint.y / SERIES_VIEWBOX.height) * 100}%`,
      }
    : undefined;

  useEffect(() => {
    setSeriesHoverIndex(null);
  }, [seriesPoints]);

  const ratingExplain = wardDetail?.rating_explain || {};
  const wardCrimeTypes = wardDetail?.crime_types || [];
  const trendCrimeTypes = trendWardDetail?.crime_types || [];
  const activeCrimeTypes =
    activeView === "trends" ? trendCrimeTypes : wardCrimeTypes;
  const wardOfficials = wardDetail?.officials || [];
  const trendWardList = useMemo(() => {
    const base = trendWardOptions.length ? trendWardOptions : wardRows;
    const seen = new Set();
    const unique = [];
    base.forEach((ward) => {
      if (!ward || !ward.ward_code) return;
      if (seen.has(ward.ward_code)) return;
      seen.add(ward.ward_code);
      unique.push(ward);
    });
    unique.sort((a, b) =>
      (a.ward_name || a.ward_code || "").localeCompare(
        b.ward_name || b.ward_code || ""
      )
    );
    return unique;
  }, [trendWardOptions, wardRows]);
  const selectedTrendWard =
    trendWardDetail?.ward ||
    trendWardList.find((row) => row.ward_code === selectedTrendWardCode) ||
    null;
  useEffect(() => {
    if (activeView !== "trends") return;
    if (!trendWardList.length) return;
    if (selectedTrendWardCode) {
      const exists = trendWardList.some(
        (ward) => ward.ward_code === selectedTrendWardCode
      );
      if (exists) return;
    }
    setSelectedTrendWardCode(trendWardList[0].ward_code);
  }, [activeView, trendWardList, selectedTrendWardCode]);
  const crimeTypeLabel = useMemo(() => {
    if (crimeTypeFocus === "all") return "All types";
    const match = activeCrimeTypes.find(
      (row) => row.crime_type === crimeTypeFocus
    );
    return match?.crime_type_label || match?.crime_type || crimeTypeFocus;
  }, [crimeTypeFocus, activeCrimeTypes]);
  const selectedAlertMetric =
    alertMetricOptions.find((option) => option.id === newAlert.metric) ||
    alertMetricOptions[0];
  const alertOperators = alertOperatorOptions[selectedAlertMetric.type] || [];
  const pageStart = wardTotal ? pageIndex * pageSize + 1 : 0;
  const pageEnd = wardTotal
    ? Math.min(wardTotal, (pageIndex + 1) * pageSize)
    : 0;
  const pageCount = wardTotal ? Math.ceil(wardTotal / pageSize) : 1;

  useEffect(() => {
    const operators = alertOperatorOptions[selectedAlertMetric.type] || [];
    if (!operators.length) return;
    const hasOperator = operators.some(
      (option) => option.id === newAlert.operator
    );
    let nextAlert = newAlert;
    if (!hasOperator) {
      nextAlert = { ...nextAlert, operator: operators[0].id };
    }
    if (selectedAlertMetric.type === "number") {
      if (nextAlert.threshold && Number.isNaN(Number(nextAlert.threshold))) {
        nextAlert = { ...nextAlert, threshold: "" };
      }
    } else if (!nextAlert.threshold) {
      nextAlert = { ...nextAlert, threshold: "High" };
    }
    if (nextAlert !== newAlert) {
      setNewAlert(nextAlert);
    }
  }, [newAlert.metric, newAlert.operator, selectedAlertMetric.type]);

  const trendClass = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num)) return "trend-stable";
    if (num > 5) return "trend-high";
    if (num < -5) return "trend-low";
    return "trend-stable";
  };

  const trajectoryDirection = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num)) return "flat";
    if (num > 5) return "up";
    if (num < -5) return "down";
    return "flat";
  };

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir(key === "ward" ? "asc" : "desc");
    }
  };

  const handleCreateAlert = async () => {
    setAlertError(null);
    if (!newAlert.name.trim()) {
      setAlertError("Alert name is required.");
      return;
    }
    if (newAlert.rule_type === "ward" && !newAlert.ward_code.trim()) {
      setAlertError("Ward code is required for ward alerts.");
      return;
    }
    const payload = {
      name: newAlert.name.trim(),
      rule_type: newAlert.rule_type,
      ward_code: newAlert.rule_type === "ward" ? newAlert.ward_code.trim() : null,
      metric: newAlert.metric,
      operator: newAlert.operator,
      trigger_on: newAlert.trigger_on,
      notify_emails: newAlert.notify_emails
        .split(",")
        .map((email) => email.trim())
        .filter(Boolean),
    };
    if (selectedAlertMetric.type === "number") {
      const num = Number(newAlert.threshold);
      payload.threshold_number = Number.isFinite(num) ? num : null;
    } else {
      payload.threshold_value = newAlert.threshold;
    }
    try {
      await api.createAlertRule(payload);
      setNewAlert({
        name: "",
        rule_type: "ward",
        ward_code: "",
        metric: "rating_band",
        operator: "eq",
        threshold: "High",
        trigger_on: "enter",
        notify_emails: newAlert.notify_emails,
      });
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const handleAcknowledgeAlert = async (eventId) => {
    try {
      await api.acknowledgeAlertEvent(eventId);
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const handleMuteRule = async (ruleId, hours = 24) => {
    try {
      await api.muteAlertRule(ruleId, { hours });
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const handleUnmuteRule = async (ruleId) => {
    try {
      await api.unmuteAlertRule(ruleId);
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const handleToggleRule = async (ruleId, isActive) => {
    try {
      await api.updateAlertRule(ruleId, { is_active: !isActive });
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const handleDeleteRule = async (ruleId) => {
    try {
      await api.deleteAlertRule(ruleId);
      loadAlerts();
    } catch (error) {
      setAlertError(error.message);
    }
  };

  const sortIndicator = (key) => {
    if (sortKey !== key) return "\u2195";
    return sortDir === "asc" ? "\u2191" : "\u2193";
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="sigil">
            {logoReady ? (
              <img
                src="/logo.png"
                alt="CrimeMap logo"
                className="logo"
                onError={() => setLogoReady(false)}
              />
            ) : (
              <span className="sigil-fallback" />
            )}
          </span>
          <div>
            <p className="brand-title">CrimeMap Intelligence</p>
            <p className="brand-subtitle">Northern Ireland Crime Signals</p>
          </div>
        </div>
        <div className="topbar-actions">
          <span className={`status-pill ${status}`}>{status}</span>
          <button
            className="button primary"
            type="button"
            onClick={() => setActiveView("gap")}
          >
            Open Ops Console
          </button>
        </div>
      </header>

      <div className="shell">
        <aside className="sidenav">
          <div className="nav-section">
            <p className="nav-title">Core Views</p>
            <button
              className={`nav-item ${activeView === "overview" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("overview")}
            >
              Overview
            </button>
            <button
              className={`nav-item ${activeView === "intelligence" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("intelligence")}
            >
              Ward Intelligence
            </button>
            <button
              className={`nav-item ${activeView === "trends" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("trends")}
            >
              Crime Trends
            </button>
            <button
              className={`nav-item ${activeView === "gap" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("gap")}
            >
              Gap Monitor
            </button>
          </div>
          <div className="nav-section">
            <p className="nav-title">Operations</p>
            <button
              className={`nav-item ${activeView === "gap" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("gap")}
            >
              Ingest Control
            </button>
            <button
              className={`nav-item ${activeView === "gap" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("gap")}
            >
              MySQL Sync
            </button>
            <button
              className={`nav-item ${activeView === "alerts" ? "active" : ""}`}
              type="button"
              onClick={() => setActiveView("alerts")}
            >
              Alerts
            </button>
          </div>
        </aside>

        <main className="main">
          {activeView === "overview" ? (
            <>
              <section className="grid grid-2">
                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Signal Summary</h2>
                    <span className="tag">Live</span>
                  </div>
                  <div className="metrics">
                    <div className="metric">
                      <p>Latest month</p>
                      <div className="metric-line">
                        <h3>{summary?.latest_month || "N/A"}</h3>
                        {sparklinePoints ? (
                          <svg
                            className="sparkline"
                            viewBox="0 0 80 24"
                            aria-hidden="true"
                          >
                            <polyline points={sparklinePoints} />
                          </svg>
                        ) : null}
                      </div>
                    </div>
                    <div className="metric">
                      <p>Total wards</p>
                      <h3>{formatNumber(summary?.total_wards)}</h3>
                    </div>
                    <div className="metric">
                      <p>Total population</p>
                      <h3>{formatNumber(summary?.total_population)}</h3>
                    </div>
                    <div className="metric">
                      <p>High crime wards</p>
                      <h3>{formatNumber(summary?.high_crime_wards)}</h3>
                    </div>
                    <div className="metric">
                      <p>Annualized Rate / 100k</p>
                      <h3>{formatRate(summary?.avg_rate_per_100k)}</h3>
                    </div>
                  </div>
                  <div className="panel-footer">
                    Data source: {summary?.source || "unknown"}
                  </div>
                </div>

                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Operational Feed</h2>
                    <span className="tag muted">Live ops</span>
                  </div>
                  <ul className="ops-list">
                    {opsFeed.map((item) => (
                      <li key={item.label}>
                        <button className="ops-item" type="button">
                          <span>{item.label}</span>
                          <strong>{item.value}</strong>
                        </button>
                      </li>
                    ))}
                  </ul>
                  <div className="panel-footer">
                    Default start month: {gapReport?.default_start_month || "N/A"}
                  </div>
                </div>
              </section>

              <section className="panel glass map-panel">
                <div className="panel-header">
                  <h2>Ward Crime + Population Map</h2>
                  <div className="panel-actions">
                    <span className="tag muted">
                      {mapInfo.exists ? "Live" : "Missing"}
                    </span>
                    <a
                      className="button ghost"
                      href={resolveMapUrl(mapInfo.wards_map_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Map
                    </a>
                  </div>
                </div>
                {mapInfo.exists ? (
                  <iframe
                    className="map-frame"
                    title="Ward crime map"
                    src={resolveMapUrl(mapInfo.wards_map_url)}
                  />
                ) : (
                  <div className="map-placeholder">
                    Map file not found. Run the pipeline to generate
                    outputs/wards_interactive_map.html.
                  </div>
                )}
                <div className="panel-footer">
                  Window: {timeWindow.first} to {timeWindow.last}
                </div>
              </section>

              <section className="grid grid-3">
                <div className="panel glass compact">
                  <div className="panel-header">
                    <h2>Threat Bands</h2>
                    <InfoTip text="Rating combines annualized rate percentile and trend slope." />
                  </div>
                  <div className="bar-stack">
                    <div className="bar high">
                      High <span>{formatNumber(bandCounts.High)}</span>
                    </div>
                    <div className="bar elevated">
                      Elevated <span>{formatNumber(bandCounts.Elevated)}</span>
                    </div>
                    <div className="bar watch">
                      Watch <span>{formatNumber(bandCounts.Watch)}</span>
                    </div>
                    <div className="bar stable">
                      Stable <span>{formatNumber(bandCounts.Stable)}</span>
                    </div>
                  </div>
                </div>

                <div className="panel glass compact">
                  <h2>Coverage</h2>
                  <div className="stat-row">
                    <span>Ward coverage</span>
                    <strong>
                      {coveragePct !== null ? `${coveragePct.toFixed(1)}%` : "N/A"}
                    </strong>
                  </div>
                  <div className="stat-row">
                    <span>Coverage window</span>
                    <strong>
                      {timeWindow.first} to {timeWindow.last}
                    </strong>
                  </div>
                  <div className="stat-row">
                    <span>Annualized Rate / 100k</span>
                    <strong>{formatRate(summary?.avg_rate_per_100k)}</strong>
                  </div>
                </div>

                <div className="panel glass compact">
                  <h2>Risk Filters</h2>
                  <div className="chips">
                    <span>Rate percentile</span>
                    <span>Trend slope</span>
                    <span>Ward coverage</span>
                    <span>Gap status</span>
                  </div>
                </div>
              </section>
            </>
          ) : null}

          {activeView === "intelligence" ? (
            <>
              <section className="grid grid-2">
              <div className="panel glass">
                <div className="panel-header">
                  <h2>Ward Watchlist</h2>
                  <div className="panel-actions">
                    <span className="tag muted">Server-driven</span>
                    <input
                      className="search-input"
                      placeholder="Search ward name or code"
                      value={searchTerm}
                      onChange={(event) => setSearchTerm(event.target.value)}
                    />
                  </div>
                </div>
                <div className="filter-row">
                  {metricOptions.map((option) => (
                    <button
                      key={option.id}
                      className={`chip-button ${bandClass(
                        option.id === "rate"
                          ? "stable"
                          : option.id === "yoy"
                            ? "elevated"
                            : "high"
                      )} ${metricFocus === option.id ? "active" : ""}`}
                      type="button"
                      onClick={() => setMetricFocus(option.id)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <div className="filter-row">
                  {bandFilters.map((band) => (
                    <button
                      key={band}
                      className={`chip-button ${bandClass(band)} ${
                        bandFilter === band ? "active" : ""
                      }`}
                      type="button"
                      onClick={() => setBandFilter(band)}
                    >
                      {band === "all" ? "All bands" : band}
                    </button>
                  ))}
                </div>
                <div className="filter-row filter-advanced">
                  <div className="filter-group">
                    <label>Coverage</label>
                    <select
                      value={coverageFilter}
                      onChange={(event) => setCoverageFilter(event.target.value)}
                    >
                      <option value="all">All coverage</option>
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                      <option value="low">Low</option>
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Min rate %</label>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      placeholder="e.g. 70"
                      value={minRatePercentile}
                      onChange={(event) => setMinRatePercentile(event.target.value)}
                    />
                  </div>
                  <div className="filter-group">
                    <label>Min trend slope</label>
                    <input
                      type="number"
                      step="0.01"
                      placeholder="e.g. 0.2"
                      value={minTrendSlope}
                      onChange={(event) => setMinTrendSlope(event.target.value)}
                    />
                  </div>
                  <div className="filter-group">
                    <label>Min YoY %</label>
                    <input
                      type="number"
                      step="0.1"
                      placeholder="e.g. 5"
                      value={minYoYChange}
                      onChange={(event) => setMinYoYChange(event.target.value)}
                    />
                  </div>
                </div>
                <div className="table scroll">
                  <div className="table-row header">
                    <button
                      className={`header-cell ${sortKey === "ward" ? "active" : ""}`}
                      type="button"
                      onClick={() => toggleSort("ward")}
                    >
                      Ward{" "}
                      <span className="sort-indicator">{sortIndicator("ward")}</span>
                    </button>
                    <button
                      className={`header-cell ${sortKey === "metric" ? "active" : ""}`}
                      type="button"
                      onClick={() => toggleSort("metric")}
                    >
                      {metricLabel}{" "}
                      <span className="sort-indicator">
                        {sortIndicator("metric")}
                      </span>
                    </button>
                    <button
                      className={`header-cell ${sortKey === "trend" ? "active" : ""}`}
                      type="button"
                      onClick={() => toggleSort("trend")}
                    >
                      Trend{" "}
                      <span className="sort-indicator">{sortIndicator("trend")}</span>
                    </button>
                    <button
                      className={`header-cell ${sortKey === "trajectory" ? "active" : ""}`}
                      type="button"
                      onClick={() => toggleSort("trajectory")}
                    >
                      Trajectory{" "}
                      <span className="sort-indicator">
                        {sortIndicator("trajectory")}
                      </span>
                    </button>
                    <button
                      className={`header-cell ${sortKey === "rating" ? "active" : ""}`}
                      type="button"
                      onClick={() => toggleSort("rating")}
                    >
                      Rating{" "}
                      <span className="sort-indicator">
                        {sortIndicator("rating")}
                      </span>
                    </button>
                  </div>
                  <StatusMessage
                    loading={wardLoading}
                    error={wardError}
                    loadingText="Loading wards..."
                    className="table-empty"
                    as="div"
                  />
                  {!wardLoading && !wardError && !watchlist.length ? (
                    <div className="table-empty">No wards match these filters.</div>
                  ) : null}
                  {watchlist.map((row) => {
                    const direction = trajectoryDirection(row.trend_pct);
                    return (
                      <button
                        className={`table-row ${bandClass(row.rating_band)} ${
                          selectedWardCode === row.ward_code ? "active" : ""
                        }`}
                        key={row.ward_code}
                        type="button"
                        onClick={() => setSelectedWardCode(row.ward_code)}
                      >
                        <span>{row.ward_name}</span>
                        <span>{metricValue(row)}</span>
                        <span className={`trend ${trendClass(row.trend_pct)}`}>
                          {formatPercent(row.trend_pct)}
                        </span>
                        <span
                          className={`trajectory ${trendClass(
                            row.trend_pct
                          )} trajectory-${direction}`}
                          aria-label={`trajectory ${direction}`}
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                            <path d="M5 12H19 M13 6L19 12L13 18" />
                          </svg>
                        </span>
                        <span className={`risk ${bandClass(row.rating_band)}`}>
                          {row.rating_band}{" "}
                          {row.rating_score ? `(${row.rating_score})` : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="table-footer">
                  <span>
                    Showing {pageStart}-{pageEnd} of {formatNumber(wardTotal)}
                  </span>
                  <div className="pagination">
                    <button
                      className="button ghost"
                      type="button"
                      onClick={() => setPageIndex(Math.max(pageIndex - 1, 0))}
                      disabled={pageIndex === 0}
                    >
                      Prev
                    </button>
                    <span className="page-count">
                      {pageIndex + 1} / {pageCount}
                    </span>
                    <button
                      className="button ghost"
                      type="button"
                      onClick={() => setPageIndex(pageIndex + 1)}
                      disabled={pageEnd >= wardTotal}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </div>

              <div className="panel glass">
                <div className="panel-header">
                  <h2>Ward Intelligence</h2>
                  <span className={`tag ${bandClass(selectedWard?.rating_band)}`}>
                    {selectedWard?.rating_band || "N/A"}
                  </span>
                </div>
                <div className="panel-actions">
                  {metricOptions.map((option) => (
                    <button
                      key={option.id}
                      className={`chip-button ${bandClass(
                        option.id === "rate"
                          ? "stable"
                          : option.id === "yoy"
                            ? "elevated"
                            : "high"
                      )} ${metricFocus === option.id ? "active" : ""}`}
                      type="button"
                      onClick={() => setMetricFocus(option.id)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                {wardDetailLoading || wardDetailError ? (
                  <StatusMessage
                    loading={wardDetailLoading}
                    error={wardDetailError}
                    loadingText="Loading ward intelligence..."
                  />
                ) : selectedWard ? (
                  <>
                    <div className="intel-grid">
                      <div>
                        <p>Ward</p>
                        <h3>{selectedWard.ward_name}</h3>
                      </div>
                      <div>
                        <p>Population</p>
                        <h3>{formatNumber(selectedWard.population)}</h3>
                      </div>
                      <div>
                        <p>Total crimes</p>
                        <h3>{formatNumber(selectedWard.total_crimes)}</h3>
                      </div>
                      {metricFocus === "rate" ? (
                        <>
                          <div>
                            <p>Annualized / 100k</p>
                            <h3>{formatNumber(selectedWard.crime_rate_per_100k)}</h3>
                          </div>
                          <div>
                            <p>Trend change (3-mo avg)</p>
                            <h3 className={trendClass(selectedWard.trend_pct)}>
                              {formatPercent(selectedWard.trend_pct)}
                            </h3>
                          </div>
                        </>
                      ) : metricFocus === "yoy" ? (
                        <>
                          <div>
                            <p>YoY change</p>
                            <h3 className={trendClass(selectedWard.yoy_change)}>
                              {formatPercent(selectedWard.yoy_change)}
                            </h3>
                          </div>
                          <div>
                            <p>Current month crimes</p>
                            <h3>{formatNumber(selectedWard.yoy_current)}</h3>
                          </div>
                          <div>
                            <p>Same month last year</p>
                            <h3>{formatNumber(selectedWard.yoy_prior)}</h3>
                          </div>
                        </>
                      ) : (
                        <>
                          <div>
                            <p>Harm / 100k</p>
                            <h3>{formatNumber(selectedWard.harm_score_per_100k)}</h3>
                          </div>
                          <div>
                            <p>Total harm</p>
                            <h3>{formatNumber(selectedWard.total_harm)}</h3>
                          </div>
                        </>
                      )}
                      <div>
                        <p>Rating score</p>
                        <h3>{formatNumber(selectedWard.rating_score)}</h3>
                      </div>
                      <div>
                        <p>Window</p>
                        <h3>
                          {selectedWard.first_month} to {selectedWard.last_month}
                        </h3>
                      </div>
                      <div>
                        <p>Coverage months</p>
                        <h3>{formatNumber(selectedWard.months)}</h3>
                      </div>
                    </div>
                    <div className="intel-split">
                      <div className="panel-inner">
                        <div className="panel-header">
                          <h3>Trendline</h3>
                          <span className="tag muted">{seriesLabel}</span>
                        </div>
                        {timeseriesLoading ? (
                          <StatusMessage
                            loading={timeseriesLoading}
                            loadingText="Loading trendline..."
                          />
                        ) : seriesPath ? (
                          <>
                            <div className="series-chart-frame">
                              <div className="series-y-axis">
                                {seriesYAxisTicks
                                  .slice()
                                  .reverse()
                                  .map((tick) => (
                                    <span key={`y-${tick.value}`}>
                                      {formatSeriesValue(tick.value)}
                                    </span>
                                  ))}
                              </div>
                              <div className="series-chart-wrap">
                                <svg
                                  className="series-chart"
                                  viewBox="0 0 520 160"
                                  onMouseMove={handleSeriesHover}
                                  onMouseLeave={() => setSeriesHoverIndex(null)}
                                >
                                  <g className="series-grid">
                                    {seriesYAxisTicks.map((tick) => (
                                      <line
                                        key={`grid-${tick.value}`}
                                        x1={SERIES_VIEWBOX.padding}
                                        x2={
                                          SERIES_VIEWBOX.width -
                                          SERIES_VIEWBOX.padding
                                        }
                                        y1={tick.y}
                                        y2={tick.y}
                                      />
                                    ))}
                                  </g>
                                  <line
                                    className="series-axis"
                                    x1={SERIES_VIEWBOX.padding}
                                    x2={SERIES_VIEWBOX.padding}
                                    y1={SERIES_VIEWBOX.padding}
                                    y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                                  />
                                  <line
                                    className="series-axis"
                                    x1={SERIES_VIEWBOX.padding}
                                    x2={SERIES_VIEWBOX.width - SERIES_VIEWBOX.padding}
                                    y1={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                                    y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                                  />
                                  <path d={seriesPath} />
                                  {seriesHoverPoint ? (
                                    <>
                                      <line
                                        className="series-marker"
                                        x1={seriesHoverPoint.x}
                                        x2={seriesHoverPoint.x}
                                        y1={SERIES_VIEWBOX.padding}
                                        y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                                      />
                                      <circle
                                        className="series-dot"
                                        cx={seriesHoverPoint.x}
                                        cy={seriesHoverPoint.y}
                                        r="4"
                                      />
                                    </>
                                  ) : null}
                                </svg>
                                {seriesHoverPoint ? (
                                  <div
                                    className="series-tooltip"
                                    style={seriesTooltipStyle}
                                  >
                                    <span className="tooltip-month">
                                      {seriesHoverPoint.month}
                                    </span>
                                    <span className="tooltip-value">
                                      {formatSeriesValue(seriesHoverPoint.value)}{" "}
                                      <em>{seriesLabel}</em>
                                    </span>
                                    <span className="tooltip-change">
                                      {seriesHoverChange === null
                                        ? "MoM: N/A"
                                        : `MoM: ${formatSeriesValue(
                                            seriesHoverChange
                                          )}`}
                                    </span>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                            <div className="series-x-axis">
                              <span>{seriesXAxisLabels.start || "Start"}</span>
                              <span>{seriesXAxisLabels.end || "End"}</span>
                            </div>
                          </>
                        ) : (
                          <p className="hint">No trendline available.</p>
                        )}
                        <p className="hint">{seriesInsight}</p>
                      </div>
                      <div className="panel-inner">
                        <div className="panel-header">
                          <h3>Explain this rating</h3>
                          <div className="panel-header-tools">
                            <InfoTip text="Score = (rate percentile * 0.7) + (trend percentile * 0.3)." />
                            <span className={`tag ${bandClass(selectedWard.rating_band)}`}>
                              {selectedWard.rating_band || "N/A"}
                            </span>
                          </div>
                        </div>
                        <div className="explain-grid">
                          <div>
                            <p>Rate percentile</p>
                            <h4>
                              {Number.isFinite(
                                Number(ratingExplain.rate_percentile)
                              )
                                ? `${Number(ratingExplain.rate_percentile).toFixed(1)}%`
                                : "N/A"}
                            </h4>
                          </div>
                          <div>
                            <p>Trend percentile</p>
                            <h4>
                              {Number.isFinite(
                                Number(ratingExplain.trend_percentile)
                              )
                                ? `${Number(ratingExplain.trend_percentile).toFixed(1)}%`
                                : "N/A"}
                            </h4>
                          </div>
                          <div>
                            <p>Rate weight</p>
                            <h4>{ratingExplain.rate_weight || 0.7}</h4>
                          </div>
                          <div>
                            <p>Trend weight</p>
                            <h4>{ratingExplain.trend_weight || 0.3}</h4>
                          </div>
                          <div>
                            <p>Rating score</p>
                            <h4>{formatNumber(ratingExplain.rating_score)}</h4>
                          </div>
                        </div>
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="hint">Select a ward to view details.</p>
                )}
              </div>
            </section>
            <section className="panel glass officials-wide">
              <div className="panel-header">
                <h2>Elected officials</h2>
                <span className="tag muted">
                  {wardOfficials.length ? `${wardOfficials.length} listed` : "No data"}
                </span>
              </div>
              {wardDetailLoading || wardDetailError ? (
                <StatusMessage
                  loading={wardDetailLoading}
                  error={wardDetailError}
                  loadingText="Loading officials..."
                />
              ) : selectedWard ? (
                wardOfficials.length ? (
                  <div className="officials-list">
                    {wardOfficials.map((official, index) => {
                      const logo = partyLogoFor(official.party);
                      return (
                        <div
                          className="official-card"
                          key={`${official.name}-${official.role}-${index}`}
                        >
                          <div className="official-card-header">
                            <div className="official-identity">
                              {logo ? (
                                <img
                                  className="party-logo"
                                  src={logo.src}
                                  alt={logo.alt}
                                />
                              ) : (
                                <div className="party-placeholder" />
                              )}
                              <div>
                                <h4>{official.name || "Unknown"}</h4>
                                <p className="official-party">
                                  {official.party || "Independent"}
                                </p>
                              </div>
                            </div>
                            {official.role ? (
                              <span className="tag muted">{official.role}</span>
                            ) : null}
                          </div>
                          <div className="official-contact">
                            {official.email ? (
                              <a
                                href={`mailto:${official.email}`}
                                className="contact-link"
                              >
                                <svg
                                  className="contact-icon"
                                  viewBox="0 0 24 24"
                                  aria-hidden="true"
                                  focusable="false"
                                >
                                  <path d="M4 6h16v12H4z" />
                                  <path d="M4 7l8 6 8-6" />
                                </svg>
                                <span>{official.email}</span>
                              </a>
                            ) : (
                              <span className="contact-muted">
                                <svg
                                  className="contact-icon"
                                  viewBox="0 0 24 24"
                                  aria-hidden="true"
                                  focusable="false"
                                >
                                  <path d="M4 6h16v12H4z" />
                                  <path d="M4 7l8 6 8-6" />
                                </svg>
                                <span>No email</span>
                              </span>
                            )}
                            {official.phone ? (
                              <span className="contact-phone">
                                <svg
                                  className="contact-icon"
                                  viewBox="0 0 24 24"
                                  aria-hidden="true"
                                  focusable="false"
                                >
                                  <path d="M6 3h4l2 6-3 2c1 2 3 4 5 5l2-3 6 2v4c0 1-1 2-2 2-9 0-16-7-16-16 0-1 1-2 2-2z" />
                                </svg>
                                <span>{official.phone}</span>
                              </span>
                            ) : null}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="hint">
                    No officials loaded. Configure the officials API to populate
                    this section.
                  </p>
                )
              ) : (
                <p className="hint">Select a ward to view officials.</p>
              )}
            </section>
          </>
        ) : null}

          {activeView === "trends" ? (
            <section className="grid grid-2">
              <div className="panel glass">
                <div className="panel-header">
                  <h2>Crime Type Trends</h2>
                  <span className="tag muted">
                    {selectedTrendWard?.ward_name || "Select a ward"}
                  </span>
                </div>
                <div className="filter-row">
                  <div className="filter-group">
                    <label htmlFor="trendWardSelect">Ward</label>
                    <select
                      id="trendWardSelect"
                      value={selectedTrendWardCode || ""}
                      onChange={(event) =>
                        setSelectedTrendWardCode(event.target.value || null)
                      }
                      disabled={!trendWardList.length}
                    >
                      <option value="">
                        {trendWardLoading
                          ? "Loading wards..."
                          : "Select a ward"}
                      </option>
                      {trendWardList.map((ward) => (
                        <option key={ward.ward_code} value={ward.ward_code}>
                          {ward.ward_name} ({ward.ward_code})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Crime types</label>
                    <button
                      className={`chip-button ${
                        crimeTypeFocus === "all" ? "active" : ""
                      }`}
                      type="button"
                      onClick={() => setCrimeTypeFocus("all")}
                    >
                      All types
                    </button>
                  </div>
                </div>
                <StatusMessage error={trendWardError} />
                <div className="table scroll">
                  <div className="table-row header type-row">
                    <span>Crime type</span>
                    <span>Total</span>
                    <span>Trend %</span>
                    <span>Direction</span>
                  </div>
                  {activeCrimeTypes.length ? (
                    activeCrimeTypes.map((row) => (
                      <button
                        className={`table-row type-row ${
                          crimeTypeFocus === row.crime_type ? "active" : ""
                        }`}
                        key={row.crime_type}
                        type="button"
                        onClick={() => setCrimeTypeFocus(row.crime_type)}
                      >
                        <span>{row.crime_type_label || row.crime_type}</span>
                        <span>{formatNumber(row.total_crimes)}</span>
                        <span className={trendClass(row.trend_pct)}>
                          {formatPercent(row.trend_pct)}
                        </span>
                        <span className="trend-direction">
                          {row.trend_direction || "flat"}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="table-empty">
                      No crime type trends available yet.
                    </div>
                  )}
                </div>
              </div>

              <div className="panel glass">
                <div className="panel-header">
                  <h2>Trendline</h2>
                  <span className="tag muted">{crimeTypeLabel}</span>
                </div>
                <div className="panel-actions">
                  {[
                    { id: "count", label: "Count" },
                    { id: "rate", label: "Rate" },
                    { id: "harm", label: "Harm" },
                  ].map((option) => (
                    <button
                      key={option.id}
                      className={`chip-button ${trendMetric === option.id ? "active" : ""}`}
                      type="button"
                      onClick={() => setTrendMetric(option.id)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                {timeseriesLoading ? (
                  <StatusMessage
                    loading={timeseriesLoading}
                    loadingText="Loading trendline..."
                  />
                ) : seriesPath ? (
                  <>
                    <div className="series-chart-frame">
                      <div className="series-y-axis">
                        {seriesYAxisTicks
                          .slice()
                          .reverse()
                          .map((tick) => (
                            <span key={`y-trend-${tick.value}`}>
                              {formatSeriesValue(tick.value)}
                            </span>
                          ))}
                      </div>
                      <div className="series-chart-wrap">
                        <svg
                          className="series-chart"
                          viewBox="0 0 520 160"
                          onMouseMove={handleSeriesHover}
                          onMouseLeave={() => setSeriesHoverIndex(null)}
                        >
                          <g className="series-grid">
                            {seriesYAxisTicks.map((tick) => (
                              <line
                                key={`grid-trend-${tick.value}`}
                                x1={SERIES_VIEWBOX.padding}
                                x2={
                                  SERIES_VIEWBOX.width - SERIES_VIEWBOX.padding
                                }
                                y1={tick.y}
                                y2={tick.y}
                              />
                            ))}
                          </g>
                          <line
                            className="series-axis"
                            x1={SERIES_VIEWBOX.padding}
                            x2={SERIES_VIEWBOX.padding}
                            y1={SERIES_VIEWBOX.padding}
                            y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                          />
                          <line
                            className="series-axis"
                            x1={SERIES_VIEWBOX.padding}
                            x2={SERIES_VIEWBOX.width - SERIES_VIEWBOX.padding}
                            y1={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                            y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                          />
                          <path d={seriesPath} />
                          {seriesHoverPoint ? (
                            <>
                              <line
                                className="series-marker"
                                x1={seriesHoverPoint.x}
                                x2={seriesHoverPoint.x}
                                y1={SERIES_VIEWBOX.padding}
                                y2={SERIES_VIEWBOX.height - SERIES_VIEWBOX.padding}
                              />
                              <circle
                                className="series-dot"
                                cx={seriesHoverPoint.x}
                                cy={seriesHoverPoint.y}
                                r="4"
                              />
                            </>
                          ) : null}
                        </svg>
                        {seriesHoverPoint ? (
                          <div className="series-tooltip" style={seriesTooltipStyle}>
                            <span className="tooltip-month">
                              {seriesHoverPoint.month}
                            </span>
                            <span className="tooltip-value">
                              {formatSeriesValue(seriesHoverPoint.value)}{" "}
                              <em>{seriesLabel}</em>
                            </span>
                            <span className="tooltip-change">
                              {seriesHoverChange === null
                                ? "MoM: N/A"
                                : `MoM: ${formatSeriesValue(seriesHoverChange)}`}
                            </span>
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="series-x-axis">
                      <span>{seriesXAxisLabels.start || "Start"}</span>
                      <span>{seriesXAxisLabels.end || "End"}</span>
                    </div>
                  </>
                ) : (
                  <p className="hint">Select a ward to view trends.</p>
                )}
                <p className="hint">{seriesInsight}</p>
              </div>
            </section>
          ) : null}

          {activeView === "gap" ? (
            <>
              <section className="grid grid-2">
                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Ops Status</h2>
                    <span className="tag muted">{opsStatus?.source || "unknown"}</span>
                  </div>
                  <div className="intel-grid">
                    <div>
                      <p>Status</p>
                      <h3>{opsStatus?.status || "N/A"}</h3>
                    </div>
                    <div>
                      <p>Dataset</p>
                      <h3>{opsStatus?.dataset_version || "N/A"}</h3>
                    </div>
                    <div>
                      <p>Coverage</p>
                      <h3>
                        {opsStatus?.coverage_start || "N/A"} to{" "}
                        {opsStatus?.coverage_end || "N/A"}
                      </h3>
                    </div>
                    <div>
                      <p>Rows loaded</p>
                      <h3>{formatNumber(opsStatus?.rows_loaded)}</h3>
                    </div>
                    <div>
                      <p>Last run</p>
                      <h3>{opsStatus?.last_run || "N/A"}</h3>
                    </div>
                  </div>
                </div>

                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Quality & Gaps</h2>
                    <span className="tag muted">Coverage confidence</span>
                  </div>
                  <div className="stat-row">
                    <span>Population missing</span>
                    <strong>{formatNumber(opsQuality?.population_missing)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Short history</span>
                    <strong>{formatNumber(opsQuality?.short_history)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>High confidence</span>
                    <strong>{formatNumber(opsQuality?.confidence_counts?.high)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Medium confidence</span>
                    <strong>{formatNumber(opsQuality?.confidence_counts?.medium)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Low confidence</span>
                    <strong>{formatNumber(opsQuality?.confidence_counts?.low)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Gap months</span>
                    <strong>
                      {formatNumber(
                        opsQuality?.gap_report?.gap_months ?? gapReport?.gap_months
                      )}
                    </strong>
                  </div>
                  <div className="stat-row">
                    <span>Latest available</span>
                    <strong>
                      {opsQuality?.gap_report?.latest_available ||
                        gapReport?.latest_available ||
                        "N/A"}
                    </strong>
                  </div>
                  <div className="stat-row">
                    <span>Latest ingested</span>
                    <strong>{opsStatus?.coverage_end || "N/A"}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Crime rows</span>
                    <strong>{formatNumber(opsQuality?.crime_rows)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Ward rows</span>
                    <strong>{formatNumber(opsQuality?.ward_rows)}</strong>
                  </div>
                  <div className="stat-row">
                    <span>Invalid coords</span>
                    <strong>
                      {opsQuality?.invalid_coords_pct !== null &&
                      opsQuality?.invalid_coords_pct !== undefined
                        ? `${opsQuality.invalid_coords_pct.toFixed(2)}%`
                        : "N/A"}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="panel glass">
                <div className="panel-header">
                  <h2>Recent Jobs</h2>
                  <span className="tag muted">Last 20</span>
                </div>
                <div className="jobs-list">
                  {opsJobs.length ? (
                    opsJobs.map((job) => (
                      <div className="job-row" key={`${job.id}-${job.started_at}`}>
                        <div>
                          <p>{job.dataset_version || "Dataset"}</p>
                          <span>
                            {job.coverage_start || "N/A"} to{" "}
                            {job.coverage_end || "N/A"}
                          </span>
                          {job.status === "failed" && job.notes ? (
                            <span className="job-error">{job.notes}</span>
                          ) : null}
                        </div>
                        <div className="job-meta">
                          <span>{job.status || "unknown"}</span>
                          <span>{formatNumber(job.rows_loaded)}</span>
                          {job.log_url ? (
                            <a className="job-link" href={job.log_url} target="_blank" rel="noreferrer">
                              Log
                            </a>
                          ) : null}
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="hint">No job history available yet.</p>
                  )}
                </div>
              </section>
            </>
          ) : null}

          {activeView === "alerts" ? (
            <>
              <section className="grid grid-2">
                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Alert Inbox</h2>
                    <span className="tag muted">
                      {formatNumber(alertEvents.length)} open
                    </span>
                  </div>
                  <StatusMessage
                    loading={alertLoading}
                    error={alertError}
                    loadingText="Loading alerts..."
                  />
                  <div className="alerts-list">
                    {alertEvents.length ? (
                      alertEvents.map((event) => (
                        <div className="alert-row" key={event.id}>
                          <div>
                            <p>{event.message || event.rule_name}</p>
                            <span>
                              {event.ward_name} ({event.ward_code}){" - "}
                              {event.coverage_end || "latest"}
                            </span>
                          </div>
                          <div className="alert-meta">
                            <span>{event.rule_name}</span>
                            <span>
                              {event.observed_text ||
                                (Number.isFinite(Number(event.observed_value))
                                  ? formatNumber(event.observed_value)
                                  : "N/A")}
                            </span>
                            <button
                              className="button ghost"
                              type="button"
                              onClick={() => handleAcknowledgeAlert(event.id)}
                            >
                              Acknowledge
                            </button>
                            <button
                              className="button ghost"
                              type="button"
                              onClick={() => handleMuteRule(event.alert_rule_id, 24)}
                            >
                              Mute 24h
                            </button>
                          </div>
                        </div>
                      ))
                    ) : (
                      <p className="hint">No open alerts right now.</p>
                    )}
                  </div>
                </div>

                <div className="panel glass">
                  <div className="panel-header">
                    <h2>Create Alert Rule</h2>
                    <span className="tag muted">Notify ops</span>
                  </div>
                  <div className="alert-form">
                    <div className="filter-group">
                      <label>Name</label>
                      <input
                        value={newAlert.name}
                        placeholder="Ward enters High band"
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, name: event.target.value })
                        }
                      />
                    </div>
                    <div className="filter-group">
                      <label>Rule type</label>
                      <select
                        value={newAlert.rule_type}
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, rule_type: event.target.value })
                        }
                      >
                        <option value="ward">Ward</option>
                        <option value="filter">Filter</option>
                      </select>
                    </div>
                    {newAlert.rule_type === "ward" ? (
                      <div className="filter-group">
                        <label>Ward code</label>
                        <input
                          value={newAlert.ward_code}
                          placeholder="e.g. 95A"
                          onChange={(event) =>
                            setNewAlert({ ...newAlert, ward_code: event.target.value })
                          }
                        />
                      </div>
                    ) : null}
                    <div className="filter-group">
                      <label>Metric</label>
                      <select
                        value={newAlert.metric}
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, metric: event.target.value })
                        }
                      >
                        {alertMetricOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="filter-group">
                      <label>Operator</label>
                      <select
                        value={newAlert.operator}
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, operator: event.target.value })
                        }
                      >
                        {alertOperators.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="filter-group">
                      <label>Threshold</label>
                      <input
                        value={newAlert.threshold}
                        placeholder={selectedAlertMetric.type === "number" ? "e.g. 80" : "High"}
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, threshold: event.target.value })
                        }
                      />
                    </div>
                    <div className="filter-group">
                      <label>Trigger</label>
                      <select
                        value={newAlert.trigger_on}
                        onChange={(event) =>
                          setNewAlert({ ...newAlert, trigger_on: event.target.value })
                        }
                      >
                        <option value="enter">Enter condition</option>
                        <option value="always">Every refresh</option>
                      </select>
                    </div>
                    <div className="filter-group">
                      <label>Notify emails</label>
                      <input
                        value={newAlert.notify_emails}
                        placeholder="ops@crimemap.ai, duty@crimemap.ai"
                        onChange={(event) =>
                          setNewAlert({
                            ...newAlert,
                            notify_emails: event.target.value,
                          })
                        }
                      />
                    </div>
                    <button
                      className="button primary"
                      type="button"
                      onClick={handleCreateAlert}
                    >
                      Create alert
                    </button>
                  </div>
                </div>
              </section>

              <section className="panel glass">
                <div className="panel-header">
                  <h2>Alert Rules</h2>
                  <span className="tag muted">
                    {formatNumber(alertRules.length)} rules
                  </span>
                </div>
                <div className="alerts-list">
                  {alertRules.length ? (
                    alertRules.map((rule) => (
                      <div className="alert-row" key={rule.id}>
                        <div>
                          <p>{rule.name}</p>
                          <span>
                            {rule.rule_type === "ward"
                              ? `Ward ${rule.ward_code || "?"}`
                              : "Filter rule"}{" - "}
                            {rule.metric || "metric"} {rule.operator || ""}
                            {rule.threshold_value ||
                              (rule.threshold_number !== null &&
                              rule.threshold_number !== undefined
                                ? ` ${rule.threshold_number}`
                                : "")}
                          </span>
                        </div>
                        <div className="alert-meta">
                          <span>{rule.is_active ? "Active" : "Paused"}</span>
                          <span>
                            {rule.muted_until ? `Muted until ${rule.muted_until}` : "Live"}
                          </span>
                          <button
                            className="button ghost"
                            type="button"
                            onClick={() => handleToggleRule(rule.id, rule.is_active)}
                          >
                            {rule.is_active ? "Pause" : "Resume"}
                          </button>
                          {rule.muted_until ? (
                            <button
                              className="button ghost"
                              type="button"
                              onClick={() => handleUnmuteRule(rule.id)}
                            >
                              Unmute
                            </button>
                          ) : (
                            <button
                              className="button ghost"
                              type="button"
                              onClick={() => handleMuteRule(rule.id, 24)}
                            >
                              Mute 24h
                            </button>
                          )}
                          <button
                            className="button ghost"
                            type="button"
                            onClick={() => handleDeleteRule(rule.id)}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="hint">No alert rules yet.</p>
                  )}
                </div>
              </section>
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}
