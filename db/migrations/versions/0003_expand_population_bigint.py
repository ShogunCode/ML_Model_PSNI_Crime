"""expand population columns to bigint

Revision ID: 0003_expand_population_bigint
Revises: 0002_create_alerts_tables
Create Date: 2026-01-17 00:00:00.000000
"""
from alembic import op

revision = "0003_expand_population_bigint"
down_revision = "0002_create_alerts_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE wards ALTER COLUMN population TYPE BIGINT;")
    op.execute("ALTER TABLE ward_metrics ALTER COLUMN population TYPE BIGINT;")


def downgrade():
    op.execute("ALTER TABLE ward_metrics ALTER COLUMN population TYPE INTEGER;")
    op.execute("ALTER TABLE wards ALTER COLUMN population TYPE INTEGER;")
