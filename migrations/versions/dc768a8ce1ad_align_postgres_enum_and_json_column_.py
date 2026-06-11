"""align postgres enum and json column types with models

Revision ID: dc768a8ce1ad
Revises: 11a0117dabea
Create Date: 2026-06-11 12:28:16.771725

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "dc768a8ce1ad"
down_revision = "11a0117dabea"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # On SQLite the status column is a CHECK-constrained VARCHAR and
        # plugin_data is plain TEXT/JSON, so there is no distinct named type
        # to reconcile.
        return

    # The initial migration created `access_request.status` with the enum
    # type named `accessrequeststate`, but the models and every other request
    # table (`role_request`, `group_request`) use `accessrequeststatus`. Move
    # the column onto the canonical type and drop the now-unused one.
    op.execute(
        "ALTER TABLE access_request "
        "ALTER COLUMN status TYPE accessrequeststatus "
        "USING status::text::accessrequeststatus"
    )
    op.execute("DROP TYPE accessrequeststate")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE TYPE accessrequeststate AS ENUM ('PENDING', 'APPROVED', 'REJECTED')")
    op.execute(
        "ALTER TABLE access_request "
        "ALTER COLUMN status TYPE accessrequeststate "
        "USING status::text::accessrequeststate"
    )
