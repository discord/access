"""index app_group app_id

Revision ID: 4e1c7b9a2d3f
Revises: 09099786c745
Create Date: 2026-10-01 12:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "4e1c7b9a2d3f"
down_revision = "09099786c745"
branch_labels = None
depends_on = None


def upgrade():
    # Plain (non-CONCURRENT) CREATE INDEX, like the okta_user_group_member
    # indexes: transactional, at the cost of briefly blocking writes to this
    # small table while it builds.
    op.create_index("ix_app_group_app_id", "app_group", ["app_id"])


def downgrade():
    op.drop_index("ix_app_group_app_id", table_name="app_group")
