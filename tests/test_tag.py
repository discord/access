from typing import Any

from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, Tag
from tests.factories import TagFactory


def test_get_tag(client: FlaskClient, db: SQLAlchemy, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup) -> None:
    # test 404
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.get(tag_url)
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    # test get tag
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.get(tag_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == tag.name


def test_put_tag(client: FlaskClient, db: SQLAlchemy, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup) -> None:
    # test 404
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.put(tag_url)
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    data = {
        "name": "Updated",
        "description": "new description",
        "enabled": False,
        "constraints": {
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 9999
        },
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["enabled"] is False
    assert data["constraints"] == {Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 9999}
    assert data["id"] == tag.id

    data = {
        "name": "Updated-again",
        "description": "new description",
        "enabled": True,
        "constraints": {},
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == "Updated-again"
    assert data["description"] == "new description"
    assert data["enabled"] is True
    assert data["constraints"] == {}
    assert data["id"] == tag.id

    data = {
        "constraints": {
            "invalid_constraint": "asdfas"
        },
    }
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 400

    data = {
        "constraints": {
            "time_limit": "asdfas"
        },
    }
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 400


def test_delete_tag(client: FlaskClient, db: SQLAlchemy, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup) -> None:
    # test 404
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.delete(tag_url)
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    tag_id = tag.id
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag_id)
    rep = client.delete(tag_url)
    assert rep.status_code == 200
    assert db.session.get(Tag, tag_id).deleted_at is not None


def test_create_tag(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture) -> None:
    # test bad data
    tags_url = url_for("api-tags.tags")
    data: dict[str, Any] = {}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 400

    data = {"name": "Created", "description": ""}

    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    data = rep.get_json()
    tag = db.session.get(Tag, data["id"])

    assert tag.name == "Created"
    assert tag.description == ""
    assert tag.enabled is True
    assert tag.constraints == {}

    data = {"name": "Created2", "description": "", "enabled": False, "constraints": {}}

    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    data = rep.get_json()
    tag = db.session.get(Tag, data["id"])

    assert tag.name == "Created2"
    assert tag.description == ""
    assert tag.enabled is False
    assert tag.constraints == {}


def test_get_all_tag(client: FlaskClient, db: SQLAlchemy) -> None:
    tags_url = url_for("api-tags.tags")

    tags = TagFactory.create_batch(3)
    db.session.add_all(tags)
    db.session.commit()

    rep = client.get(tags_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for tag in tags:
        assert any(u["id"] == tag.id for u in results["results"])

    rep = client.get(tags_url, query_string={"q": "Tag-"})
    assert rep.status_code == 200

    results = rep.get_json()
    assert(len(results["results"]) == 3)
    for tag in tags:
        assert any(u["id"] == tag.id for u in results["results"])
