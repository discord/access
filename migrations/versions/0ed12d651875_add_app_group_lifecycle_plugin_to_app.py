"""add app_group_lifecycle_plugin to app

Revision ID: 0ed12d651875
Revises: cbc5bb2f05b7
Create Date: 2025-10-24 21:03:01.388376

"""

import sqlalchemy as sa
from alembic import op

revision = "0ed12d651875"
down_revision = "cbc5bb2f05b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("app", schema=None) as batch_op:
        batch_op.add_column(sa.Column("app_group_lifecycle_plugin", sa.Unicode(length=255), nullable=True))
        batch_op.add_column(sa.Column("plugin_data", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("app", schema=None) as batch_op:
        batch_op.drop_column("plugin_data")
        batch_op.drop_column("app_group_lifecycle_plugin")
