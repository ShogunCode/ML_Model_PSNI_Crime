"""create alerts tables

Revision ID: 0002_create_alerts_tables
Revises: 0001_create_core_tables
Create Date: 2026-01-17 00:00:00.000000
"""
from alembic import op

revision = "0002_create_alerts_tables"
down_revision = "0001_create_core_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE alert_rules (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            rule_type TEXT NOT NULL,
            ward_code TEXT,
            metric TEXT,
            operator TEXT,
            threshold_value TEXT,
            threshold_number DOUBLE PRECISION,
            filter_json JSONB,
            trigger_on TEXT NOT NULL DEFAULT 'enter',
            window_months INTEGER,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            muted_until TIMESTAMPTZ,
            notify_emails TEXT[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX idx_alert_rules_active ON alert_rules (is_active);")
    op.execute("CREATE INDEX idx_alert_rules_ward_code ON alert_rules (ward_code);")

    op.execute(
        """
        CREATE TABLE alert_events (
            id BIGSERIAL PRIMARY KEY,
            alert_rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
            dataset_version TEXT,
            coverage_end DATE,
            ward_code TEXT,
            ward_name TEXT,
            metric TEXT,
            operator TEXT,
            threshold_value TEXT,
            threshold_number DOUBLE PRECISION,
            observed_value DOUBLE PRECISION,
            observed_text TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            message TEXT,
            value_json JSONB,
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (alert_rule_id, coverage_end, ward_code)
        );
        """
    )
    op.execute("CREATE INDEX idx_alert_events_rule ON alert_events (alert_rule_id);")
    op.execute("CREATE INDEX idx_alert_events_status ON alert_events (status);")
    op.execute("CREATE INDEX idx_alert_events_ward ON alert_events (ward_code);")
    op.execute("CREATE INDEX idx_alert_events_coverage ON alert_events (coverage_end);")

    op.execute(
        """
        CREATE TABLE alert_notifications (
            id BIGSERIAL PRIMARY KEY,
            alert_event_id BIGINT NOT NULL REFERENCES alert_events(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            recipient TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            sent_at TIMESTAMPTZ,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_alert_notifications_event ON alert_notifications (alert_event_id);"
    )

    op.execute(
        """
        CREATE TABLE alert_rule_runs (
            id BIGSERIAL PRIMARY KEY,
            alert_rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
            dataset_version TEXT,
            coverage_end DATE,
            evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            matched_count INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_alert_rule_runs_rule ON alert_rule_runs (alert_rule_id);"
    )
    op.execute(
        "CREATE INDEX idx_alert_rule_runs_coverage ON alert_rule_runs (coverage_end);"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS alert_rule_runs;")
    op.execute("DROP TABLE IF EXISTS alert_notifications;")
    op.execute("DROP TABLE IF EXISTS alert_events;")
    op.execute("DROP TABLE IF EXISTS alert_rules;")
