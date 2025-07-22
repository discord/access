import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List

from flask import current_app
from sqlalchemy.orm import (
    aliased,
    joinedload,
    selectinload,
    with_polymorphic,
)

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import get_group_managers
from api.operations import (
    DeleteGroup,
    DeleteUser,
    ModifyGroupUsers,
    RejectAccessRequest,
    UnmanageGroup,
)
from api.plugins import get_notification_hook
from api.services import okta
from api.services.okta_service import is_managed_group

logger = logging.getLogger(__name__)


def sync_users() -> None:
    logger.info("User sync starting")

    # Get all users from okta
    users = okta.list_users()
    user_type_to_user_attrs_to_titles = {}

    # Hydrate all users into sql alchemy context at once
    # to avoid a roundtrip for each user
    _ = OktaUser.query.all()

    for user in users:
        logger.info(f"Syncing user {user.id}")

        if user.type.id not in user_type_to_user_attrs_to_titles:
            user_type_to_user_attrs_to_titles[user.type.id] = okta.get_user_schema(user.type.id).user_attrs_to_titles()

        user_attrs_to_titles = user_type_to_user_attrs_to_titles[user.type.id]

        db_user = db.session.get(OktaUser, user.id)

        if db_user is None:
            logger.info(f"Creating user in DB {user.id}")
            db.session.add(user.update_okta_user(OktaUser(), user_attrs_to_titles))

        else:
            # User was found. Let's update
            user.update_okta_user(db_user, user_attrs_to_titles)

    db.session.commit()

    # Delete users and end all group memberships in the DB for users that are suspended/deactivated in Okta
    deleted_user_ids = [u.id for u in filter(lambda u: u.get_deleted_at() is not None, users)]

    users_to_delete = (
        OktaUser.query.filter(OktaUser.id.in_(deleted_user_ids)).filter(OktaUser.deleted_at.is_(None)).all()
    )

    for user in users_to_delete:
        logger.info(f"Deleting user in DB {user.id} that was suspended/deactivated in Okta")
        DeleteUser(user=user.id).execute()

    # Delete users and end all group memberships in the DB for users that are deleted in Okta
    active_user_ids = [u.id for u in filter(lambda u: u.get_deleted_at() is None, users)]

    more_users_to_delete = (
        OktaUser.query.filter(OktaUser.id.not_in(active_user_ids)).filter(OktaUser.deleted_at.is_(None)).all()
    )

    for user in more_users_to_delete:
        logger.info(f"Deleting user in DB {user.id} that was deleted in Okta")
        DeleteUser(user=user.id, sync_to_okta=False).execute()

    # End all active group memberships in the DB for users that were previously deleted
    db_deleted_users_with_access = (
        OktaUserGroupMember.query.join(OktaUserGroupMember.user)
        .filter(OktaUser.deleted_at.isnot(None))
        .filter(db.or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > db.func.now()))
        .all()
    )
    for user_id in set([u.user_id for u in db_deleted_users_with_access]):
        logger.info(f"Ending active group ownerships/memberships for deleted user in DB {user_id}")
        DeleteUser(user=user_id).execute()

    # Sync manager foreign keys, as Okta only gives us employee numbers
    users_by_employee_number = {
        user.profile.employee_number: user for user in filter(lambda u: u.profile.employee_number is not None, users)
    }
    for user in users:
        db_user = db.session.get(OktaUser, user.id)
        manager = users_by_employee_number.get(user.profile.manager_id, None)
        db_user.manager_id = getattr(manager, "id", None)

    db.session.commit()

    logger.info("User sync finished.")


