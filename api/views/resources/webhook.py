import json

from flask import abort, current_app, g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy.orm import joinedload

from api.extensions import db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyGroupUsers

OKTA_WEBHOOK_VERIFICATION_HEADER_NAME = "X-Okta-Verification-Challenge"
OKTA_IGA_USER_AGENT = "okta_iga_connector"


class OktaWebhookResource(MethodResource):
    # To handle the one-time verification challenge from Okta
    # https://developer.okta.com/docs/concepts/event-hooks/#one-time-verification-request
    def get(self) -> ResponseReturnValue:
        if current_app.config["OKTA_WEBHOOK_ID"] is None or g.current_user_id != current_app.config["OKTA_WEBHOOK_ID"]:
            abort(403)

        verification_challenge_from_header = request.headers.get(OKTA_WEBHOOK_VERIFICATION_HEADER_NAME)
        if verification_challenge_from_header is None:
            current_app.logger.error("Okta event webhook verification request is missing header")
            abort(400)

        return {"verification": verification_challenge_from_header}

    # A Okta event webhook to handle group membership changes from the IGA Access Certification Campaigns
    def post(self) -> ResponseReturnValue:
        if current_app.config["OKTA_WEBHOOK_ID"] is None or g.current_user_id != current_app.config["OKTA_WEBHOOK_ID"]:
            abort(403)

        if not request.is_json:
            current_app.logger.error("Okta event webhook request is not JSON")
            abort(400)

        events = request.get_json().get("data", {}).get("events", [])

        for event in events:
            # Verify that the event is from the IGA user agent
            userAgent = event.get("client", {}).get("userAgent", {}).get("rawUserAgent")
            if userAgent != OKTA_IGA_USER_AGENT:
                current_app.logger.info("Okta webhook Event is not from Okta IGA user agent: %s", userAgent)
                continue

            # Verify that the event is from the IGA actor ID
            actorId = event.get("actor", {}).get("id")
            if current_app.config["OKTA_IGA_ACTOR_ID"] is None or actorId != current_app.config["OKTA_IGA_ACTOR_ID"]:
                current_app.logger.warn("Okta webhook event is not from Okta IGA user agent: %s", actorId)
                continue

            # Verify that the event is a group membership change
            eventType = event.get("eventType", "")
            if eventType not in (
                "group.user_membership.add",
                "group.user_membership.remove",
            ):
                current_app.logger.warn("Okta webhook event type is unexpected: %s", eventType)
                continue

            user = None
            group = None
            targets = event.get("target", [])
            for target in targets:
                if target.get("type") == "User":
                    user = (
                        OktaUser.query.filter(OktaUser.id == target.get("id"))
                        .filter(OktaUser.deleted_at.is_(None))
                        .first()
                    )
                elif target.get("type") == "UserGroup":
                    group = (
                        OktaGroup.query.filter(OktaGroup.id == target.get("id"))
                        .filter(OktaGroup.deleted_at.is_(None))
                        .filter(OktaGroup.is_managed.is_(True))
                        .first()
                    )

            if user is None or group is None:
                current_app.logger.warn(
                    "Could not find active user or group in target: %s",
                    json.dumps(targets),
                )
                continue

            # Add or remove direct group membership for the user
            if event.get("eventType") == "group.user_membership.add":
                ModifyGroupUsers(
                    group=group,
                    current_user_id=g.current_user_id,
                    members_to_add=[user.id],
                ).execute()
            elif event.get("eventType") == "group.user_membership.remove":
                ModifyGroupUsers(
                    group=group,
                    current_user_id=g.current_user_id,
                    members_to_remove=[user.id],
                ).execute()

            # If the user has access to this group via a role, remove them from the role
            if type(group) is not RoleGroup and event.get("eventType") == "group.user_membership.remove":
                active_role_user_group_memberships = (
                    OktaUserGroupMember.query.options(
                        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                            RoleGroupMap.active_role_group
                        )
                    )
                    .filter(OktaUserGroupMember.user_id == user.id)
                    .filter(OktaUserGroupMember.group_id == group.id)
                    .filter(
                        db.or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > db.func.now(),
                        )
                    )
                    .filter(OktaUserGroupMember.is_owner.is_(False))
                    .filter(OktaUserGroupMember.role_group_map_id.is_not(None))
                    .all()
                )

                for active_role_user_group_membership in active_role_user_group_memberships:
                    if active_role_user_group_membership.active_role_group_mapping.active_role_group.is_managed:
                        ModifyGroupUsers(
                            group=active_role_user_group_membership.active_role_group_mapping.role_group_id,
                            current_user_id=g.current_user_id,
                            members_to_remove=[user.id],
                        ).execute()

        return {"accepted": True}
