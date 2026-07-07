"""index okta_user_group_member group lookups

Revision ID: 61afb5496c0a
Revises: dc768a8ce1ad
Create Date: 2026-07-07 10:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "61afb5496c0a"
down_revision = "dc768a8ce1ad"
branch_labels = None
depends_on = None


def upgrade():
    # Plain (non-CONCURRENT) CREATE INDEX, deliberately: it stays transactional
    # (clean rollback, no INVALID index to clean up) at the cost of blocking
    # writes to this one table while the index builds. Reads are unaffected,
    # and write volume here is low enough that queued writes clear trivially.
    # If the migration hangs waiting on a lock, cancelling it is safe - it
    # rolls back cleanly and can simply be re-run.
    op.create_index(
        "idx_okta_user_group_member_group_id_is_owner_ended_at",
        "okta_user_group_member",
        ["group_id", "is_owner", "ended_at"],
    )


def downgrade():
    op.drop_index(
        "idx_okta_user_group_member_group_id_is_owner_ended_at",
        table_name="okta_user_group_member",
    )
