"""Add pending_app_group_deletion outbox

Revision ID: 9fd155b4a911
Revises: c8f9ba49f867
Create Date: 2026-08-20 15:36:08.903491

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "9fd155b4a911"
down_revision = "c8f9ba49f867"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pending_app_group_deletion",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("group_id", sa.Unicode(length=50), nullable=False),
        sa.Column("plugin_id", sa.Unicode(length=255), nullable=False),
        sa.Column(
            "member_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Unicode(length=1024), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["okta_group.id"],
            name=op.f("fk_pending_app_group_deletion_group_id_okta_group"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_app_group_deletion")),
        # One outstanding delivery per (group, plugin): re-deleting a group already awaiting
        # delivery must not queue a second one.
        sa.UniqueConstraint("group_id", "plugin_id", name="group_id_plugin_id"),
    )


def downgrade():
    op.drop_table("pending_app_group_deletion")
