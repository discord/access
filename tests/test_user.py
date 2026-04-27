from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from api.config import settings
from api.models import App, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations import ModifyGroupUsers, ModifyRoleGroups

from tests.factories import OktaUserFactory
from typing import Any


def test_get_user_at_me_includes_group_memberships(
    client: TestClient,
    db: Any,
    okta_group: OktaGroup,
    url_for: Any,
) -> None:
    """The /users/@me page reads active_group_memberships and
    active_group_ownerships to render the user's groups. The migrated
    OktaUserDetail originally dropped these lists."""
    db.session.add(okta_group)
    db.session.commit()
    access_user = OktaUser.query.filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[access_user.id],
        owners_to_add=[access_user.id],
        sync_to_okta=False,
    ).execute()

    rep = client.get(url_for("api-users.user_by_id", user_id="@me"))
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert "active_group_memberships" in data
    assert "active_group_ownerships" in data
    member_group_ids = [m["active_group"]["id"] for m in data["active_group_memberships"] if m.get("active_group")]
    owner_group_ids = [m["active_group"]["id"] for m in data["active_group_ownerships"] if m.get("active_group")]
    assert okta_group.id in member_group_ids
    assert okta_group.id in owner_group_ids


def test_get_user_profile_filtered_by_allowlist(
    client: TestClient,
    db: Any,
    user: OktaUser,
    url_for: Any,
) -> None:
    """`OktaUser.profile` should be filtered to the keys in
    `USER_DISPLAY_CUSTOM_ATTRIBUTES`. Anything else must be stripped."""
    user.profile = {"Title": "Engineer", "Manager": "boss@example.com", "Secret": "shh"}
    db.session.add(user)
    db.session.commit()

    rep = client.get(url_for("api-users.user_by_id", user_id=user.id))
    assert rep.status_code == 200, rep.text
    profile = rep.json()["profile"]
    # Defaults are "Title,Manager"
    assert "Title" in profile
    assert "Manager" in profile
    assert "Secret" not in profile


def test_get_user(
    client: TestClient,
    db: Any,
    user: OktaUser,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup, url_for: Any) -> None:
    # test 404
    user_url = url_for("api-users.user_by_id", user_id="randomid")
    rep = client.get(user_url)
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
    user_name = user.display_name
    user_email = user.email

    db.session.expunge_all()

    # test get user
    user_url = url_for("api-users.user_by_id", user_id=user_id)
    rep = client.get(user_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["display_name"] == user_name
    assert data["email"] == user_email


def test_put_user(client: TestClient, db: Any, user: OktaUser, url_for: Any) -> None:
    db.session.add(user)
    db.session.commit()

    # test 405
    user_url = url_for("api-users.user_by_id", user_id=user.id)
    rep = client.put(user_url)
    assert rep.status_code == 405


def test_delete_user(client: TestClient, db: Any, user: OktaUser, url_for: Any) -> None:
    db.session.add(user)
    db.session.commit()

    # test 405
    user_url = url_for("api-users.user_by_id", user_id=user.id)
    rep = client.delete(user_url)
    assert rep.status_code == 405


def test_create_user(client: TestClient, db: Any, user: OktaUser, url_for: Any) -> None:
    # test 405 (POST not allowed on /api/users)
    users_url = url_for("api-users.users")
    rep = client.post(users_url, json={"id": user.id, "email": user.email})
    assert rep.status_code == 405


def test_get_all_user(client: TestClient, db: Any, url_for: Any) -> None:
    users_url = url_for("api-users.users")
    users = OktaUserFactory.create_batch(10)

    db.session.add_all(users)
    db.session.commit()

    rep = client.get(users_url)
    assert rep.status_code == 200

    results = rep.json()
    for user in users:
        assert any(u["id"] == user.id for u in results["results"])

    rep = client.get(users_url, params={"q": "a"})
    assert rep.status_code == 200

    results = rep.json()
    for user in users:
        assert any(u["id"] == user.id for u in results["results"])


def test_user_email_uniqueness(client: TestClient, db: Any) -> None:
    known_email = "test@email.com"

    # Create a user with a unique email
    user1 = OktaUserFactory.create()

    user1.email = known_email

    db.session.add(user1)
    db.session.commit()

    # Trying to insert a user with the same email should fail
    user2 = OktaUserFactory.create()
    user2.email = known_email
    try:
        db.session.add(user2)
        db.session.commit()
        assert False
    except IntegrityError as e:
        assert "constraint" in str(e)
        db.session.rollback()

    # Verify another user with the same email can be inserted if its state is deleted
    user2.deleted_at = datetime.now(timezone.utc)
    db.session.add(user2)
    db.session.commit()  # This should not raise an exception
