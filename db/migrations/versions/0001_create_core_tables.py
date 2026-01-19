"""create core tables

Revision ID: 0001_create_core_tables
Revises:
Create Date: 2026-01-09 00:00:00.000000

"""
from alembic import op

revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    op.execute(
        """
        CREATE TABLE wards (
            ward_code TEXT PRIMARY KEY,
            ward_name TEXT,
            population INTEGER,
            geom geometry(MultiPolygon, 4326),
            centroid geometry(Point, 4326),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE crimes (
            id BIGSERIAL PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            month DATE,
            longitude DOUBLE PRECISION,
            latitude DOUBLE PRECISION,
            location TEXT,
            crime_type TEXT,
            ward_code TEXT REFERENCES wards(ward_code),
            ward_name TEXT,
            geom geometry(Point, 4326),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (dataset_version, month, longitude, latitude, location, crime_type, ward_code)
        );
        """
    )
    op.execute("CREATE INDEX idx_crimes_ward_month ON crimes (ward_code, month);")
    op.execute("CREATE INDEX idx_crimes_month ON crimes (month);")
    op.execute("CREATE INDEX idx_crimes_crime_type ON crimes (crime_type);")
    op.execute("CREATE INDEX idx_crimes_geom ON crimes USING GIST (geom);")

    op.execute(
        """
        CREATE TABLE ward_metrics (
            id BIGSERIAL PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            coverage_start DATE,
            coverage_end DATE NOT NULL,
            ward_code TEXT NOT NULL REFERENCES wards(ward_code),
            ward_name TEXT,
            population INTEGER,
            number_of_crimes INTEGER,
            crime_rate_per_100k DOUBLE PRECISION,
            rate_percentile DOUBLE PRECISION,
            rate_rank INTEGER,
            high_crime_rate BOOLEAN,
            total_crimes INTEGER,
            avg_monthly DOUBLE PRECISION,
            trend_change DOUBLE PRECISION,
            trend_pct DOUBLE PRECISION,
            trend_slope DOUBLE PRECISION,
            yoy_current INTEGER,
            yoy_prior INTEGER,
            yoy_change DOUBLE PRECISION,
            total_harm DOUBLE PRECISION,
            harm_score_per_100k DOUBLE PRECISION,
            months INTEGER,
            first_month DATE,
            last_month DATE,
            rating_score DOUBLE PRECISION,
            rating_band TEXT,
            trend_percentile DOUBLE PRECISION,
            annualized_crime_rate_per_100k DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (dataset_version, coverage_end, ward_code)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_ward_metrics_dataset ON ward_metrics (dataset_version, coverage_end);"
    )
    op.execute("CREATE INDEX idx_ward_metrics_rating ON ward_metrics (rating_score);")
    op.execute("CREATE INDEX idx_ward_metrics_trend ON ward_metrics (trend_slope);")

    op.execute(
        """
        CREATE TABLE ward_type_metrics (
            id BIGSERIAL PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            coverage_start DATE,
            coverage_end DATE NOT NULL,
            ward_code TEXT NOT NULL REFERENCES wards(ward_code),
            ward_name TEXT,
            crime_type TEXT NOT NULL,
            total_crimes INTEGER,
            avg_monthly DOUBLE PRECISION,
            trend_change DOUBLE PRECISION,
            trend_pct DOUBLE PRECISION,
            trend_slope DOUBLE PRECISION,
            months INTEGER,
            first_month DATE,
            last_month DATE,
            trend_direction TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (dataset_version, coverage_end, ward_code, crime_type)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_ward_type_metrics_dataset ON ward_type_metrics "
        "(dataset_version, coverage_end);"
    )
    op.execute(
        "CREATE INDEX idx_ward_type_metrics_crime ON ward_type_metrics (crime_type);"
    )

    op.execute(
        """
        CREATE TABLE gap_report (
            id BIGSERIAL PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            coverage_end DATE NOT NULL,
            checked_at TIMESTAMPTZ,
            history_latest DATE,
            latest_available DATE,
            gap_months INTEGER,
            default_start_month DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (dataset_version, coverage_end)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE job_runs (
            id BIGSERIAL PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            coverage_start DATE,
            coverage_end DATE,
            status TEXT NOT NULL,
            source TEXT,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            rows_loaded INTEGER,
            notes TEXT
        );
        """
    )
    op.execute("CREATE INDEX idx_job_runs_status ON job_runs (status);")
    op.execute("CREATE INDEX idx_job_runs_coverage_end ON job_runs (coverage_end);")
    op.execute("CREATE INDEX idx_job_runs_started_at ON job_runs (started_at);")


def downgrade():
    op.execute("DROP TABLE IF EXISTS ward_type_metrics;")
    op.execute("DROP TABLE IF EXISTS ward_metrics;")
    op.execute("DROP TABLE IF EXISTS gap_report;")
    op.execute("DROP TABLE IF EXISTS crimes;")
    op.execute("DROP TABLE IF EXISTS job_runs;")
    op.execute("DROP TABLE IF EXISTS wards;")
    op.execute("DROP EXTENSION IF EXISTS postgis;")
