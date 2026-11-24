from sqlalchemy import delete, select, text, update

from api.database import DbSession, db
from api.operations import CreateTag


async def create_tag(body, db: DbSession):
    tag = Tag(name=body.name)
    # ruleid: router-direct-db-write
    db.add(tag)
    # ruleid: router-direct-db-write
    await db.flush()
    # ruleid: router-direct-db-write
    await db.commit()
    # ruleid: router-direct-db-write
    await db.delete(tag)
    # ruleid: router-direct-db-write
    await db.session.add_all([tag])
    # ruleid: router-direct-db-write
    await db.execute(update(Tag).where(Tag.id == tag.id).values(enabled=False))
    # ruleid: router-direct-db-write
    await db.execute(delete(Tag).where(Tag.id == tag.id))

    # ok: router-direct-db-write
    tag = await CreateTag(name=body.name).execute()
    # ok: router-direct-db-write
    tags = await db.scalars(select(Tag))
    # ok: router-direct-db-write
    await db.execute(select(Tag.id).where(Tag.name == body.name))
    # ok: router-direct-db-write
    await db.execute(text("SELECT 1"))
    # ok: router-direct-db-write
    seen = set()
    seen.add(tag.id)
    return tag
