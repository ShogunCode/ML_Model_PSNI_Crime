"""create ward officials table

Revision ID: 0004_create_ward_officials
Revises: 0003_expand_population_bigint
Create Date: 2026-01-18 00:00:00.000000

"""
from alembic import op

revision = "0004_create_ward_officials"
down_revision = "0003_expand_population_bigint"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE ward_officials (
            id BIGSERIAL PRIMARY KEY,
            ward_code TEXT NOT NULL REFERENCES wards(ward_code),
            official_name TEXT NOT NULL,
            role TEXT,
            party TEXT,
            email TEXT,
            phone TEXT,
            source TEXT,
            source_id TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (ward_code, official_name, role, party, email)
        );
        """
    )
    op.execute("CREATE INDEX idx_ward_officials_ward ON ward_officials (ward_code);")


def downgrade():
    op.execute("DROP TABLE IF EXISTS ward_officials;")
