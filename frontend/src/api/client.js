/**
 * @typedef {Object} SummaryResponse
 * @property {string=} latest_month
 * @property {number=} avg_rate_per_100k
 * @property {Object.<string, number>=} band_counts
 * @property {number=} total_wards
 * @property {number=} total_crimes
 */

/**
 * @typedef {Object} GapReport
 * @property {string=} checked_at
 * @property {string=} history_latest
 * @property {string=} latest_available
 * @property {number=} gap_months
 * @property {string=} default_start_month
 */

/**
 * @typedef {Object} MapInfo
 * @property {string} wards_map_url
 * @property {boolean} exists
 */

/**
 * @typedef {Object} OpsStatus
 * @property {string=} status
 * @property {string=} dataset_version
 * @property {string=} coverage_start
 * @property {string=} coverage_end
 * @property {number=} rows_loaded
 * @property {string=} last_run
 * @property {string=} source
 */

/**
 * @typedef {Object} OpsJob
 * @property {number=} id
 * @property {string=} dataset_version
 * @property {string=} coverage_start
 * @property {string=} coverage_end
 * @property {string=} status
 * @property {string=} source
 * @property {string=} started_at
 * @property {string=} finished_at
 * @property {number=} rows_loaded
 * @property {string=} notes
 * @property {string=} log_url
 */

/**
 * @typedef {Object} OpsJobsResponse
 * @property {string=} source
 * @property {OpsJob[]=} jobs
 */

/**
 * @typedef {Object} OpsQualityResponse
 * @property {string=} source
 * @property {string=} dataset_version
 * @property {string=} coverage_start
 * @property {string=} coverage_end
 * @property {number=} population_missing
 * @property {number=} population_missing_pct
 * @property {number=} short_history
 * @property {number=} short_history_pct
 * @property {number=} invalid_coords
 * @property {number=} invalid_coords_pct
 * @property {number=} crime_rows
 * @property {number=} ward_rows
 * @property {Object.<string, number>=} confidence_counts
 */

/**
 * @typedef {Object} WardRow
 * @property {string} ward_code
 * @property {string} ward_name
 * @property {number=} population
 * @property {number=} crime_rate_per_100k
 * @property {number=} trend_pct
 * @property {number=} trend_slope
 * @property {number=} yoy_change
 * @property {number=} harm_score_per_100k
 * @property {number=} rating_score
 * @property {string=} rating_band
 */

/**
 * @typedef {Object} WardListResponse
 * @property {WardRow[]} items
 * @property {number} total
 * @property {number} limit
 * @property {number} offset
 */

/**
 * @typedef {Object} WardDetailResponse
 * @property {WardRow} ward
 * @property {Object} rating_explain
 * @property {Array<Object>} crime_types
 * @property {Array<Object>} officials
 */

/**
 * @typedef {Object} TimeSeriesPoint
 * @property {string} month
 * @property {number=} value
 */

/**
 * @typedef {Object} TimeSeriesSummary
 * @property {number} window
 * @property {number=} latest_avg
 * @property {number=} prior_avg
 * @property {number=} change
 * @property {number=} pct_change
 * @property {string=} direction
 */

/**
 * @typedef {Object} TimeSeriesResponse
 * @property {string} ward_code
 * @property {string} ward_name
 * @property {string} metric
 * @property {string=} crime_type
 * @property {TimeSeriesPoint[]} points
 * @property {TimeSeriesSummary} summary
 */

/**
 * @typedef {Object} AlertRule
 * @property {number} id
 * @property {string} name
 * @property {string} rule_type
 * @property {string=} ward_code
 * @property {string=} metric
 * @property {string=} operator
 * @property {string=} threshold_value
 * @property {number=} threshold_number
 * @property {boolean=} is_active
 */

/**
 * @typedef {Object} AlertEvent
 * @property {number} id
 * @property {number} alert_rule_id
 * @property {string=} ward_code
 * @property {string=} ward_name
 * @property {string=} status
 * @property {string=} message
 * @property {string=} triggered_at
 * @property {string=} acknowledged_at
 */