def sync_groups(act_as_authority: bool) -> None:
    logger.info("Group sync starting")

    groups_in_okta = okta.list_groups()
    db_group_ids = {row.id for row in db.session.query(OktaGroup.id).filter(OktaGroup.deleted_at.is_(None)).all()}

    group_ids_with_group_rules = okta.list_groups_with_active_rules()

    for group in groups_in_okta:
        logger.info(f"Syncing group {group.id}")

        # Remove found groups from deleted group ids
        db_group_ids.discard(group.id)

        db_group = db.session.get(OktaGroup, group.id)

        # Handle the case where the group is in okta but not in the DB.
        if db_group is None:
            if act_as_authority:
                logger.info(f"A new group {group.id} was added directly through okta. Deleting.")
                okta.delete_group(group.id)
            else:
                logger.info(f"A new group {group.id} was added directly through okta. Adding to DB.")
                db.session.add(group.update_okta_group(OktaGroup(), group_ids_with_group_rules))

        # Handle the case where we've marked the group as deleted, but it still exists in okta
        elif db_group.deleted_at:
            if act_as_authority:
                logger.info(f"Group {group.id} is marked as deleted, but still exists in okta. Deleting.")
                DeleteGroup(group=group.id).execute()
            else:
                logger.info(f"Group {group.id} is marked as deleted, but still exists in okta. Resurrecting.")
                db_group.deleted_at = None

        # Handle the cases where the group is active in both Okta and our DB.
        else:
            if not act_as_authority:
                was_previously_managed = db_group.is_managed
                db_group = group.update_okta_group(db_group, group_ids_with_group_rules)

                if not db_group.is_managed and was_previously_managed:
                    UnmanageGroup(group=db_group).execute()

    # Any remaining group ids have been deleted from okta's side.
    if len(db_group_ids) > 0:
        logger.info(
            f"{len(db_group_ids)} groups exist locally but not in Okta. Deleting locally. Group Ids: {db_group_ids}"
        )

        for group_id in db_group_ids:
            DeleteGroup(group=group_id, sync_to_okta=False).execute()

    db.session.commit()
    logger.info("Group sync finished.")


def sync_group_memberships(act_as_authority: bool) -> None:
    logger.info("Membership sync started.")
    groups = okta.list_groups()

    # Hydrate all groups into sql alchemy context at once
    # to avoid a roundtrip for each group
    _ = db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup])).all()

    group_ids_with_group_rules = okta.list_groups_with_active_rules()

    for group in groups:
        is_managed = is_managed_group(group, group_ids_with_group_rules)

        act_authoritatively = act_as_authority and is_managed

        logger.info(f"Syncing group {group.id}. act_authoritatively: {act_authoritatively}")

        members = okta.list_users_for_group(group.id)

        logger.info(f"Fetched users list for group {group.id}")

        db_all_group_members = {
            row.id: row.user_id
            for row in db.session.query(
                OktaUserGroupMember.user_id,
                OktaUserGroupMember.id,
            ).filter(
                OktaUserGroupMember.group_id == group.id,
                OktaUserGroupMember.is_owner.is_(False),
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                ),
            )
        }

        for member in members:
            # User is a member in okta but not in the DB
            if member.id not in db_all_group_members.values():
                logger.info(f"User {member.id} is not in the group in our DB.")

                if act_authoritatively:
                    okta.remove_user_from_group(group.id, member.id)
                else:
                    reason = (
                        "User in Okta group but not in Access group."
                        if is_managed
                        else "User added via Okta group rule."
                    )
                    ModifyGroupUsers(
                        group=group.id,
                        members_to_add=[member.id],
                        created_reason=reason,
                    ).execute()

            # User is a member in okta and an entry exists in our DB
            else:
                db_all_group_members = {k: v for k, v in db_all_group_members.items() if v != member.id}

        logger.info("Members in Okta synced to DB.")

        # All remaining values are memberships that are marked active in our DB
        # But are not valid memberships in okta
        if db_all_group_members:
            logger.info(
                f"Users were marked as members in the DB but not in okta. Updating. User IDs: {db_all_group_members}"
            )

            distinct_member_ids = set(db_all_group_members.values())
            if act_authoritatively:
                # Create in okta
                for member_id in distinct_member_ids:
                    okta.add_user_to_group(group.id, member_id)
            else:
                # Remove the direct group memberships to this group in our DB
                # This will not affect group memberships that are via other group roles
                ModifyGroupUsers(group=group.id, members_to_remove=list(distinct_member_ids)).execute()

        logger.info("Members in DB synced to Okta.")

        db.session.commit()

    logger.info("Membership sync finished.")


