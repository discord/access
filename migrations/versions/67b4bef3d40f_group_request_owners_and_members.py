"""group request owners and members

Revision ID: 67b4bef3d40f
Revises: c76de18489a8
Create Date: 2026-11-06 08:57:47.494501

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "67b4bef3d40f"
down_revision = "c76de18489a8"
branch_labels = None
depends_on = None

_LIST_COLUMNS = (
    "requested_owner_user_ids",
    "requested_owner_role_ids",
    "requested_member_user_ids",
    "requested_member_role_ids",
    "resolved_owner_user_ids",
    "resolved_owner_role_ids",
    "resolved_member_user_ids",
    "resolved_member_role_ids",
)


def upgrade():
    with op.batch_alter_table("group_request", schema=None) as batch_op:
        for name in _LIST_COLUMNS:
            batch_op.add_column(
                sa.Column(
                    name,
                    sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                    server_default="[]",
                    nullable=False,
                )
            )
        batch_op.add_column(sa.Column("requested_membership_ending_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("resolved_membership_ending_at", sa.DateTime(), nullable=True))

    # Existing requests named no owners: the requester became the owner on approval.
    as_array = "jsonb_build_array" if op.get_bind().dialect.name == "postgresql" else "json_array"
    op.execute(f"UPDATE group_request SET requested_owner_user_ids = {as_array}(requester_user_id)")
    op.execute(
        f"UPDATE group_request SET resolved_owner_user_ids = {as_array}(requester_user_id) WHERE status = 'APPROVED'"
    )


def downgrade():
    with op.batch_alter_table("group_request", schema=None) as batch_op:
        batch_op.drop_column("resolved_membership_ending_at")
        batch_op.drop_column("requested_membership_ending_at")
        for name in reversed(_LIST_COLUMNS):
            batch_op.drop_column(name)
