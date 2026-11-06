"""deletion requests

Revision ID: 7ed9cde00dae
Revises: e0fd98888f81
Create Date: 2026-11-06 09:40:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7ed9cde00dae"
down_revision = "e0fd98888f81"
branch_labels = None
depends_on = None


def upgrade():
    status_enum: sa.Enum
    if op.get_bind().dialect.name == "postgresql":
        # The type already exists from the request tables.
        status_enum = postgresql.ENUM("PENDING", "APPROVED", "REJECTED", name="accessrequeststatus", create_type=False)
    else:
        status_enum = sa.Enum("PENDING", "APPROVED", "REJECTED", name="accessrequeststatus")

    op.create_table(
        "deletion_request",
        sa.Column("id", sa.Unicode(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("requester_user_id", sa.Unicode(length=50), nullable=True),
        sa.Column("requester_service_principal_id", sa.Unicode(length=50), nullable=True),
        sa.Column("group_id", sa.Unicode(length=50), nullable=True),
        sa.Column("app_id", sa.Unicode(length=20), nullable=True),
        sa.Column("request_reason", sa.Unicode(length=1024), nullable=False),
        sa.Column("resolver_user_id", sa.Unicode(length=50), nullable=True),
        sa.Column("resolver_service_principal_id", sa.Unicode(length=50), nullable=True),
        sa.Column("resolution_reason", sa.Unicode(length=1024), nullable=False),
        sa.CheckConstraint(
            "(requester_user_id IS NULL) != (requester_service_principal_id IS NULL)",
            name=op.f("ck_deletion_request_exactly_one_requester"),
        ),
        sa.CheckConstraint(
            "(group_id IS NULL) != (app_id IS NULL)", name=op.f("ck_deletion_request_exactly_one_target")
        ),
        sa.ForeignKeyConstraint(["app_id"], ["app.id"], name=op.f("fk_deletion_request_app_id_app")),
        sa.ForeignKeyConstraint(["group_id"], ["okta_group.id"], name=op.f("fk_deletion_request_group_id_okta_group")),
        sa.ForeignKeyConstraint(
            ["requester_service_principal_id"],
            ["service_principal.id"],
            name=op.f("fk_deletion_request_requester_service_principal_id_service_principal"),
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"], ["okta_user.id"], name=op.f("fk_deletion_request_requester_user_id_okta_user")
        ),
        sa.ForeignKeyConstraint(
            ["resolver_user_id"], ["okta_user.id"], name=op.f("fk_deletion_request_resolver_user_id_okta_user")
        ),
        sa.ForeignKeyConstraint(
            ["resolver_service_principal_id"],
            ["service_principal.id"],
            name=op.f("fk_deletion_request_resolver_service_principal_id_service_principal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_request")),
    )


def downgrade():
    op.drop_table("deletion_request")