def sync_group_ownerships(act_as_authority: bool) -> None:
    logger.info("Ownership sync started.")
    groups = okta.list_groups()

    group_ids_with_group_rules = okta.list_groups_with_active_rules()

    for group in groups:
        is_managed = is_managed_group(group, group_ids_with_group_rules)

        act_authoritatively = act_as_authority and is_managed

        logger.info(f"Syncing group {group.id}. act_authoritatively: {act_authoritatively}")

        owners = okta.list_owners_for_group(group.id)

        db_all_group_owners = {
            row.id: row.user_id
            for row in db.session.query(
                OktaUserGroupMember.user_id,
                OktaUserGroupMember.id,
            ).filter(
                OktaUserGroupMember.group_id == group.id,
                OktaUserGroupMember.is_owner.is_(True),
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                ),
            )
        }

        # If the group ownership is managed by Access and there are no owners for it
        # check to see if it's an AppGroup and if so, add the app owners as owners in Okta
        if act_authoritatively and len(db_all_group_owners) == 0:
            app_group = (
                AppGroup.query.options(
                    joinedload(AppGroup.app).options(selectinload(App.active_owner_app_groups)),
                )
                .filter(AppGroup.deleted_at.is_(None))
                .filter(AppGroup.id == group.id)
                .first()
            )

            if app_group is not None and not app_group.is_owner:
                app_owner_group_ids = [g.id for g in app_group.app.active_owner_app_groups]
                db_all_group_owners = {
                    row.id: row.user_id
                    for row in db.session.query(
                        OktaUserGroupMember.user_id,
                        OktaUserGroupMember.id,
                    ).filter(
                        OktaUserGroupMember.group_id.in_(app_owner_group_ids),
                        OktaUserGroupMember.is_owner.is_(True),
                        db.or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > db.func.now(),
                        ),
                    )
                }

        for owner in owners:
            # User is a owner in okta but not in the DB
            if owner.id not in db_all_group_owners.values():
                logger.info(f"User {owner.id} is not in the group in our DB.")

                if act_authoritatively:
                    okta.remove_owner_from_group(group.id, owner.id)
                else:
                    reason = (
                        "User in Okta group but not in Access group."
                        if is_managed
                        else "User was added via Okta group rule."
                    )
                    ModifyGroupUsers(
                        group=group.id,
                        owners_to_add=[owner.id],
                        created_reason=reason,
                    ).execute()

            # User is a owner in okta and an entry exists in our DB
            else:
                db_all_group_owners = {k: v for k, v in db_all_group_owners.items() if v != owner.id}

        # All remaining values are ownerships that are marked active in our DB
        # But are not valid ownerships in okta
        if db_all_group_owners:
            logger.info(
                f"Users were marked as owners in the DB but not in okta. Updating. User IDs: {db_all_group_owners}"
            )

            distinct_owner_ids = set(db_all_group_owners.values())
            if act_authoritatively:
                # Create in okta
                for owner_id in distinct_owner_ids:
                    okta.add_owner_to_group(group.id, owner_id)
            else:
                # Remove the direct group ownerships to this group in our DB
                # This will not affect group ownerships that are via other group roles
                ModifyGroupUsers(group=group.id, owners_to_remove=list(distinct_owner_ids)).execute()

        db.session.commit()

    logger.info("Ownership sync finished.")


