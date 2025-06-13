"""manually update indexes

Revision ID: cdcadbe08ab3
Revises: 6d2a03b326f9
Create Date: 2025-06-13 09:44:45.673469

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cdcadbe08ab3"
down_revision = "6d2a03b326f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("idx_okta_user_group_member_user_id", "okta_user_group_member", ["user_id"])
    op.create_index("idx_okta_user_group_member_group_id", "okta_user_group_member", ["group_id"])
    op.create_index("idx_okta_user_group_member_role_group_map_id", "okta_user_group_member", ["role_group_map_id"])
    op.create_index("idx_okta_user_group_member_created_actor_id", "okta_user_group_member", ["created_actor_id"])
    op.create_index("idx_okta_user_group_member_ended_actor_id", "okta_user_group_member", ["ended_actor_id"])

    op.create_index("idx_access_request_requester_user_id", "access_request", ["requester_user_id"])
    op.create_index("idx_access_request_requested_group_id", "access_request", ["requested_group_id"])
    op.create_index("idx_access_request_resolver_user_id", "access_request", ["resolver_user_id"])
    op.create_index("idx_access_request_approved_membership_id", "access_request", ["approved_membership_id"])

    op.create_index("idx_role_request_requester_user_id", "role_request", ["requester_user_id"])
    op.create_index("idx_role_request_requester_role_id", "role_request", ["requester_role_id"])
    op.create_index("idx_role_request_requested_group_id", "role_request", ["requested_group_id"])
    op.create_index("idx_role_request_resolver_user_id", "role_request", ["resolver_user_id"])
    op.create_index("idx_role_request_approved_membership_id", "role_request", ["approved_membership_id"])

    op.create_index("idx_role_group_map_role_id", "role_group_map", ["role_id"])
    op.create_index("idx_role_group_map_group_id", "role_group_map", ["group_id"])
    op.create_index("idx_role_group_map_created_actor_id", "role_group_map", ["created_actor_id"])
    op.create_index("idx_role_group_map_ended_actor_id", "role_group_map", ["ended_actor_id"])

    op.create_index("idx_app_group_app_id", "app_group", ["app_id"])
    op.create_index("idx_okta_user_manager_id", "okta_user", ["manager_id"])

    op.create_index("idx_okta_user_group_member_user_group", "okta_user_group_member", ["user_id", "group_id"])
    op.create_index("idx_okta_user_group_member_group_ended", "okta_user_group_member", ["group_id", "ended_at"])
    op.create_index("idx_access_request_status_resolved", "access_request", ["status", "resolved_at"])


def downgrade():
    op.drop_index("idx_okta_user_group_member_user_id", "okta_user_group_member")
    op.drop_index("idx_okta_user_group_member_group_id", "okta_user_group_member")
    op.drop_index("idx_okta_user_group_member_role_group_map_id", "okta_user_group_member")
    op.drop_index("idx_okta_user_group_member_created_actor_id", "okta_user_group_member")
    op.drop_index("idx_okta_user_group_member_ended_actor_id", "okta_user_group_member")

    op.drop_index("idx_access_request_requester_user_id", "access_request")
    op.drop_index("idx_access_request_requested_group_id", "access_request")
    op.drop_index("idx_access_request_resolver_user_id", "access_request")
    op.drop_index("idx_access_request_approved_membership_id", "access_request")

    op.drop_index("idx_role_request_requester_user_id", "role_request")
    op.drop_index("idx_role_request_requester_role_id", "role_request")
    op.drop_index("idx_role_request_requested_group_id", "role_request")
    op.drop_index("idx_role_request_resolver_user_id", "role_request")
    op.drop_index("idx_role_request_approved_membership_id", "role_request")

    op.drop_index("idx_role_group_map_role_id", "role_group_map")
    op.drop_index("idx_role_group_map_group_id", "role_group_map")
    op.drop_index("idx_role_group_map_created_actor_id", "role_group_map")
    op.drop_index("idx_role_group_map_ended_actor_id", "role_group_map")

    op.drop_index("idx_app_group_app_id", "app_group")
    op.drop_index("idx_okta_user_manager_id", "okta_user")

    op.drop_index("idx_okta_user_group_member_user_group", "okta_user_group_member")
    op.drop_index("idx_okta_user_group_member_group_ended", "okta_user_group_member")
    op.drop_index("idx_access_request_status_resolved", "access_request")
