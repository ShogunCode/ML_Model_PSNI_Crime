CREATE TABLE IF NOT EXISTS crime_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  month DATE,
  longitude DECIMAL(9, 6),
  latitude DECIMAL(9, 6),
  location VARCHAR(255),
  crime_type VARCHAR(64),
  ward_code VARCHAR(16),
  ward_name VARCHAR(128),
  UNIQUE KEY uniq_crime (month, longitude, latitude, location, crime_type, ward_code),
  KEY idx_ward_month (ward_code, month),
  KEY idx_month (month),
  KEY idx_crime_type (crime_type)
);

CREATE TABLE IF NOT EXISTS ward_analysis (
  ward_code VARCHAR(16) NOT NULL PRIMARY KEY,
  ward_name VARCHAR(128),
  population INT,
  number_of_crimes INT,
  crime_rate_per_100k DOUBLE,
  rate_percentile DOUBLE,
  rate_rank INT,
  high_crime_rate BOOLEAN,
  total_crimes INT,
  avg_monthly DOUBLE,
  trend_change DOUBLE,
  trend_pct DOUBLE,
  trend_slope DOUBLE,
  yoy_current INT,
  yoy_prior INT,
  yoy_change DOUBLE,
  total_harm DOUBLE,
  harm_score_per_100k DOUBLE,
  months INT,
  first_month VARCHAR(7),
  last_month VARCHAR(7)
);

CREATE TABLE IF NOT EXISTS ward_crime_type_trends (
  ward_code VARCHAR(16) NOT NULL,
  ward_name VARCHAR(128),
  crime_type VARCHAR(64) NOT NULL,
  total_crimes INT,
  avg_monthly DOUBLE,
  trend_change DOUBLE,
  trend_pct DOUBLE,
  trend_slope DOUBLE,
  months INT,
  first_month VARCHAR(7),
  last_month VARCHAR(7),
  trend_direction VARCHAR(16),
  PRIMARY KEY (ward_code, crime_type)
);

CREATE TABLE IF NOT EXISTS police_api_gap_report (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  checked_at DATETIME,
  history_latest VARCHAR(7),
  latest_available VARCHAR(7),
  gap_months INT,
  default_start_month VARCHAR(7)
);

CREATE TABLE IF NOT EXISTS ingest_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source VARCHAR(32),
  started_at DATETIME,
  finished_at DATETIME,
  rows_loaded INT,
  notes VARCHAR(255)
);