def expire_access_requests() -> None:
    logger.info("Access request expiration started.")
    MAX_ACCESS_REQUEST_AGE_SECONDS = current_app.config["MAX_ACCESS_REQUEST_AGE_SECONDS"]

    older_than_max = (
        AccessRequest.query.filter(AccessRequest.status == AccessRequestStatus.PENDING)
        .filter(AccessRequest.resolved_at.is_(None))
        .filter(
            AccessRequest.created_at < datetime.now(timezone.utc) - timedelta(seconds=MAX_ACCESS_REQUEST_AGE_SECONDS)
        )
        .all()
    )
    for access_request in older_than_max:
        RejectAccessRequest(
            access_request=access_request,
            rejection_reason="Closed because the request expired",
        ).execute()

    older_than_request = (
        AccessRequest.query.filter(AccessRequest.status == AccessRequestStatus.PENDING)
        .filter(AccessRequest.resolved_at.is_(None))
        .filter(AccessRequest.request_ending_at < db.func.now())
        .all()
    )
    for access_request in older_than_request:
        RejectAccessRequest(
            access_request=access_request,
            rejection_reason="Closed because the request expired",
        ).execute()

    logger.info("Access request expiration finished.")


def expiring_access_notifications_user() -> None:
    logger.info("Expiring access notifications for users started.")
    notification_hook = get_notification_hook()

    weekend_notif_tomorrow = False
    day = date.today() + timedelta(days=1)
    next_day = day + timedelta(days=1)
    if datetime.now().weekday() == 4:
        next_day = day + timedelta(days=3)
        weekend_notif_tomorrow = True

    db_memberships_expiring_tomorrow = (
        OktaUserGroupMember.query.options(
            joinedload(OktaUserGroupMember.active_user), joinedload(OktaUserGroupMember.active_group)
        )
        .join(OktaUserGroupMember.active_user)
        .join(OktaUserGroupMember.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(OktaUserGroupMember.ended_at >= day, OktaUserGroupMember.ended_at < next_day))
        .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        .filter(OktaUserGroupMember.should_expire.is_(False))
        .all()
    )

    # remove OktaUserGroupMembers from the list where there's a role that grants the same access
    db_memberships_roles = (
        OktaUserGroupMember.query.options(
            joinedload(OktaUserGroupMember.active_user), joinedload(OktaUserGroupMember.active_group)
        )
        .join(OktaUserGroupMember.active_user)
        .join(OktaUserGroupMember.active_group)
        .filter(OktaUserGroupMember.role_group_map_id.is_not(None))
        .all()
    )

    user_id_group_id_roles = set((member.user_id, member.group_id) for member in db_memberships_roles)

    db_memberships_expiring_tomorrow = [
        member
        for member in db_memberships_expiring_tomorrow
        if (member.user_id, member.group_id) not in user_id_group_id_roles
    ]

    grouped_tomorrow: dict[OktaUser, list[OktaUserGroupMember]] = {}
    for membership in db_memberships_expiring_tomorrow:
        grouped_tomorrow.setdefault(membership.active_user, []).append(membership)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    grouped_tomorrow_old: dict[OktaUser, list[OktaGroup]] = {}
    for membership in db_memberships_expiring_tomorrow:
        grouped_tomorrow_old.setdefault(membership.active_user, []).append(membership.active_group)

    weekend_notif_week = False
    day = date.today() + timedelta(weeks=1)
    next_day = day + timedelta(days=1)
    if datetime.now().weekday() == 4:
        next_day = day + timedelta(days=3)
        weekend_notif_week = True

    db_memberships_expiring_next_week = (
        OktaUserGroupMember.query.options(
            joinedload(OktaUserGroupMember.active_user), joinedload(OktaUserGroupMember.active_group)
        )
        .join(OktaUserGroupMember.active_user)
        .join(OktaUserGroupMember.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(OktaUserGroupMember.ended_at >= day, OktaUserGroupMember.ended_at < next_day))
        .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        .filter(OktaUserGroupMember.should_expire.is_(False))
        .all()
    )

    # remove OktaUserGroupMembers from the list where there's a role that grants the same access
    db_memberships_expiring_next_week = [
        member
        for member in db_memberships_expiring_next_week
        if (member.user_id, member.group_id) not in user_id_group_id_roles
    ]

    grouped_next_week: dict[OktaUser, list[OktaUserGroupMember]] = {}
    for membership in db_memberships_expiring_next_week:
        grouped_next_week.setdefault(membership.active_user, []).append(membership)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    grouped_next_week_old: dict[OktaUser, list[OktaGroup]] = {}
    for membership in db_memberships_expiring_next_week:
        grouped_next_week_old.setdefault(membership.active_user, []).append(membership.active_group)

    for user in grouped_tomorrow:
        # If the user has access expiring both tomorrow and in a week, only send one message
        if user in grouped_next_week:
            notification_hook.access_expiring_user(
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=grouped_tomorrow_old[user] + grouped_next_week_old[user],
                user=user,
                expiration_datetime=None,
                okta_user_group_members=grouped_tomorrow[user] + grouped_next_week[user],
            )
        else:
            notification_hook.access_expiring_user(
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=grouped_tomorrow_old[user],
                user=user,
                expiration_datetime=None if weekend_notif_tomorrow else datetime.now() + timedelta(days=1),
                okta_user_group_members=grouped_tomorrow[user],
            )

    for user in grouped_next_week:
        if user not in grouped_tomorrow:
            notification_hook.access_expiring_user(
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=grouped_next_week_old[user],
                user=user,
                expiration_datetime=None if weekend_notif_week else datetime.now() + timedelta(weeks=1),
                okta_user_group_members=grouped_next_week[user],
            )

    logger.info("Expiring access notifications for users finished.")


