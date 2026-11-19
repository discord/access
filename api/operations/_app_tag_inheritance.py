from sqlalchemy import func, or_, select

from api.extensions import db
from api.models import AppTagMap, OktaGroupTagMap
from api.operations.modify_groups_time_limit import ModifyGroupsTimeLimit


async def inherit_app_tags(group_id: str, app_id: str) -> None:
    """Make an app group's app-inherited tags match its app's active tags.

    Ends the group's app-inherited tag maps that the app no longer carries, adds a map for
    each app tag the group is missing, and applies the added tags' time limits to access
    already granted on the group. Group-level tag maps are left alone. Commits.

    Args:
        group_id: The app group to update.
        app_id: The app the group belongs to.
    """
    app_tag_maps = (
        await db.session.scalars(
            select(AppTagMap)
            .where(AppTagMap.app_id == app_id)
            .where(or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))
        )
    ).all()
    inherited_tag_maps = (
        await db.session.scalars(
            select(OktaGroupTagMap)
            .where(OktaGroupTagMap.group_id == group_id)
            .where(OktaGroupTagMap.app_tag_map_id.is_not(None))
            .where(or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))
        )
    ).all()

    app_tag_map_ids = {m.id for m in app_tag_maps}
    for tag_map in inherited_tag_maps:
        if tag_map.app_tag_map_id not in app_tag_map_ids:
            tag_map.ended_at = func.now()

    already_inherited = {m.app_tag_map_id for m in inherited_tag_maps}
    added = [m for m in app_tag_maps if m.id not in already_inherited]
    for app_tag_map in added:
        db.session.add(OktaGroupTagMap(tag_id=app_tag_map.tag_id, group_id=group_id, app_tag_map_id=app_tag_map.id))
    await db.session.commit()

    if added:
        await ModifyGroupsTimeLimit(groups=[group_id], tags=[m.tag_id for m in added]).execute()
