"""group_request plugin_data

Revision ID: 128a7f45cac6
Revises: 98bc5533e0f9
Create Date: 2026-06-27 06:09:07.323973

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "128a7f45cac6"
down_revision = "98bc5533e0f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group_request",
        sa.Column(
            "requested_plugin_data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "group_request",
        sa.Column(
            "resolved_plugin_data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("group_request", "resolved_plugin_data")
    op.drop_column("group_request", "requested_plugin_data")
