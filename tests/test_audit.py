from fastapi.testclient import TestClient

from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from typing import Any


def test_user_audit_resolves_at_me(client: TestClient, db: Any, url_for: Any) -> None:
    """`?user_id=@me` is the reserved alias the React Expiring page uses;
    it must resolve to the authenticated user's id, not 404."""
    user_url = url_for("api-audit.users_and_groups")
    rep = client.get(
        user_url,
        params={"user_id": "@me", "page": 0, "per_page": 20, "order_by": "created_at", "order_desc": "true"},
    )
    assert rep.status_code == 200, rep.text
    assert "results" in rep.json()


def test_group_audit_resolves_at_me_role_owner(client: TestClient, db: Any, url_for: Any) -> None:
    """`?role_owner_id=@me` and `?owner_id=@me` should both resolve to the
    authenticated user."""
    url = url_for("api-audit.groups_and_roles")
    for params in (
        {"role_owner_id": "@me"},
        {"owner_id": "@me"},
    ):
        rep = client.get(url, params={**params, "page": 0, "per_page": 20})
        assert rep.status_code == 200, rep.text


def test_user_audit_returns_nested_objects(
    client: TestClient,
    db: Any,
    user: OktaUser,
    okta_group: OktaGroup,
    url_for: Any,
) -> None:
    """The Expiring access page reads row.user, row.group (with nested .app),
    row.role_group_mapping etc. Confirm the audit endpoint emits these."""
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    ModifyGroupUsers(group=okta_group, members_to_add=[user.id], sync_to_okta=False).execute()

    rep = client.get(url_for("api-audit.users_and_groups"), params={"user_id": user.id})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    assert len(rows) >= 1
    row = rows[0]
    assert row["user"]["id"] == user.id
    assert row["user"]["email"] == user.email
    assert row["group"]["id"] == okta_group.id
    assert row["group"]["name"] == okta_group.name
    assert row["group"]["type"] == "okta_group"


def test_group_audit_returns_nested_role_and_group(
    client: TestClient,
    db: Any,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    url_for: Any,
) -> None:
    """Expiring-roles reads row.role_group, row.group on each row; without
    these the page renders blank role/group names."""
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.commit()
    ModifyRoleGroups(role_group=role_group, groups_to_add=[okta_group.id], sync_to_okta=False).execute()

    rep = client.get(url_for("api-audit.groups_and_roles"), params={"role_id": role_group.id})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    assert len(rows) >= 1
    row = rows[0]
    assert row["role_group"]["id"] == role_group.id
    assert row["role_group"]["name"] == role_group.name
    assert row["group"]["id"] == okta_group.id
    assert row["group"]["name"] == okta_group.name


def test_get_user_audit(
    client: TestClient,
    db: Any,
    user: OktaUser,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    url_for: Any,
) -> None:
    # test 404
    user_url = url_for("api-audit.users_and_groups")
    rep = client.get(user_url, params={"user_id": "randomid"})
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(group=okta_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[okta_group.id, app_group.id],
        owner_groups_to_add=[okta_group.id, app_group.id],
        sync_to_okta=False,
    ).execute()

    user_id = user.id

    db.session.expunge_all()

    # test get user
    rep = client.get(user_url, params={"user_id": user_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 10
    assert data["total"] == 10

    db.session.expunge_all()

    rep = client.get(user_url, params={"user_id": user_id, "owner": True})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 5
    assert data["total"] == 5

    db.session.expunge_all()

    rep = client.get(user_url, params={"user_id": user_id, "q": "App-"})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4


def test_get_group_audit(
    client: TestClient,
    db: Any,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    tag: Tag,
    url_for: Any,
) -> None:
    # test 404
    group_url = url_for("api-audit.users_and_groups")
    rep = client.get(group_url, params={"group_id": "randomid"})
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(tag)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    ModifyGroupUsers(group=okta_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[okta_group.id, app_group.id],
        owner_groups_to_add=[okta_group.id, app_group.id],
        sync_to_okta=False,
    ).execute()

    user_email = user.email
    okta_group_id = okta_group.id
    role_group_id = role_group.id
    app_group_id = app_group.id

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": role_group_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    # test get group
    rep = client.get(group_url, params={"group_id": okta_group_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": okta_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": okta_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": role_group_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": role_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 1
    assert data["total"] == 1

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": role_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": app_group_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": app_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, params={"group_id": app_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4


def test_get_role_audit(
    client: TestClient,
    db: Any,
    role_group: RoleGroup,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    # test 404
    role_url = url_for("api-audit.groups_and_roles")
    rep = client.get(role_url, params={"role_id": "randomid"})
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(group=okta_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            okta_group.id,
            app_group.id,
        ],
        owner_groups_to_add=[
            okta_group.id,
            app_group.id,
        ],
        sync_to_okta=False,
    ).execute()

    role_group_id = role_group.id
    app_group_id = app_group.id
    okta_group_id = okta_group.id

    db.session.expunge_all()

    # test get role
    rep = client.get(role_url, params={"role_id": role_group_id})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(role_url, params={"role_id": role_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(role_url, params={"role_id": role_group_id, "q": "App-"})
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(role_url, params={"role_id": app_group_id})
    assert rep.status_code == 404

    db.session.expunge_all()

    rep = client.get(role_url, params={"role_id": okta_group_id})
    assert rep.status_code == 404
