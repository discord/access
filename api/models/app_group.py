from typing import List

from sqlalchemy import func, or_, select

from api.extensions import db
from api.models.core_models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember


async def get_app_managers(app_id: str) -> List[OktaUser]:
    """Returns the users that can manage members of the app"""
    owner_app_groups_stmt = (
        select(AppGroup)
        .where(OktaGroup.deleted_at.is_(None))
        .where(AppGroup.app_id == app_id)
        .where(AppGroup.is_owner.is_(True))
    )

    if ((await db.session.scalar(select(func.count()).select_from(owner_app_groups_stmt.subquery()))) or 0) > 0:
        owner_app_group_ids = [ag.id for ag in await db.session.scalars(owner_app_groups_stmt)]
        result = await db.session.scalars(
            select(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .where(OktaUserGroupMember.group_id.in_(owner_app_group_ids))
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        )
        return list(result.all())

    return []


async def get_access_owners() -> List[OktaUser]:
    """Returns the access super admins that are members of the owners group"""

    access_app = (
        await db.session.scalars(
            select(App).where(App.deleted_at.is_(None)).where(App.name == App.ACCESS_APP_RESERVED_NAME)
        )
    ).first()

    if access_app is None:
        return []

    owner_app_groups_stmt = (
        select(AppGroup)
        .where(OktaGroup.deleted_at.is_(None))
        .where(AppGroup.app_id == access_app.id)
        .where(AppGroup.is_owner.is_(True))
    )

    if ((await db.session.scalar(select(func.count()).select_from(owner_app_groups_stmt.subquery()))) or 0) > 0:
        owner_app_group_ids = [ag.id for ag in await db.session.scalars(owner_app_groups_stmt)]
        result = await db.session.scalars(
            select(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .where(OktaUserGroupMember.group_id.in_(owner_app_group_ids))
            .where(OktaUserGroupMember.is_owner.is_(False))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        )
        return list(result.all())

    return []


def app_owners_group_description(app_name: str, additional_description: str | None = None) -> str:
    """Compose an app owner group's description.

    The base line is fixed; free text, when given, follows it after a blank line so the
    two render as separate paragraphs (group descriptions are rendered as markdown).

    This format string is mirrored in the frontend by `appOwnerGroupDescriptionPrefix`
    (`src/pages/groups/appOwnerGroupDescription.ts`), which needs the base line to seed
    its edit field. Change one and you must change the other; both are pinned by tests.

    Args:
        app_name: The owning app's name.
        additional_description: Free text to append. Empty or whitespace-only is treated
            as absent.

    Returns:
        The base line alone, or the base line, a blank line, and the free text.
    """
    base = f"Owners of the {app_name} application"
    additional = (additional_description or "").strip()
    if not additional:
        return base
    return f"{base}\n\n{additional}"


def app_owners_group_description_remainder(description: str, app_name: str) -> str:
    """Extract the free-text remainder from an app owner group's description.

    The reseat rule, shared with the edit form: a description that starts with the base
    line for `app_name` splits into base + remainder; one that does not becomes the
    remainder in its entirety. The second branch is what makes a description written
    before this convention (or written directly to the database) recoverable rather than
    discarded -- the caller composes it back onto a correct base line.

    Args:
        description: The stored description. May be empty.
        app_name: The app name whose base line is expected.

    Returns:
        The remainder, or an empty string when there is none.
    """
    normalized = (description or "").replace("\r\n", "\n")
    base = app_owners_group_description(app_name)
    if normalized.strip() == base:
        return ""
    if normalized.startswith(base):
        return normalized[len(base) :].strip()
    return normalized.strip()
