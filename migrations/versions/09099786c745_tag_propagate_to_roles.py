"""tag propagate_to_roles

Revision ID: 09099786c745
Revises: c8f9ba49f867
Create Date: 2026-08-23 18:16:19.279682

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import expression


# revision identifiers, used by Alembic.
revision = "09099786c745"
down_revision = "c8f9ba49f867"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tag", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "propagate_to_roles",
                sa.Boolean(),
                server_default=expression.true(),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("tag", schema=None) as batch_op:
        batch_op.drop_column("propagate_to_roles")
