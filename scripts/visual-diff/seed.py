"""Seed a fresh SQLite database with the fixed data set the visual diff renders.

Usage: python seed.py <sqlite-file> <anchor-iso-datetime>

Run with the `api` package of the checkout being rendered on PYTHONPATH, so each side of the
diff builds its schema from its own models. Both sides are seeded by this one script, so it
sticks to columns that are stable across versions.

Every id comes from fixtures.json, which the Playwright spec reads to build its URLs. Every
timestamp is an offset from the anchor rather than from the wall clock, so the two sides hold
identical rows and, with the browser clock frozen relative to the same anchor, render identical
relative dates. Nothing here talks to Okta.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from api.extensions import Base
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    AppTagMap,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
    Tag,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text())
DAY = timedelta(days=1)


def _rows(anchor: datetime) -> list[Any]:
    """Build every row to insert, in foreign-key order."""
    users, apps, groups = FIXTURES["users"], FIXTURES["apps"], FIXTURES["groups"]
    tags, requests = FIXTURES["tags"], FIXTURES["requests"]
    created = anchor - 60 * DAY

    def stamps(at: datetime = created) -> dict[str, datetime]:
        return {"created_at": at, "updated_at": at}

    def user(key: str, first: str, last: str, title: str, email: str | None = None) -> OktaUser:
        return OktaUser(
            id=users[key],
            email=email or f"{first}.{last}@example.com".lower(),
            first_name=first,
            last_name=last,
            display_name=f"{first} {last}",
            profile={"Title": title},
            **stamps(),
        )

    access_name = App.ACCESS_APP_RESERVED_NAME
    prefix, sep = AppGroup.APP_GROUP_NAME_PREFIX, AppGroup.APP_NAME_GROUP_NAME_SEPARATOR
    owners_suffix = AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX

    membership_id = iter(range(1, 1000))

    def member(user_key: str, group_key: str, **kwargs: Any) -> OktaUserGroupMember:
        kwargs.setdefault("created_actor_id", users["admin"])
        at = kwargs.pop("at", created)
        return OktaUserGroupMember(
            id=next(membership_id), user_id=users[user_key], group_id=groups[group_key], **stamps(at), **kwargs
        )

    role_map = RoleGroupMap(
        id=1,
        role_group_id=groups["role"],
        group_id=groups["wikiEditors"],
        is_owner=False,
        ended_at=anchor + 10 * DAY,
        created_actor_id=users["owner"],
        created_reason="Engineers edit the wiki",
        **stamps(anchor - 30 * DAY),
    )
    app_tag = AppTagMap(id=1, tag_id=tags["sox"], app_id=apps["wiki"], **stamps())

    return [
        user("admin", "Ada", "Admin", "Access Administrator", email=FIXTURES["adminEmail"]),
        user("owner", "Grace", "Owner", "Engineering Manager"),
        user("member", "Linus", "Member", "Software Engineer"),
        user("requester", "Margaret", "Requester", "Data Analyst"),
        App(id=apps["access"], name=access_name, description=f"The {access_name} Portal", **stamps()),
        App(id=apps["wiki"], name="Wiki", description="The team wiki", **stamps()),
        Tag(
            id=tags["sox"],
            name="SOX",
            description="Quarterly-reviewed access",
            enabled=True,
            constraints={
                Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 90 * 24 * 60 * 60,
                Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
            },
            **stamps(),
        ),
        AppGroup(
            id=groups["accessOwners"],
            name=f"{prefix}{access_name}{sep}{owners_suffix}",
            description=f"Owners of the {access_name} application",
            app_id=apps["access"],
            is_owner=True,
            **stamps(),
        ),
        AppGroup(
            id=groups["wikiOwners"],
            name=f"{prefix}Wiki{sep}{owners_suffix}",
            description="Owners of the Wiki application",
            app_id=apps["wiki"],
            is_owner=True,
            **stamps(),
        ),
        AppGroup(
            id=groups["wikiEditors"],
            name=f"{prefix}Wiki{sep}Editors",
            description="Can edit every wiki page",
            app_id=apps["wiki"],
            is_owner=False,
            **stamps(),
        ),
        OktaGroup(
            id=groups["engineering"],
            type="okta_group",
            name="Engineering",
            description="Everyone in engineering",
            **stamps(),
        ),
        RoleGroup(
            id=groups["role"],
            name=f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Software-Engineer",
            description="Software engineers",
            **stamps(),
        ),
        app_tag,
        OktaGroupTagMap(id=1, tag_id=tags["sox"], group_id=groups["wikiOwners"], app_tag_map_id=1, **stamps()),
        OktaGroupTagMap(id=2, tag_id=tags["sox"], group_id=groups["wikiEditors"], app_tag_map_id=1, **stamps()),
        role_map,
        # The admin's membership in the Access owners group is what makes them an Access admin.
        member("admin", "accessOwners", created_actor_id=None),
        member("owner", "wikiOwners"),
        member("owner", "engineering", is_owner=True),
        member("owner", "role", is_owner=True),
        member("member", "engineering", ended_at=anchor + 5 * DAY, created_reason="Onboarding project"),
        member("member", "role"),
        member("requester", "role", at=anchor - 20 * DAY),
        member("requester", "engineering", ended_at=anchor - 3 * DAY, ended_actor_id=users["owner"]),
        # Memberships the role grants through its mapping to the wiki editors group.
        member("member", "wikiEditors", role_group_map_id=1, ended_at=anchor + 10 * DAY),
        member("requester", "wikiEditors", role_group_map_id=1, ended_at=anchor + 10 * DAY),
        AccessRequest(
            id=requests["access"],
            status=AccessRequestStatus.PENDING,
            requester_user_id=users["requester"],
            requested_group_id=groups["engineering"],
            request_ownership=False,
            request_reason="Need to read the deploy dashboards",
            request_ending_at=anchor + 30 * DAY,
            **stamps(anchor - 2 * DAY),
        ),
        RoleRequest(
            id=requests["role"],
            status=AccessRequestStatus.PENDING,
            requester_user_id=users["owner"],
            requester_role_id=groups["role"],
            requested_group_id=groups["engineering"],
            request_ownership=False,
            request_reason="Every software engineer belongs in Engineering",
            request_ending_at=None,
            **stamps(anchor - 1 * DAY),
        ),
        GroupRequest(
            id=requests["group"],
            status=AccessRequestStatus.PENDING,
            requester_user_id=users["requester"],
            requested_group_name="Data-Platform",
            requested_group_description="Analysts working on the data platform",
            requested_group_type="okta_group",
            requested_group_tags=[],
            request_reason="A home for data platform access",
            **stamps(anchor - 1 * DAY),
        ),
    ]


async def main(db_file: str, anchor: datetime) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        # Insert one row at a time, in order, so joined-table subclasses and foreign keys land
        # without relying on the unit of work to sort a whole session.
        def insert(sync_conn: Any) -> None:
            with Session(bind=sync_conn) as session:
                for row in _rows(anchor):
                    session.add(row)
                    session.flush()
                session.commit()

        await conn.run_sync(insert)
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: seed.py <sqlite-file> <anchor-iso-datetime>")
    asyncio.run(main(sys.argv[1], datetime.fromisoformat(sys.argv[2]).replace(tzinfo=None)))
