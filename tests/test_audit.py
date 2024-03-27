from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy

from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.operations import ModifyGroupUsers, ModifyRoleGroups


def test_get_user_audit(client: FlaskClient, db: SQLAlchemy, user: OktaUser, access_app: App, app_group: AppGroup, role_group: RoleGroup, okta_group: OktaGroup) -> None:
    # test 404
    user_url = url_for("api-audit.users_and_groups")
    rep = client.get(user_url, query_string={"user_id": "randomid"})
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=app_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[okta_group.id, app_group.id],
        owner_groups_to_add=[okta_group.id, app_group.id],
        sync_to_okta=False
    ).execute()

    user_id = user.id

    db.session.expunge_all()

    # test get user
    rep = client.get(user_url, query_string={"user_id": user_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 10
    assert data["total"] == 10

    db.session.expunge_all()

    rep = client.get(user_url, query_string={"user_id":  user_id, "owner": True})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 5
    assert data["total"] == 5

    db.session.expunge_all()

    rep = client.get(user_url, query_string={"user_id":  user_id, "q": "App-"})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

def test_get_group_audit(client: FlaskClient, db: SQLAlchemy, access_app: App, app_group: AppGroup, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser, tag: Tag) -> None:
    # test 404
    group_url = url_for("api-audit.users_and_groups")
    rep = client.get(group_url, query_string={"group_id": "randomid"})
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

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=app_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[okta_group.id, app_group.id],
        owner_groups_to_add=[okta_group.id, app_group.id],
        sync_to_okta=False
    ).execute()

    user_email = user.email
    okta_group_id = okta_group.id
    role_group_id = role_group.id
    app_group_id = app_group.id

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": role_group_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    # test get group
    rep = client.get(group_url, query_string={"group_id": okta_group_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": okta_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": okta_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": role_group_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": role_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 1
    assert data["total"] == 1

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": role_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": app_group_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": app_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(group_url, query_string={"group_id": app_group_id, "q": user_email})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

def test_get_role_audit(client: FlaskClient, db: SQLAlchemy, role_group: RoleGroup, access_app: App, app_group: AppGroup, okta_group: OktaGroup, user: OktaUser) -> None:
    # test 404
    role_url = url_for("api-audit.groups_and_roles")
    rep = client.get(role_url, query_string={"role_id": "randomid"})
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=app_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
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
        sync_to_okta=False
    ).execute()

    role_group_id = role_group.id
    app_group_id = app_group.id
    okta_group_id = okta_group.id

    db.session.expunge_all()

    # test get role
    rep = client.get(role_url, query_string={"role_id": role_group_id})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 4
    assert data["total"] == 4

    db.session.expunge_all()

    rep = client.get(role_url, query_string={"role_id": role_group_id, "owner": True})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(role_url, query_string={"role_id": role_group_id, "q": "App-"})
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["results"]) == 2
    assert data["total"] == 2

    db.session.expunge_all()

    rep = client.get(role_url, query_string={"role_id": app_group_id})
    assert rep.status_code == 404

    db.session.expunge_all()

    rep = client.get(role_url, query_string={"role_id": okta_group_id})
    assert rep.status_code == 404
