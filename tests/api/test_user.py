from datetime import datetime, timezone

from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

from api.models import App, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.views.schemas import OktaUserSchema
from tests.factories import OktaUserFactory


def test_get_user(
    client: FlaskClient,
    db: SQLAlchemy,
    user: OktaUser,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup,
) -> None:
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

    data = rep.get_json()
    assert data["display_name"] == user_name
    assert data["email"] == user_email


def test_put_user(client: FlaskClient, db: SQLAlchemy, user: OktaUser) -> None:
    db.session.add(user)
    db.session.commit()

    # test 405
    user_url = url_for("api-users.user_by_id", user_id=user.id)
    rep = client.put(user_url)
    assert rep.status_code == 405


def test_delete_user(client: FlaskClient, db: SQLAlchemy, user: OktaUser) -> None:
    db.session.add(user)
    db.session.commit()

    # test 405
    user_url = url_for("api-users.user_by_id", user_id=user.id)
    rep = client.delete(user_url)
    assert rep.status_code == 405


def test_create_user(client: FlaskClient, db: SQLAlchemy, user: OktaUser) -> None:
    # test 405
    users_url = url_for("api-users.users")
    # marshmallow-sqlalchemy SQLAlchemyAutoSchema constructors are not typed
    data = OktaUserSchema().dump(user)  # type: ignore[no-untyped-call]
    rep = client.post(users_url, json=data)
    assert rep.status_code == 405


def test_get_all_user(client: FlaskClient, db: SQLAlchemy) -> None:
    users_url = url_for("api-users.users")
    users = OktaUserFactory.create_batch(10)

    db.session.add_all(users)
    db.session.commit()

    rep = client.get(users_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for user in users:
        assert any(u["id"] == user.id for u in results["results"])

    rep = client.get(users_url, query_string={"q": "a"})
    assert rep.status_code == 200

    results = rep.get_json()
    for user in users:
        assert any(u["id"] == user.id for u in results["results"])


def test_user_email_uniqueness(client: FlaskClient, db: SQLAlchemy) -> None:
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
