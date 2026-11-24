from sqlalchemy import func, or_, select

from api.extensions import Db
from api.integrity import fix_app_group_tags
from api.models import AppGroup, OktaGroupTagMap
from tests.factories import AppFactory, AppGroupFactory, AppTagMapFactory, OktaGroupTagMapFactory, TagFactory


async def _drifted_app_group(db: Db) -> tuple[str, dict[str, int | None]]:
    """An app group still carrying another app's tag and missing its own app's.

    Returns the group id and the active tag maps it should end up with, as tag id to
    `app_tag_map_id`.
    """
    app = AppFactory.build()
    other_app = AppFactory.build()
    app_tag = TagFactory.build()
    other_app_tag = TagFactory.build()
    group_tag = TagFactory.build()
    group = AppGroupFactory.build(
        app_id=app.id, name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Thing"
    )
    db.session.add_all([app, other_app, app_tag, other_app_tag, group_tag, group])
    await db.session.commit()
    app_tag_map = AppTagMapFactory.build(app_id=app.id, tag_id=app_tag.id)
    other_app_tag_map = AppTagMapFactory.build(app_id=other_app.id, tag_id=other_app_tag.id)
    db.session.add_all([app_tag_map, other_app_tag_map])
    await db.session.commit()
    db.session.add_all(
        [
            OktaGroupTagMapFactory.build(
                group_id=group.id, tag_id=other_app_tag.id, app_tag_map_id=other_app_tag_map.id
            ),
            OktaGroupTagMapFactory.build(group_id=group.id, tag_id=group_tag.id),
        ]
    )
    await db.session.commit()
    return group.id, {app_tag.id: app_tag_map.id, group_tag.id: None}


async def _active_tag_maps(db: Db, group_id: str) -> dict[str, int | None]:
    db.session.expire_all()
    tag_maps = (
        await db.session.scalars(
            select(OktaGroupTagMap)
            .where(OktaGroupTagMap.group_id == group_id)
            .where(or_(OktaGroupTagMap.ended_at.is_(None), OktaGroupTagMap.ended_at > func.now()))
        )
    ).all()
    return {m.tag_id: m.app_tag_map_id for m in tag_maps}


async def test_sweep_fixes_drifted_app_group(db: Db) -> None:
    group_id, expected = await _drifted_app_group(db)
    assert await fix_app_group_tags(dry_run=False) == 1
    assert await _active_tag_maps(db, group_id) == expected


async def test_sweep_is_idempotent(db: Db) -> None:
    group_id, expected = await _drifted_app_group(db)
    await fix_app_group_tags(dry_run=False)
    assert await fix_app_group_tags(dry_run=False) == 0
    assert await _active_tag_maps(db, group_id) == expected


async def test_dry_run_writes_nothing(db: Db) -> None:
    group_id, _ = await _drifted_app_group(db)
    before = await _active_tag_maps(db, group_id)
    assert await fix_app_group_tags(dry_run=True) == 1
    assert await _active_tag_maps(db, group_id) == before
