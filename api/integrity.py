import logging

from sqlalchemy import func, or_, select, update
from api.extensions import db
from api.models import OktaGroup, OktaUserGroupMember, RoleGroupMap
from api.operations import UnmanageGroup
from api.services import okta

logger = logging.getLogger(__name__)


def verify_and_fix_unmanaged_groups(dry_run: bool = False) -> None:
    active_unmanaged_groups = db.session.scalars(
        select(OktaGroup).where(OktaGroup.is_managed.is_(False)).where(OktaGroup.deleted_at.is_(None))
    ).all()
    for group in active_unmanaged_groups:
        UnmanageGroup(group=group.id).execute(dry_run=dry_run)


def verify_and_fix_role_memberships(dry_run: bool = False) -> None:
    active_role_group_maps = db.session.scalars(
        select(RoleGroupMap).where(
            or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > func.now(),
            )
        )
    ).all()
    for active_role_group_map in active_role_group_maps:
        active_role_group_members_query = (
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == active_role_group_map.role_group_id)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.is_owner.is_(False))
        )
        active_role_group_members_ids = [m.user_id for m in db.session.scalars(active_role_group_members_query).all()]

        active_group_users_for_role = db.session.scalars(
            select(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.role_group_map_id == active_role_group_map.id)
        ).all()
        active_group_users_for_role_ids = [m.user_id for m in active_group_users_for_role]

        # Fix missing group memberships/ownerships for the role by adding the user to the group
        missing_group_users_for_role = set(active_role_group_members_ids) - set(active_group_users_for_role_ids)
        if len(missing_group_users_for_role) > 0:
            logger.info(
                f"Role {active_role_group_map.role_group_id} is missing group "
                f"{'ownerships' if active_role_group_map.is_owner else 'memberships'} in group "
                f"{active_role_group_map.group_id} for users {missing_group_users_for_role}"
            )
            if not dry_run:
                for member in list(missing_group_users_for_role):
                    role_group_membership = db.session.scalars(
                        active_role_group_members_query.where(OktaUserGroupMember.user_id == member)
                    ).first()
                    # `member` came out of `active_role_group_members_ids` which we
                    # just built from this same query, so the filter must return a
                    # row. Assert to narrow `Optional` for type checking.
                    assert role_group_membership is not None
                    if not active_role_group_map.is_owner:
                        # Add user to okta group members
                        okta.add_user_to_group(
                            active_role_group_map.group_id,
                            member,
                        )
                    else:
                        # Add user to okta group owners
                        # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                        okta.add_owner_to_group(
                            active_role_group_map.group_id,
                            member,
                        )
                    # If the both the role associated group and role group membership has an end date,
                    # use the earliest of the two for setting the end date for associated group members and owners
                    if active_role_group_map.ended_at is None:
                        associated_users_ended_at = role_group_membership.ended_at
                    elif role_group_membership.ended_at is None:
                        associated_users_ended_at = active_role_group_map.ended_at
                    else:
                        associated_users_ended_at = min(active_role_group_map.ended_at, role_group_membership.ended_at)
                    db.session.add(
                        OktaUserGroupMember(
                            user_id=member,
                            group_id=active_role_group_map.group_id,
                            is_owner=active_role_group_map.is_owner,
                            role_group_map_id=active_role_group_map.id,
                            ended_at=associated_users_ended_at,
                        )
                    )
                db.session.commit()

        # Fix extra group memberships/ownerships for the role by ending the membership and potentially
        # removing the user from the group
        extra_group_users_for_role = set(active_group_users_for_role_ids) - set(active_role_group_members_ids)
        if len(extra_group_users_for_role) > 0:
            logger.info(
                f"Role {active_role_group_map.role_group_id} has extra "
                f"{'ownerships' if active_role_group_map.is_owner else 'memberships'} in group "
                f"{active_role_group_map.group_id} for users {extra_group_users_for_role}"
            )
            if not dry_run:
                # End all extra OktaUserGroupMembers the users not members of the role group
                db.session.execute(
                    update(OktaUserGroupMember)
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .where(OktaUserGroupMember.role_group_map_id == active_role_group_map.id)
                    .where(OktaUserGroupMember.user_id.in_(extra_group_users_for_role))
                    .values({OktaUserGroupMember.ended_at: func.now()})
                    .execution_options(synchronize_session="fetch")
                )
                db.session.commit()

                # Check if there are other OktaUserGroupMembers for this user/group
                # combination before removing membership, there can be multiple role groups
                # which allow group access for this user
                removed_users_with_other_access = db.session.scalars(
                    select(OktaUserGroupMember)
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .where(OktaUserGroupMember.is_owner == active_role_group_map.is_owner)
                    .where(OktaUserGroupMember.group_id == active_role_group_map.group_id)
                    .where(OktaUserGroupMember.user_id.in_(extra_group_users_for_role))
                ).all()
                removed_users_with_other_access_ids = [m.user_id for m in removed_users_with_other_access]
                okta_users_to_remove_ids = set(extra_group_users_for_role) - set(removed_users_with_other_access_ids)
                for user_id in okta_users_to_remove_ids:
                    if not active_role_group_map.is_owner:
                        # Remove user from okta group membership
                        okta.remove_user_from_group(active_role_group_map.group_id, user_id)
                    else:
                        # Remove user from okta group owners
                        # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                        okta.remove_owner_from_group(active_role_group_map.group_id, user_id)
