from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from api.models import (
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.extensions import Db
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from tests.factories import OktaGroupFactory, OktaUserFactory, RoleGroupFactory
from typing import Any


def test_user_audit_resolves_at_me(client: TestClient, db: Db, url_for: Any) -> None:
    """`?user_id=@me` is the reserved alias the React Expiring page uses;
    it must resolve to the authenticated user's id, not 404."""
    user_url = url_for("api-audit.users_and_groups")
    rep = client.get(
        user_url,
        params={"user_id": "@me", "page": 0, "per_page": 20, "order_by": "created_at", "order_desc": "true"},
    )
    assert rep.status_code == 200, rep.text
    assert "results" in rep.json()


def test_group_audit_resolves_at_me_role_owner(client: TestClient, db: Db, url_for: Any) -> None:
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
    db: Db,
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
    db: Db,
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
    db: Db,
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
    db: Db,
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
    db: Db,
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


def test_audit_users_default_order_is_newest_first(client: TestClient, db: Db, url_for: Any) -> None:
    """Without `order_by` / `order_desc`, /api/audit/users must default to
    `created_at DESC`. Flask's Marshmallow schema declared
    `order_desc=True`; the FastAPI Query Model mirrors that. Seed three
    rows with controlled `created_at` so a regression that flips the
    direction reliably fails this test."""
    group = OktaGroupFactory.create()
    u_old, u_mid, u_new = (OktaUserFactory.create() for _ in range(3))
    db.session.add_all([group, u_old, u_mid, u_new])
    db.session.commit()
    ModifyGroupUsers(
        group=group,
        members_to_add=[u_old.id, u_mid.id, u_new.id],
        sync_to_okta=False,
    ).execute()

    base = datetime.now(timezone.utc) - timedelta(days=10)
    pinned = {
        u_old.id: base,
        u_mid.id: base + timedelta(days=2),
        u_new.id: base + timedelta(days=4),
    }
    for ugm in db.session.query(OktaUserGroupMember).filter(OktaUserGroupMember.group_id == group.id).all():
        if ugm.user_id in pinned:
            ugm.created_at = pinned[ugm.user_id]
    db.session.commit()

    rep = client.get(url_for("api-audit.users_and_groups"), params={"group_id": group.id})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    seeded_rows = [r for r in rows if r["user_id"] in pinned]
    assert [r["user_id"] for r in seeded_rows] == [u_new.id, u_mid.id, u_old.id]


def test_audit_groups_default_order_is_newest_first(client: TestClient, db: Db, url_for: Any) -> None:
    """Same direction-of-default contract as `users` — seed three
    `RoleGroupMap` rows at controlled `created_at` and confirm the
    response order is newest-first when no order params are provided."""
    role = RoleGroupFactory.create()
    g_old, g_mid, g_new = (OktaGroupFactory.create() for _ in range(3))
    db.session.add_all([role, g_old, g_mid, g_new])
    db.session.commit()
    ModifyRoleGroups(
        role_group=role,
        groups_to_add=[g_old.id, g_mid.id, g_new.id],
        sync_to_okta=False,
    ).execute()

    base = datetime.now(timezone.utc) - timedelta(days=10)
    pinned = {
        g_old.id: base,
        g_mid.id: base + timedelta(days=2),
        g_new.id: base + timedelta(days=4),
    }
    for rgm in db.session.query(RoleGroupMap).filter(RoleGroupMap.role_group_id == role.id).all():
        if rgm.group_id in pinned:
            rgm.created_at = pinned[rgm.group_id]
    db.session.commit()

    rep = client.get(url_for("api-audit.groups_and_roles"), params={"role_id": role.id})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    seeded_rows = [r for r in rows if r["group_id"] in pinned]
    assert [r["group_id"] for r in seeded_rows] == [g_new.id, g_mid.id, g_old.id]


# --- Fix 4 parity tests --------------------------------------------------------


def test_users_audit_q_with_user_id_searches_only_groups(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When `user_id` is pinned, the free-text `q` filter must narrow to
    *group* columns only — `?user_id=...&q=Alice` should match group
    names but not user profile fields (the user is already pinned)."""
    user = OktaUserFactory.create(email="alice@example.com", first_name="Alice", last_name="A")
    matching_group = OktaGroupFactory.create(name="MatchableGroup", description="contains Alice")
    other_group = OktaGroupFactory.create(name="UnrelatedGroup", description="no match")
    db.session.add_all([user, matching_group, other_group])
    db.session.commit()
    ModifyGroupUsers(group=matching_group, members_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=other_group, members_to_add=[user.id], sync_to_okta=False).execute()

    # Searching `Alice` with the user filter pinned should only match the
    # group whose description contains "Alice" — not the other group, even
    # though the user's first_name is "Alice".
    rep = client.get(url_for("api-audit.users_and_groups"), params={"user_id": user.id, "q": "Alice"})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    matched_group_ids = {r["group_id"] for r in rows}
    assert matching_group.id in matched_group_ids
    assert other_group.id not in matched_group_ids


def test_users_audit_q_with_group_id_searches_only_users(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """Symmetric: with `group_id` set, `q` must restrict to user columns
    so the response excludes memberships whose user doesn't match — even
    if the group name does."""
    matching_user = OktaUserFactory.create(email="zlatan@example.com", first_name="Zlatan")
    other_user = OktaUserFactory.create(email="other@example.com", first_name="Other")
    group = OktaGroupFactory.create(name="GroupZlatan", description="zlatan-themed")
    db.session.add_all([matching_user, other_user, group])
    db.session.commit()
    ModifyGroupUsers(
        group=group, members_to_add=[matching_user.id, other_user.id], sync_to_okta=False
    ).execute()

    rep = client.get(url_for("api-audit.users_and_groups"), params={"group_id": group.id, "q": "zlatan"})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    matched_user_ids = {r["user_id"] for r in rows}
    assert matching_user.id in matched_user_ids
    assert other_user.id not in matched_user_ids


def test_groups_audit_q_with_role_id_searches_only_groups(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When `role_id` is set on /api/audit/groups, `q` must restrict to
    the *associated group* columns and ignore the role columns."""
    role = RoleGroupFactory.create(name="ZebraRole")
    matching_group = OktaGroupFactory.create(name="ZebraStripeGroup")
    other_group = OktaGroupFactory.create(name="UnrelatedGroup")
    db.session.add_all([role, matching_group, other_group])
    db.session.commit()
    ModifyRoleGroups(
        role_group=role,
        groups_to_add=[matching_group.id, other_group.id],
        sync_to_okta=False,
    ).execute()

    rep = client.get(url_for("api-audit.groups_and_roles"), params={"role_id": role.id, "q": "Zebra"})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    matched_group_ids = {r["group_id"] for r in rows}
    assert matching_group.id in matched_group_ids
    # `q="Zebra"` matches the role's name too, but with `role_id` pinned
    # the q-filter must only consider associated-group columns.
    assert other_group.id not in matched_group_ids


def test_users_audit_owner_id_excludes_owner_self_membership(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When filtering /api/audit/users by `owner_id`, the response must
    exclude the owner's own memberships. The frontend's "expiring access"
    review surface is meant to show *other* users' access the owner owes
    a renewal on, not the owner's own."""
    owner = OktaUserFactory.create(email="own@example.com")
    other = OktaUserFactory.create(email="other@example.com")
    group = OktaGroupFactory.create(name="OwnedGroup")
    db.session.add_all([owner, other, group])
    db.session.commit()
    # Owner is owner of group; owner is also a regular member of the same
    # group; another user is also a member.
    ModifyGroupUsers(
        group=group,
        members_to_add=[owner.id, other.id],
        owners_to_add=[owner.id],
        sync_to_okta=False,
    ).execute()

    rep = client.get(url_for("api-audit.users_and_groups"), params={"owner_id": owner.id})
    assert rep.status_code == 200
    rows = rep.json()["results"]
    user_ids_in_response = {r["user_id"] for r in rows}
    assert owner.id not in user_ids_in_response
    assert other.id in user_ids_in_response


def test_users_audit_includes_role_associated_group_mappings_when_unfiltered(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When neither `user_id` nor `group_id` is set, rows whose group is a
    RoleGroup must surface `active_role_associated_group_member_mappings`
    and `active_role_associated_group_owner_mappings` so the React UI can
    render the role's associated-groups rollup."""
    role = RoleGroupFactory.create(name="UnfilteredRole")
    associated = OktaGroupFactory.create(name="UnfilteredAssociatedGroup")
    user = OktaUserFactory.create()
    db.session.add_all([role, associated, user])
    db.session.commit()
    ModifyRoleGroups(role_group=role, groups_to_add=[associated.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role, members_to_add=[user.id], sync_to_okta=False).execute()

    rep = client.get(url_for("api-audit.users_and_groups"))
    assert rep.status_code == 200, rep.text
    rows = rep.json()["results"]
    role_rows = [r for r in rows if r.get("group", {}).get("id") == role.id]
    assert role_rows, "expected at least one row for the role group"
    sample = role_rows[0]["group"]
    assert "active_role_associated_group_member_mappings" in sample
    member_maps = sample["active_role_associated_group_member_mappings"]
    assert any(
        (mm or {}).get("active_group", {}).get("id") == associated.id for mm in member_maps
    ), f"associated group missing from member mappings: {member_maps}"


def test_users_audit_omits_role_associated_group_mappings_when_user_filter(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """With `user_id` pinned, `active_role_associated_group_*_mappings`
    must be suppressed on the response (eager-loading them under a
    polymorphic `with_polymorphic` query is unstable). The serializer
    must not attach those fields in this mode."""
    role = RoleGroupFactory.create(name="FilteredRole")
    associated = OktaGroupFactory.create(name="FilteredAssociatedGroup")
    user = OktaUserFactory.create()
    db.session.add_all([role, associated, user])
    db.session.commit()
    ModifyRoleGroups(role_group=role, groups_to_add=[associated.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role, members_to_add=[user.id], sync_to_okta=False).execute()

    rep = client.get(url_for("api-audit.users_and_groups"), params={"user_id": user.id})
    assert rep.status_code == 200, rep.text
    rows = rep.json()["results"]
    role_rows = [r for r in rows if r.get("group", {}).get("id") == role.id]
    assert role_rows
    sample = role_rows[0]["group"]
    # Either absent from the dict or set to an empty list — both behaviors
    # are acceptable, but the populated case is a regression.
    assert not sample.get("active_role_associated_group_member_mappings")
    assert not sample.get("active_role_associated_group_owner_mappings")


def test_users_audit_direct_flag_reorders(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When `direct` is present and neither `user_id` nor `owner_id` is
    supplied, the order_by re-applies using the email/created_at compound
    shape: with `?direct=true&order_by=moniker`, rows come back ordered
    by lowercased email asc rather than insertion order."""
    group = OktaGroupFactory.create()
    u_z = OktaUserFactory.create(email="zara@example.com")
    u_a = OktaUserFactory.create(email="alpha@example.com")
    db.session.add_all([group, u_a, u_z])
    db.session.commit()
    # Insert in non-alphabetical order so a default unordered query would
    # surface them in insertion order rather than sorted.
    ModifyGroupUsers(group=group, members_to_add=[u_z.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group, members_to_add=[u_a.id], sync_to_okta=False).execute()

    rep = client.get(
        url_for("api-audit.users_and_groups"),
        params={"direct": "true", "order_by": "moniker", "order_desc": "false"},
    )
    assert rep.status_code == 200, rep.text
    rows = rep.json()["results"]
    seeded_emails = [
        r["user"]["email"]
        for r in rows
        if r["user"]["email"] in {"zara@example.com", "alpha@example.com"}
    ]
    assert seeded_emails == ["alpha@example.com", "zara@example.com"]