# TODO eventually clean this up, leaving for now for backwards compatibility
class GroupsAndUsers:
    def __init__(self) -> None:
        self.groups: List[OktaGroup] = []
        self.roles: List[RoleGroup] = []
        self.users: List[OktaUser] = []


def expiring_access_notifications_owner() -> None:
    logger.info("Expiring access notifications for owners started.")
    notification_hook = get_notification_hook()

    day = date.today()
    next_week = day + timedelta(weeks=1)

    # Expiring groups
    db_memberships_expiring_this_week = (
        OktaUserGroupMember.query.options(
            joinedload(OktaUserGroupMember.active_user), joinedload(OktaUserGroupMember.active_group)
        )
        .join(OktaUserGroupMember.active_user)
        .join(OktaUserGroupMember.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(OktaUserGroupMember.ended_at >= day, OktaUserGroupMember.ended_at < next_week))
        .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        .filter(OktaUserGroupMember.should_expire.is_(False))
        .all()
    )

    access_owners = get_access_owners()

    # Map of group owners -> list[OktaUserGroupMember]
    owner_expiring_groups_this: defaultdict[OktaUser, list[OktaUserGroupMember]] = defaultdict(list)
    for okta_user_group_member in db_memberships_expiring_this_week:
        owners = get_group_managers(okta_user_group_member.group_id)

        if len(owners) == 0:
            owners += (
                get_app_managers(okta_user_group_member.group.app_id)
                if type(okta_user_group_member.group) is AppGroup
                else []
            )

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            if owner.id != okta_user_group_member.user_id:
                owner_expiring_groups_this[owner].append(okta_user_group_member)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    # Map of group -> list of users with access expiring next week
    users_per_group = defaultdict(list)

    for m in db_memberships_expiring_this_week:
        users_per_group[m.active_group].append(m.active_user)

    # Map of group owners -> (number of groups with expiring memberships, number of users with expiring memberships)
    owner_expiring_groups_this_old: defaultdict[OktaUser, GroupsAndUsers] = defaultdict(GroupsAndUsers)
    for group in users_per_group:
        owners = get_group_managers(group.id)

        if len(owners) == 0:
            owners += get_app_managers(group.app_id) if type(group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            non_owner_users = [user for user in users_per_group[group] if user.id != owner.id]
            if len(non_owner_users) > 0:
                owner_expiring_groups_this_old[owner].users += non_owner_users
                owner_expiring_groups_this_old[owner].groups.append(group)

    one_week = date.today() + timedelta(weeks=1)
    two_weeks = one_week + timedelta(weeks=1)

    db_memberships_expiring_next_week = (
        OktaUserGroupMember.query.options(
            joinedload(OktaUserGroupMember.active_user), joinedload(OktaUserGroupMember.active_group)
        )
        .join(OktaUserGroupMember.active_user)
        .join(OktaUserGroupMember.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(OktaUserGroupMember.ended_at >= one_week, OktaUserGroupMember.ended_at < two_weeks))
        .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        .filter(OktaUserGroupMember.should_expire.is_(False))
        .all()
    )

    # Map of group owners -> list[OktaUserGroupMember]
    owner_expiring_groups_next: defaultdict[OktaUser, list[OktaUserGroupMember]] = defaultdict(list)
    for okta_user_group_member in db_memberships_expiring_next_week:
        owners = get_group_managers(okta_user_group_member.group_id)

        if len(owners) == 0:
            owners += (
                get_app_managers(okta_user_group_member.group.app_id)
                if type(okta_user_group_member.group) is AppGroup
                else []
            )

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            if owner.id != okta_user_group_member.user_id:
                owner_expiring_groups_next[owner].append(okta_user_group_member)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    # Map of group -> list of users with access expiring next week
    users_per_group = defaultdict(list)

    for m in db_memberships_expiring_next_week:
        users_per_group[m.active_group].append(m.active_user)

    # Map of group owners -> (number of groups with expiring memberships, number of users with expiring memberships)
    owner_expiring_groups_next_old: defaultdict[OktaUser, GroupsAndUsers] = defaultdict(GroupsAndUsers)
    for group in users_per_group:
        owners = get_group_managers(group.id)

        if len(owners) == 0:
            owners += get_app_managers(group.app_id) if type(group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            non_owner_users = [user for user in users_per_group[group] if user.id != owner.id]
            if len(non_owner_users) > 0:
                owner_expiring_groups_next_old[owner].users += non_owner_users
                owner_expiring_groups_next_old[owner].groups.append(group)

    for owner in owner_expiring_groups_this:
        # If the owner has members with access expiring both this week and next week, only send one message
        if owner in owner_expiring_groups_next:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_groups_this_old[owner].groups + owner_expiring_groups_next_old[owner].groups,
                roles=None,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                users=owner_expiring_groups_this_old[owner].users + owner_expiring_groups_next_old[owner].users,
                expiration_datetime=None,
                group_user_associations=owner_expiring_groups_this[owner] + owner_expiring_groups_next[owner],
                role_group_associations=None,
            )
        else:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_groups_this_old[owner].groups,
                roles=None,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                users=owner_expiring_groups_this_old[owner].users,
                expiration_datetime=datetime.now(),
                group_user_associations=owner_expiring_groups_this[owner],
                role_group_associations=None,
            )

    for owner in owner_expiring_groups_next:
        if owner not in owner_expiring_groups_this:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_groups_next_old[owner].groups,
                roles=None,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                users=owner_expiring_groups_next_old[owner].users,
                expiration_datetime=datetime.now() + timedelta(weeks=1),
                group_user_associations=owner_expiring_groups_next[owner],
                role_group_associations=None,
            )

    all_group_types = with_polymorphic(OktaGroup, [AppGroup, RoleGroup], flat=True)
    role_group_alias = aliased(RoleGroup)

    # Expiring roles
    day = date.today()
    next_week = day + timedelta(weeks=1)

    db_roles_expiring_this_week = (
        RoleGroupMap.query.options(
            joinedload(RoleGroupMap.active_role_group), joinedload(RoleGroupMap.active_group.of_type(all_group_types))
        )
        .join(RoleGroupMap.active_role_group.of_type(role_group_alias))
        .join(RoleGroupMap.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(RoleGroupMap.ended_at >= day, RoleGroupMap.ended_at < next_week))
        .filter(RoleGroupMap.should_expire.is_(False))
        .all()
    )

    # Map of group owners -> list[RoleGroupMap]
    owner_expiring_roles_this: defaultdict[OktaUser, list[RoleGroupMap]] = defaultdict(list)
    for role_group_map in db_roles_expiring_this_week:
        owners = get_group_managers(role_group_map.group_id)

        if len(owners) == 0:
            owners += get_app_managers(role_group_map.group.app_id) if type(role_group_map.group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            owner_expiring_roles_this[owner].append(role_group_map)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    # Map of group -> list of roles with access expiring next week
    roles_per_group = defaultdict(list)

    for r in db_roles_expiring_this_week:
        roles_per_group[r.active_group].append(r.active_role_group)

    # Map of group owners -> (number of groups with expiring memberships, number of users with expiring memberships)
    owner_expiring_roles_this_old: defaultdict[OktaUser, GroupsAndUsers] = defaultdict(GroupsAndUsers)
    for group in roles_per_group:
        owners = get_group_managers(group.id)

        if len(owners) == 0:
            owners += get_app_managers(group.app_id) if type(group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            owner_expiring_roles_this_old[owner].roles += roles_per_group[group]
            owner_expiring_roles_this_old[owner].groups.append(group)

    one_week = date.today() + timedelta(weeks=1)
    two_weeks = one_week + timedelta(weeks=1)

    db_roles_expiring_next_week = (
        RoleGroupMap.query.options(
            joinedload(RoleGroupMap.active_role_group), joinedload(RoleGroupMap.active_group.of_type(all_group_types))
        )
        .join(RoleGroupMap.active_role_group.of_type(role_group_alias))
        .join(RoleGroupMap.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(RoleGroupMap.ended_at >= one_week, RoleGroupMap.ended_at < two_weeks))
        .filter(RoleGroupMap.should_expire.is_(False))
        .all()
    )

    # Map of group owners -> list[RoleGroupMap]
    owner_expiring_roles_next: defaultdict[OktaUser, list[RoleGroupMap]] = defaultdict(list)
    for role_group_map in db_roles_expiring_next_week:
        owners = get_group_managers(role_group_map.group_id)

        if len(owners) == 0:
            owners += get_app_managers(role_group_map.group.app_id) if type(role_group_map.group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            owner_expiring_roles_next[owner].append(role_group_map)

    # TODO eventually clean this up, leaving for now for backwards compatibility
    # Map of group -> list of roles with access expiring next week
    roles_per_group = defaultdict(list)

    for r in db_roles_expiring_next_week:
        roles_per_group[r.active_group].append(r.active_role_group)

    # Map of group owners -> (number of groups with expiring memberships, number of users with expiring memberships)
    owner_expiring_roles_next_old: defaultdict[OktaUser, GroupsAndUsers] = defaultdict(GroupsAndUsers)
    for group in roles_per_group:
        owners = get_group_managers(group.id)

        if len(owners) == 0:
            owners += get_app_managers(group.app_id) if type(group) is AppGroup else []

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            owner_expiring_roles_next_old[owner].roles += roles_per_group[group]
            owner_expiring_roles_next_old[owner].groups.append(group)

    for owner in owner_expiring_roles_this:
        # If the owner has members with access expiring both this week and next week, only send one message
        if owner in owner_expiring_roles_next:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_roles_this_old[owner].groups + owner_expiring_roles_next_old[owner].groups,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                roles=owner_expiring_roles_this_old[owner].roles + owner_expiring_roles_next_old[owner].roles,
                users=None,
                expiration_datetime=None,
                group_user_associations=None,
                role_group_associations=owner_expiring_roles_this[owner] + owner_expiring_roles_next[owner],
            )
        else:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_roles_this_old[owner].groups,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                roles=owner_expiring_roles_this_old[owner].roles,
                users=None,
                expiration_datetime=datetime.now(),
                group_user_associations=None,
                role_group_associations=owner_expiring_roles_this[owner],
            )

    for owner in owner_expiring_roles_next:
        if owner not in owner_expiring_roles_this:
            notification_hook.access_expiring_owner(
                owner=owner,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                groups=owner_expiring_roles_next_old[owner].groups,
                # TODO eventually clean this up, leaving for now for backwards compatibility
                roles=owner_expiring_roles_next_old[owner].roles,
                users=None,
                expiration_datetime=datetime.now() + timedelta(weeks=1),
                group_user_associations=None,
                role_group_associations=owner_expiring_roles_next[owner],
            )

    logger.info("Expiring access notifications for owners finished.")


def expiring_access_notifications_role_owner() -> None:
    logger.info("Expiring access notifications for role owners started.")
    notification_hook = get_notification_hook()

    access_owners = get_access_owners()

    all_group_types = with_polymorphic(OktaGroup, [AppGroup, RoleGroup], flat=True)
    role_group_alias = aliased(RoleGroup)

    weekend_notif_tomorrow = False
    day = date.today() + timedelta(days=1)
    next_day = day + timedelta(days=1)
    if datetime.now().weekday() == 4:
        next_day = day + timedelta(days=3)
        weekend_notif_tomorrow = True

    db_roles_expiring_tomorrow = (
        RoleGroupMap.query.options(
            joinedload(RoleGroupMap.active_role_group), joinedload(RoleGroupMap.active_group.of_type(all_group_types))
        )
        .join(RoleGroupMap.active_role_group.of_type(role_group_alias))
        .join(RoleGroupMap.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(RoleGroupMap.ended_at >= day, RoleGroupMap.ended_at < next_day))
        .filter(RoleGroupMap.should_expire.is_(False))
        .all()
    )

    # Map of role owners -> list[RoleGroupMap]
    role_owner_expiring_roles_tomorrow: defaultdict[OktaUser, list[RoleGroupMap]] = defaultdict(list)
    for role_group_map in db_roles_expiring_tomorrow:
        owners = get_group_managers(role_group_map.role_group.id)

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            role_owner_expiring_roles_tomorrow[owner].append(role_group_map)

    weekend_notif_week = False
    day = date.today() + timedelta(weeks=1)
    next_day = day + timedelta(days=1)
    if datetime.now().weekday() == 4:
        next_day = day + timedelta(days=3)
        weekend_notif_week = True

    db_roles_expiring_next_week = (
        RoleGroupMap.query.options(
            joinedload(RoleGroupMap.active_role_group), joinedload(RoleGroupMap.active_group.of_type(all_group_types))
        )
        .join(RoleGroupMap.active_role_group.of_type(role_group_alias))
        .join(RoleGroupMap.active_group)
        .filter(OktaGroup.is_managed.is_(True))
        .filter(db.and_(RoleGroupMap.ended_at >= day, RoleGroupMap.ended_at < next_day))
        .filter(RoleGroupMap.should_expire.is_(False))
        .all()
    )

    # Map of role owners -> list[RoleGroupMap]
    role_owner_expiring_roles_next: defaultdict[OktaUser, list[RoleGroupMap]] = defaultdict(list)
    for role_group_map in db_roles_expiring_next_week:
        owners = get_group_managers(role_group_map.role_group.id)

        if len(owners) == 0:
            owners = access_owners

        for owner in owners:
            role_owner_expiring_roles_next[owner].append(role_group_map)

    for owner in role_owner_expiring_roles_tomorrow:
        # If the role owner has roles they own with access expiring both this week and next week, only send one message
        if owner in role_owner_expiring_roles_next:
            notification_hook.access_expiring_role_owner(
                owner=owner,
                roles=role_owner_expiring_roles_tomorrow[owner] + role_owner_expiring_roles_next[owner],
                expiration_datetime=None,
            )
        else:
            notification_hook.access_expiring_role_owner(
                owner=owner,
                roles=role_owner_expiring_roles_tomorrow[owner],
                expiration_datetime=None if weekend_notif_tomorrow else datetime.now() + timedelta(days=1),
            )

    for owner in role_owner_expiring_roles_next:
        if owner not in role_owner_expiring_roles_tomorrow:
            notification_hook.access_expiring_role_owner(
                owner=owner,
                roles=role_owner_expiring_roles_next[owner],
                expiration_datetime=None if weekend_notif_week else datetime.now() + timedelta(weeks=1),
            )

    logger.info("Expiring access notifications for role owners finished.")
