"""app requests

Revision ID: e0fd98888f81
Revises: c6344ca4f8b1
Create Date: 2026-11-06 09:18:39.786698

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e0fd98888f81"
down_revision = "c6344ca4f8b1"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    status_enum: sa.Enum
    if op.get_bind().dialect.name == "postgresql":
        # The type already exists from the request tables.
        status_enum = postgresql.ENUM("PENDING", "APPROVED", "REJECTED", name="accessrequeststatus", create_type=False)
    else:
        status_enum = sa.Enum("PENDING", "APPROVED", "REJECTED", name="accessrequeststatus")

    op.create_table(
        "app_request",
        sa.Column("id", sa.Unicode(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("requester_user_id", sa.Unicode(length=50), nullable=True),
        sa.Column("requester_service_principal_id", sa.Unicode(length=50), nullable=True),
        sa.Column("requested_app_name", sa.Unicode(length=255), nullable=False),
        sa.Column("requested_app_description", sa.Unicode(length=1024), nullable=False),
        sa.Column("requested_app_tags", _json(), server_default="[]", nullable=False),
        sa.Column("requested_owner_user_ids", _json(), server_default="[]", nullable=False),
        sa.Column("requested_owner_role_ids", _json(), server_default="[]", nullable=False),
        sa.Column("requested_app_groups", _json(), server_default="[]", nullable=False),
        sa.Column("requested_ownership_ending_at", sa.DateTime(), nullable=True),
        sa.Column("requested_membership_ending_at", sa.DateTime(), nullable=True),
        sa.Column("request_reason", sa.Unicode(length=1024), nullable=False),
        sa.Column("resolver_user_id", sa.Unicode(length=50), nullable=True),
        sa.Column("resolved_ownership_ending_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_membership_ending_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_reason", sa.Unicode(length=1024), nullable=False),
        sa.Column("approved_app_id", sa.Unicode(length=20), nullable=True),
        sa.CheckConstraint(
            "(requester_user_id IS NULL) != (requester_service_principal_id IS NULL)",
            name=op.f("ck_app_request_exactly_one_requester"),
        ),
        sa.ForeignKeyConstraint(["approved_app_id"], ["app.id"], name=op.f("fk_app_request_approved_app_id_app")),
        sa.ForeignKeyConstraint(
            ["requester_service_principal_id"],
            ["service_principal.id"],
            name=op.f("fk_app_request_requester_service_principal_id_service_principal"),
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"], ["okta_user.id"], name=op.f("fk_app_request_requester_user_id_okta_user")
        ),
        sa.ForeignKeyConstraint(
            ["resolver_user_id"], ["okta_user.id"], name=op.f("fk_app_request_resolver_user_id_okta_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_request")),
    )
    with op.batch_alter_table("app", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by_service_principal_id", sa.Unicode(length=50), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_app_created_by_service_principal_id_service_principal"),
            "service_principal",
            ["created_by_service_principal_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("app", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_app_created_by_service_principal_id_service_principal"), type_="foreignkey"
        )
        batch_op.drop_column("created_by_service_principal_id")
    op.drop_table("app_request")
