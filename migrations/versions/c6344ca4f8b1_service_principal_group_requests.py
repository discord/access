"""service principal group requests

Revision ID: c6344ca4f8b1
Revises: 67b4bef3d40f
Create Date: 2026-11-06 09:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c6344ca4f8b1"
down_revision = "67b4bef3d40f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("group_request", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requester_service_principal_id", sa.Unicode(length=50), nullable=True))
        batch_op.alter_column("requester_user_id", existing_type=sa.Unicode(length=50), nullable=True)
        batch_op.create_foreign_key(
            batch_op.f("fk_group_request_requester_service_principal_id_service_principal"),
            "service_principal",
            ["requester_service_principal_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_group_request_exactly_one_requester"),
            "(requester_user_id IS NULL) != (requester_service_principal_id IS NULL)",
        )

    with op.batch_alter_table("okta_group", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by_service_principal_id", sa.Unicode(length=50), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_okta_group_created_by_service_principal_id_service_principal"),
            "service_principal",
            ["created_by_service_principal_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("okta_group", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_okta_group_created_by_service_principal_id_service_principal"), type_="foreignkey"
        )
        batch_op.drop_column("created_by_service_principal_id")

    # Requests from service principals have no user requester to keep.
    op.execute("DELETE FROM group_request WHERE requester_user_id IS NULL")
    with op.batch_alter_table("group_request", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_group_request_exactly_one_requester"), type_="check")
        batch_op.drop_constraint(
            batch_op.f("fk_group_request_requester_service_principal_id_service_principal"), type_="foreignkey"
        )
        batch_op.alter_column("requester_user_id", existing_type=sa.Unicode(length=50), nullable=False)
        batch_op.drop_column("requester_service_principal_id")