/**
 * @typedef {Object} AlertEventsResponse
 * @property {AlertEvent[]} items
 * @property {number} total
 * @property {number} limit
 * @property {number} offset
 */

export const API_BASE =
  import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_V2 = `${API_BASE}/api/v2`;
const OPS_BASE = `${API_BASE}/ops`;

export const assets = {
  wardsMapUrl: `${API_BASE}/assets/wards_interactive_map.html`,
};

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
};

const withQuery = (baseUrl, params) => {
  if (!params) return baseUrl;
  const query = params.toString();
  if (!query) return baseUrl;
  return `${baseUrl}?${query}`;
};

export const api = {
  /** @returns {Promise<SummaryResponse>} */
  getSummary: (signal) => requestJson(`${API_BASE}/api/summary`, { signal }),
  /** @returns {Promise<GapReport>} */
  getGapReport: (signal) => requestJson(`${API_BASE}/api/gap-report`, { signal }),
  /** @returns {Promise<MapInfo>} */
  getMap: (signal) => requestJson(`${API_BASE}/api/map`, { signal }),
  /** @returns {Promise<OpsStatus>} */
  getOpsStatus: (signal) => requestJson(`${OPS_BASE}/status`, { signal }),
  /** @returns {Promise<OpsQualityResponse>} */
  getOpsQuality: (signal) => requestJson(`${OPS_BASE}/quality`, { signal }),
  /** @returns {Promise<OpsJobsResponse>} */
  getOpsJobs: (limit = 20, signal) =>
    requestJson(withQuery(`${OPS_BASE}/jobs`, new URLSearchParams({ limit })), {
      signal,
    }),
  /** @returns {Promise<WardListResponse>} */
  listWards: (params, signal) =>
    requestJson(withQuery(`${API_V2}/wards`, params), { signal }),
  /** @returns {Promise<WardDetailResponse>} */
  getWardDetail: (wardCode, signal) =>
    requestJson(`${API_V2}/wards/${encodeURIComponent(wardCode)}`, { signal }),
  /** @returns {Promise<TimeSeriesResponse>} */
  getWardTimeseries: (wardCode, params, signal) =>
    requestJson(
      withQuery(`${API_V2}/wards/${encodeURIComponent(wardCode)}/timeseries`, params),
      { signal }
    ),
  /** @returns {Promise<AlertRule[]>} */
  listAlertRules: (options = {}) => {
    const params = new URLSearchParams();
    if (options.limit) params.set("limit", options.limit);
    if (options.offset) params.set("offset", options.offset);
    return requestJson(withQuery(`${API_V2}/alerts/rules`, params), {
      signal: options.signal,
    });
  },
  /** @returns {Promise<AlertEventsResponse>} */
  listAlertEvents: (options = {}) => {
    const params = new URLSearchParams();
    if (options.status) params.set("status", options.status);
    if (options.limit) params.set("limit", options.limit);
    if (options.offset) params.set("offset", options.offset);
    return requestJson(withQuery(`${API_V2}/alerts/events`, params), {
      signal: options.signal,
    });
  },
  /** @returns {Promise<AlertRule>} */
  createAlertRule: (payload) =>
    requestJson(`${API_V2}/alerts/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  /** @returns {Promise<{status: string}>} */
  acknowledgeAlertEvent: (eventId) =>
    requestJson(`${API_V2}/alerts/events/${eventId}/acknowledge`, {
      method: "POST",
    }),
  /** @returns {Promise<{status: string}>} */
  muteAlertRule: (ruleId, payload) =>
    requestJson(`${API_V2}/alerts/rules/${ruleId}/mute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  /** @returns {Promise<{status: string}>} */
  unmuteAlertRule: (ruleId) =>
    requestJson(`${API_V2}/alerts/rules/${ruleId}/unmute`, {
      method: "POST",
    }),
  /** @returns {Promise<AlertRule>} */
  updateAlertRule: (ruleId, payload) =>
    requestJson(`${API_V2}/alerts/rules/${ruleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  /** @returns {Promise<{status: string}>} */
  deleteAlertRule: (ruleId) =>
    requestJson(`${API_V2}/alerts/rules/${ruleId}`, {
      method: "DELETE",
    }),
};
