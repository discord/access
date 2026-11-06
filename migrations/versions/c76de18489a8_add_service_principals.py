"""add service principals

Revision ID: c76de18489a8
Revises: 09099786c745
Create Date: 2026-11-06 08:44:04.212012

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c76de18489a8"
down_revision = "09099786c745"
branch_labels = None
depends_on = None

_PROVIDER_ENUM = sa.Enum("CLOUDFLARE", name="serviceprincipalprovider")


def upgrade():
    op.create_table(
        "service_principal",
        sa.Column("id", sa.Unicode(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("display_name", sa.Unicode(length=100), nullable=False),
        sa.Column("provider", _PROVIDER_ENUM, nullable=False),
        sa.Column("subject", sa.Unicode(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_principal")),
    )
    with op.batch_alter_table("service_principal", schema=None) as batch_op:
        batch_op.create_index(
            "idx_service_principal_provider_subject",
            ["provider", "subject"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )

    op.create_table(
        "service_principal_owner",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("service_principal_id", sa.Unicode(length=50), nullable=False),
        sa.Column("user_id", sa.Unicode(length=50), nullable=True),
        sa.Column("role_group_id", sa.Unicode(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NULL) != (role_group_id IS NULL)",
            name=op.f("ck_service_principal_owner_exactly_one_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["role_group_id"], ["role_group.id"], name=op.f("fk_service_principal_owner_role_group_id_role_group")
        ),
        sa.ForeignKeyConstraint(
            ["service_principal_id"],
            ["service_principal.id"],
            name=op.f("fk_service_principal_owner_service_principal_id_service_principal"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["okta_user.id"], name=op.f("fk_service_principal_owner_user_id_okta_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_principal_owner")),
    )


def downgrade():
    op.drop_table("service_principal_owner")
    with op.batch_alter_table("service_principal", schema=None) as batch_op:
        batch_op.drop_index(
            "idx_service_principal_provider_subject",
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
    op.drop_table("service_principal")
    if op.get_bind().dialect.name == "postgresql":
        _PROVIDER_ENUM.drop(op.get_bind(), checkfirst=True)
