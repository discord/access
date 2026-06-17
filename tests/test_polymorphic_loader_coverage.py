"""Regression battery for the production lazy-load errors that motivated
demoting five relationships to ``lazy="select"``.

Under the original ``raise_on_sql`` regime, production raised
``InvalidRequestError: ... is not available due to lazy='raise_on_sql'`` for
``RoleGroup.active_role_associated_group_member_mappings``,
``RoleGroupMap.active_group``, ``AppGroup.app``, and
``OktaGroup.active_group_tags`` — yet the errors never reproduced in tests.
The reason: ``raise_on_sql`` raises identically under test, so the only
production-only ingredient was DATA SHAPE. The failing serializations needed
polymorphic subtypes in nested positions (a role group whose associated groups
are app groups, tagged groups inside request refs, app groups inside
membership rows) that the suite never built. Now that the five relationships
are back on ``raise_on_sql``, this battery builds the production shape once
and walks every read surface: any eager-load gap fails deterministically here
instead of as a paged 500.

The error chain maps onto the schema nesting: a role group in a rich position
serializes its associated-group mappings, each mapping serializes its
``active_group``, an app-group ``active_group`` serializes its ``app``, and
every rich group ref serializes ``active_group_tags``.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.extensions import Db, db as _db
from api.models import AppTagMap, OktaGroupTagMap
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
def production_shape(db: Db) -> dict[str, Any]:
    """Build the data shape from the production error reports.

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
    tags = [TagFactory.build(constraints={}) for _ in range(4)]

    db.session.add_all([user, requester, app, app_group, okta_group, role_group, *tags])
    db.session.commit()
    # tags[3] is an app-level tag (AppTagMap) that propagates to the app group,
    # so the app group's OktaGroupTagMap carries a non-None
    # `active_app_tag_mapping` — the only way that relationship's non-None
    # serialization path (-> AppTagMapDetail) gets exercised. tags[0..2] are
    # direct group tags (active_app_tag_mapping is None).
    app_tag_map = AppTagMap(tag_id=tags[3].id, app_id=app.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add_all(
        [
            OktaGroupTagMap(tag_id=tags[0].id, group_id=app_group.id),
            OktaGroupTagMap(tag_id=tags[1].id, group_id=okta_group.id),
            OktaGroupTagMap(tag_id=tags[2].id, group_id=role_group.id),
            OktaGroupTagMap(tag_id=tags[3].id, group_id=app_group.id, app_tag_map_id=app_tag_map.id),
        ]
    )
    db.session.commit()

    # Role memberships first, then associate the role to both groups as
    # member AND owner so `active_role_associated_group_*_mappings` rows
    # exist with polymorphic `active_group` targets (AppGroup + OktaGroup).
    ModifyGroupUsers(
        group=role_group.id, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=okta_group.id, members_to_add=[requester.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group.id,
        groups_to_add=[app_group.id, okta_group.id],
        owner_groups_to_add=[app_group.id],
        sync_to_okta=False,
    ).execute()

    # Pending access request targeting the ROLE group: the detail ref
    # serializes the role's associated-group mappings, and through them each
    # mapping's active_group and its app.
    request_role_target = CreateAccessRequest(
        requester_user=requester, requested_group=role_group, request_reason="role target"
    ).execute()
    request_app_target = CreateAccessRequest(
        requester_user=requester, requested_group=app_group, request_reason="app target"
    ).execute()
    role_request = CreateRoleRequest(
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
        "app_propagated_tag": tags[3].id,
        "access_request_role_target": request_role_target.id,
        "access_request_app_target": request_app_target.id,
        "role_request": role_request.id,
    }
    # Evict every object the setup ops loaded. In tests the HTTP request reuses
    # this same session, so a warm identity map can serve relationships the
    # route's own loaders never eager-loaded — masking gaps that fail in
    # production, where each request gets a cold session. `expunge_all` (not
    # `expire_all`, which keeps objects in the map) forces the routes to load
    # everything through their declared options, so a missing eager-load raises
    # `lazy="raise_on_sql"` deterministically here instead of flaking in CI.
    db.session.expunge_all()
    return shape


def _get_ok(client: TestClient, url: str) -> Any:
    # The TestClient shares this session, so a prior request (or fixture setup)
    # can leave relationships warm in the identity map and mask a route whose
    # own loaders never eager-loaded them. Production gives each request a cold
    # session, so evict before every call: a missing eager-load then raises
    # `lazy="raise_on_sql"` deterministically instead of flaking by test order.
    _db.session.expunge_all()
    rep = client.get(url)
    assert rep.status_code == 200, f"{url} -> {rep.status_code}: {rep.text[:500]}"
    return rep.json()


def test_group_list_serializes_role_associations_and_apps(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = _get_ok(client, url_for("api-groups.groups"))
    by_id = {g["id"]: g for g in data["items"]}
    role = by_id[production_shape["role_group"]]
    # the role row exposes its associated-group mappings…
    assert role["active_role_associated_group_member_mappings"]
    # …each mapping exposes its active_group…
    actives = [m["active_group"] for m in role["active_role_associated_group_member_mappings"]]
    assert all(a is not None for a in actives)
    # …and the app-group one exposes its app.
    app_refs = [a for a in actives if a["type"] == "app_group"]
    assert app_refs and all(a["app"] is not None for a in app_refs)
    # tags serialize on every row type.
    assert by_id[production_shape["okta_group"]]["active_group_tags"]
    assert by_id[production_shape["app_group"]]["active_group_tags"]
    assert role["active_group_tags"]


@pytest.mark.parametrize("key", ["role_group", "app_group", "okta_group"])
def test_group_detail_for_each_type(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any, key: str
) -> None:
    data = _get_ok(client, url_for("api-groups.group_by_id", group_id=production_shape[key]))
    assert data["active_group_tags"]
    if key == "role_group":
        mappings = data["active_role_associated_group_member_mappings"]
        assert mappings
        app_actives = [m["active_group"] for m in mappings if m["active_group"]["type"] == "app_group"]
        assert app_actives and all(a["app"] is not None for a in app_actives)
    if key == "app_group":
        assert data["app"] is not None
        # Reverse role-association: a role is associated to this app group, so its
        # mappings serialize RoleGroupMap.active_group from the group side.
        assert data["active_role_member_mappings"]
        assert all(m["active_group"] is not None for m in data["active_role_member_mappings"])
        # The app-propagated tag carries a non-None OktaGroupTagMap.active_app_tag_mapping
        # (-> AppTagMapDetail); direct tags leave it None, so this is the only place
        # that relationship's non-None serialization is exercised.
        propagated = [t for t in data["active_group_tags"] if t.get("active_app_tag_mapping")]
        assert propagated, "expected the app-propagated tag to serialize active_app_tag_mapping"
        assert propagated[0]["active_app_tag_mapping"]["active_tag"] is not None


def test_role_routes(app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any) -> None:
    _get_ok(client, url_for("api-roles.roles"))
    detail = _get_ok(client, url_for("api-roles.role_by_id", role_id=production_shape["role_group"]))
    mappings = detail["active_role_associated_group_member_mappings"]
    assert mappings
    assert any(m["active_group"]["type"] == "app_group" and m["active_group"]["app"] for m in mappings)
    _get_ok(client, url_for("api-roles.role_members_by_id", role_id=production_shape["role_group"]))


def test_user_routes_with_via_role_app_group_memberships(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    _get_ok(client, url_for("api-users.users"))
    data = _get_ok(client, url_for("api-users.user_by_id", user_id=production_shape["user"]))
    groups = [m["active_group"] for m in data["active_group_memberships"] if m.get("active_group")]
    app_groups = [g for g in groups if g["type"] == "app_group"]
    # via-role membership rows reach app groups; their `app` ref must be loaded
    assert app_groups and all(g["app"] is not None for g in app_groups)


def test_access_request_detail_role_group_target(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = _get_ok(
        client,
        url_for(
            "api-access-requests.access_request_by_id",
            access_request_id=production_shape["access_request_role_target"],
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


def test_access_request_detail_app_group_target_and_lists(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    data = _get_ok(
        client,
        url_for(
            "api-access-requests.access_request_by_id",
            access_request_id=production_shape["access_request_app_target"],
        ),
    )
    assert data["requested_group"]["type"] == "app_group"
    assert data["requested_group"]["app"] is not None
    assert data["requested_group"]["active_group_tags"]
    _get_ok(client, url_for("api-access-requests.access_requests"))


def test_role_request_routes(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    _get_ok(client, url_for("api-role-requests.role_requests"))
    data = _get_ok(
        client, url_for("api-role-requests.role_request_by_id", role_request_id=production_shape["role_request"])
    )
    assert data["requested_group"]["type"] == "app_group"
    assert data["requested_group"]["app"] is not None
    # The role-request detail ref serializes OktaGroup.active_group_tags too.
    assert data["requested_group"]["active_group_tags"]


def test_tag_and_app_routes(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any
) -> None:
    _get_ok(client, url_for("api-tags.tags"))
    tag_detail = _get_ok(client, url_for("api-tags.tag_by_id", tag_id=production_shape["tag"]))
    # the tag is attached to the APP group: the nested active_group must carry its app
    actives = [t["active_group"] for t in tag_detail["active_group_tags"] if t.get("active_group")]
    assert any(g["type"] == "app_group" and g.get("app") for g in actives)
    _get_ok(client, url_for("api-apps.apps"))
    _get_ok(client, url_for("api-apps.app_by_id", app_id=production_shape["app"]))


@pytest.mark.parametrize(
    "params",
    [
        "",  # unfiltered: emits role-associated mappings (the include_role_associations branch)
        "user_id={user}",
        "group_id={role_group}",
        "group_id={app_group}",
    ],
)
def test_audit_users_with_polymorphic_rows(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any, params: str
) -> None:
    # The user_id / group_id filters are the historical unloadable case that
    # forced lazy="select" on these relationships.
    url = url_for("api-audit.users_and_groups")
    qs = params.format(**production_shape)
    data = _get_ok(client, f"{url}?{qs}" if qs else url)
    assert data["items"]


@pytest.mark.parametrize("params", ["", "role_id={role_group}", "group_id={app_group}"])
def test_audit_groups_with_polymorphic_rows(
    app: FastAPI, client: TestClient, db: Db, production_shape: dict[str, Any], url_for: Any, params: str
) -> None:
    url = url_for("api-audit.groups_and_roles")
    qs = params.format(**production_shape)
    data = _get_ok(client, f"{url}?{qs}" if qs else url)
    assert data["items"]
