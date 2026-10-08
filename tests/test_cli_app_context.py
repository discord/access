"""The app-context manager the CLI commands run inside."""

from __future__ import annotations

from sqlalchemy import func, select

from api.cli import app_context
from api.extensions import Db, _session_scope
from api.models import OktaUser, Tag
from tests.factories import TagFactory


async def test_app_context_commits_work_on_exit(db: Db) -> None:
    tag = TagFactory.build()
    tag_id = tag.id
    async with app_context(scope="probe-commit"):
        db.session.add(tag)

    assert await db.session.get(Tag, tag_id) is not None


async def test_app_context_leaves_a_borrowed_engine_usable(db: Db) -> None:
    """The context manager disposes only an engine it created itself. Disposing
    a borrowed one would close connections its owner is still using."""
    engine_before = db.engine
    async with app_context(scope="probe-engine"):
        pass

    assert db.engine is engine_before
    assert await db.session.scalar(select(func.count(OktaUser.id))) is not None


async def test_app_context_restores_the_previous_session_scope(db: Db) -> None:
    scope_before = _session_scope.get()
    async with app_context(scope="probe-scope"):
        assert _session_scope.get() == "probe-scope"

    assert _session_scope.get() == scope_before


async def test_app_context_leaves_the_scope_alone_when_not_given_one(db: Db) -> None:
    scope_before = _session_scope.get()
    async with app_context():
        assert _session_scope.get() == scope_before
