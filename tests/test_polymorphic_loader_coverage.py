"""Regression battery for the Flask-era lazy-load Sentry issues.

ACCESS-FLASK-4N (`RoleGroup.active_role_associated_group_member_mappings`),
ACCESS-FLASK-44 (`RoleGroupMap.active_group`), and ACCESS-FLASK-4E
(`AppGroup.app`, `OktaGroup.active_group_tags`) were production
`InvalidRequestError: ... lazy='raise_on_sql'` failures that never reproduced
in tests. The reason: `raise_on_sql` raises identically in tests, so the only
production-only ingredient was DATA SHAPE — the failing serializations needed
polymorphic subtypes in nested positions (a role group whose associated groups
are app groups, tagged groups inside request refs, app groups inside
membership rows) that the suite never built. The five relationships involved
were demoted to `lazy="select"` as a band-aid; the async migration flips them
back to `raise_on_sql`, so this battery builds the production shape once and
walks every read surface. Any eager-load gap fails deterministically here
instead of as a paged 500.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from api.extensions import Db
from api.models import (
    OktaGroupTagMap,
)
from api.operations import (
    CreateAccessRequest,
    CreateRoleRequest,
    ModifyGroupUsers,
    ModifyRoleGroups,
)
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    RoleGroupFactory,
    TagFactory,
)


@pytest.fixture
async def production_shape(db: Db) -> dict[str, Any]:
    """Build the data shape from the ACCESS-FLASK Sentry reports.

    A tagged role group with member+owner associations to a tagged app group
    and a tagged plain group; a user holding direct and via-role access; a
    pending access request targeting the ROLE group (the rich detail ref that
    serializes the role's associated-group mappings); another targeting the
    app group; and a role request from the role to the app group.
    """
    user = OktaUserFactory.build()
    requester = OktaUserFactory.build()
    app = AppFactory.build()
    app_group = AppGroupFactory.build(app_id=app.id, name=f"App-{app.name}-Eng")
    okta_group = OktaGroupFactory.build()
    role_group = RoleGroupFactory.build()
    tags = [TagFactory.build(constraints={}) for _ in range(3)]

    db.session.add_all([user, requester, app, app_group, okta_group, role_group, *tags])
    await db.session.commit()
    db.session.add_all(
        [
            OktaGroupTagMap(tag_id=tags[0].id, group_id=app_group.id),
            OktaGroupTagMap(tag_id=tags[1].id, group_id=okta_group.id),
            OktaGroupTagMap(tag_id=tags[2].id, group_id=role_group.id),
        ]
    )
    await db.session.commit()

    # Role memberships first, then associate the role to both groups as
    # member AND owner so `active_role_associated_group_*_mappings` rows
    # exist with polymorphic `active_group` targets (AppGroup + OktaGroup).
    await ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=okta_group.id, members_to_add=[requester.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group.id,
        groups_to_add=[app_group.id, okta_group.id],
        owner_groups_to_add=[app_group.id],
        sync_to_okta=False,
    ).execute()

    # Pending access request targeting the ROLE group: the detail ref
    # serializes the role's associated-group mappings (ACCESS-FLASK-4N) and
    # through them each mapping's active_group (44) and its app (4E).
    request_role_target = await CreateAccessRequest(
        requester_user=requester, requested_group=role_group, request_reason="role target"
    ).execute()
    request_app_target = await CreateAccessRequest(
        requester_user=requester, requested_group=app_group, request_reason="app target"
    ).execute()
    role_request = await CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=app_group,
        request_ownership=False,
        request_reason="role to app",
    ).execute()

    shape = {
        "user": user.id,
        "requester": requester.id,
        "app": app.id,
        "app_group": app_group.id,
        "okta_group": okta_group.id,
        "role_group": role_group.id,
        "tag": tags[0].id,
        "access_request_role_target": request_role_target.id,
        "access_request_app_target": request_app_target.id,
        "role_request": role_request.id,
    }
    # Drop identity-map state staled by the ops above (expire_on_commit=False)
    # so the routes under test load everything through their own loaders.
    db.session.expire_all()
    return shape


async def _get_ok(client: AsyncClient, url: str) -> Any:
    rep = await client.get(url)
    assert rep.status_code == 200, f"{url} -> {rep.status_code}: {rep.text[:500]}"
    return rep.json()


async def test_group_list_serializes_role_associations_and_apps(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = await _get_ok(client, url_for("api-groups.groups"))
    by_id = {g["id"]: g for g in data["items"]}
    role = by_id[production_shape["role_group"]]
    # ACCESS-FLASK-4N: the role row exposes its associated-group mappings…
    assert role["active_role_associated_group_member_mappings"]
    # …44: each mapping exposes its active_group…
    actives = [m["active_group"] for m in role["active_role_associated_group_member_mappings"]]
    assert all(a is not None for a in actives)
    # …4E: and the app-group one exposes its app.
    app_refs = [a for a in actives if a["type"] == "app_group"]
    assert app_refs and all(a["app"] is not None for a in app_refs)
    # 4E-b: tags serialize on every row type.
    assert by_id[production_shape["okta_group"]]["active_group_tags"]
    assert by_id[production_shape["app_group"]]["active_group_tags"]
    assert role["active_group_tags"]


@pytest.mark.parametrize("key", ["role_group", "app_group", "okta_group"])
async def test_group_detail_for_each_type(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any, key: str
) -> None:
    data = await _get_ok(client, url_for("api-groups.group_by_id", group_id=production_shape[key]))
    assert data["active_group_tags"]
    if key == "role_group":
        mappings = data["active_role_associated_group_member_mappings"]
        assert mappings
        app_actives = [m["active_group"] for m in mappings if m["active_group"]["type"] == "app_group"]
        assert app_actives and all(a["app"] is not None for a in app_actives)
    if key == "app_group":
        assert data["app"] is not None


async def test_role_routes(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    await _get_ok(client, url_for("api-roles.roles"))
    detail = await _get_ok(client, url_for("api-roles.role_by_id", role_id=production_shape["role_group"]))
    mappings = detail["active_role_associated_group_member_mappings"]
    assert mappings
    assert any(m["active_group"]["type"] == "app_group" and m["active_group"]["app"] for m in mappings)
    await _get_ok(client, url_for("api-roles.role_members_by_id", role_id=production_shape["role_group"]))


async def test_user_routes_with_via_role_app_group_memberships(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    await _get_ok(client, url_for("api-users.users"))
    data = await _get_ok(client, url_for("api-users.user_by_id", user_id=production_shape["user"]))
    groups = [m["active_group"] for m in data["active_group_memberships"] if m.get("active_group")]
    app_groups = [g for g in groups if g["type"] == "app_group"]
    # via-role membership rows reach app groups; their `app` ref must be loaded
    assert app_groups and all(g["app"] is not None for g in app_groups)


async def test_access_request_detail_role_group_target(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = await _get_ok(
        client,
        url_for(
            "api-access-requests.access_request_by_id", access_request_id=production_shape["access_request_role_target"]
        ),
    )
    rg = data["requested_group"]
    assert rg["type"] == "role_group"
    assert rg["active_group_tags"]
    mappings = rg["active_role_associated_group_member_mappings"]
    assert mappings
    app_actives = [
        m["active_group"] for m in mappings if m["active_group"] and m["active_group"]["type"] == "app_group"
    ]
    assert app_actives and all(a["app"] is not None for a in app_actives)


async def test_access_request_detail_app_group_target_and_lists(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = await _get_ok(
        client,
        url_for(
            "api-access-requests.access_request_by_id", access_request_id=production_shape["access_request_app_target"]
        ),
    )
    assert data["requested_group"]["type"] == "app_group"
    assert data["requested_group"]["app"] is not None
    assert data["requested_group"]["active_group_tags"]
    await _get_ok(client, url_for("api-access-requests.access_requests"))


async def test_role_request_routes(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    await _get_ok(client, url_for("api-role-requests.role_requests"))
    data = await _get_ok(
        client, url_for("api-role-requests.role_request_by_id", role_request_id=production_shape["role_request"])
    )
    assert data["requested_group"]["type"] == "app_group"
    assert data["requested_group"]["app"] is not None


async def test_tag_and_app_routes(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    await _get_ok(client, url_for("api-tags.tags"))
    tag_detail = await _get_ok(client, url_for("api-tags.tag_by_id", tag_id=production_shape["tag"]))
    # the tag is attached to the APP group: the nested active_group must carry its app
    actives = [t["active_group"] for t in tag_detail["active_group_tags"] if t.get("active_group")]
    assert any(g["type"] == "app_group" and g.get("app") for g in actives)
    await _get_ok(client, url_for("api-apps.apps"))
    await _get_ok(client, url_for("api-apps.app_by_id", app_id=production_shape["app"]))


@pytest.mark.parametrize(
    "params",
    [
        "",  # unfiltered: emits role-associated mappings (the include_role_associations branch)
        "user_id={user}",
        "group_id={role_group}",
        "group_id={app_group}",
    ],
)
async def test_audit_users_with_polymorphic_rows(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any, params: str
) -> None:
    # The Flask-era comments singled out exactly this endpoint with user_id /
    # group_id filters as the unloadable case that forced lazy="select".
    url = url_for("api-audit.users_and_groups")
    qs = params.format(**production_shape)
    data = await _get_ok(client, f"{url}?{qs}" if qs else url)
    assert data["items"]


@pytest.mark.parametrize("params", ["", "role_id={role_group}", "group_id={app_group}"])
async def test_audit_groups_with_polymorphic_rows(
    app: FastAPI, client: AsyncClient, db: Db, production_shape: dict[str, Any], url_for: Any, params: str
) -> None:
    url = url_for("api-audit.groups_and_roles")
    qs = params.format(**production_shape)
    data = await _get_ok(client, f"{url}?{qs}" if qs else url)
    assert data["items"]
