"""
Tests for REQUIRE_DESCRIPTIONS=True configuration.

IMPORTANT: These tests should be run with the REQUIRE_DESCRIPTIONS environment
variable set to 'true' before pytest starts. Use the dedicated tox environment:
    tox -e test-require-descriptions

This tests the production scenario where the env var is set at application startup.
"""
from typing import Any, Protocol, cast

from faker import Faker
from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from okta.models.group import Group
from pytest_mock import MockerFixture

from api.models import App, Tag
from api.services import okta


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


def test_create_app_requires_description(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    """Test that apps require descriptions when REQUIRE_DESCRIPTIONS=True"""
    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    apps_url = url_for("api-apps.apps")

    # Test creating app without description should fail when REQUIRE_DESCRIPTIONS=True
    data: dict[str, Any] = {"name": "TestAppNoDesc"}
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "required" in str(response_data).lower()

    # Test creating app with empty description should fail
    data = {"name": "TestAppEmptyDesc", "description": ""}
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    # Empty description fails length validation
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test creating app with a description should succeed
    data = {"name": "TestAppWithDesc", "description": "This has a description"}
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201

    result = rep.get_json()
    assert result["name"] == "TestAppWithDesc"
    assert result["description"] == "This has a description"


def test_create_group_requires_description(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    access_app: App,
) -> None:
    """Test that groups require descriptions when REQUIRE_DESCRIPTIONS=True"""
    db.session.add(access_app)
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    groups_url = url_for("api-groups.groups")

    # Test creating okta_group without description should fail when REQUIRE_DESCRIPTIONS=True
    data: dict[str, Any] = {"type": "okta_group", "name": "TestGroupNoDesc"}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "required" in str(response_data).lower()

    # Test creating group with empty description should fail
    data = {"type": "okta_group", "name": "TestGroupEmptyDesc", "description": ""}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    # Empty description fails length validation
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test creating groups with descriptions should succeed
    data = {"type": "role_group", "name": "Role-TestGroupWithDesc", "description": "This has a description"}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201

    result = rep.get_json()
    assert result["name"] == "Role-TestGroupWithDesc"
    assert result["description"] == "This has a description"


def test_create_tag_requires_description(
    client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture
) -> None:
    """Test that tags require descriptions when REQUIRE_DESCRIPTIONS=True"""
    tags_url = url_for("api-tags.tags")

    # Test creating tag without description should fail when REQUIRE_DESCRIPTIONS=True
    data: dict[str, Any] = {"name": "TestTag"}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "required" in str(response_data).lower()

    # Test creating tag with empty description should fail
    data = {"name": "TestTag2", "description": ""}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    # Empty description fails length validation
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test creating tag with a description should succeed
    data = {"name": "TestTag3", "description": "This has a description"}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    result = rep.get_json()
    tag = db.session.get(Tag, result["id"])
    assert tag.name == "TestTag3"
    assert tag.description == "This has a description"


def test_update_app_requires_description(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    """Test that app updates require descriptions when REQUIRE_DESCRIPTIONS=True"""
    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    update_group_spy = mocker.patch.object(okta, "update_group")

    apps_url = url_for("api-apps.apps")

    # First create an app with a description
    data: dict[str, Any] = {"name": "TestApp", "description": "Original description"}
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201
    app_id = rep.get_json()["id"]

    app_url = url_for("api-apps.app_by_id", app_id=app_id)

    # Test updating with empty description should fail
    data = {"name": "UpdatedApp", "description": ""}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test partial update without description should preserve existing description
    data = {"name": "UpdatedApp"}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedApp"
    assert result["description"] == "Original description"

    # Test updating with valid description should succeed
    data = {"name": "UpdatedApp2", "description": "New description"}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedApp2"
    assert result["description"] == "New description"


def test_update_group_requires_description(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    access_app: App,
) -> None:
    """Test that group updates require descriptions when REQUIRE_DESCRIPTIONS=True"""
    db.session.add(access_app)
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    update_group_spy = mocker.patch.object(okta, "update_group")

    groups_url = url_for("api-groups.groups")

    # First create a group with a description
    data: dict[str, Any] = {"type": "okta_group", "name": "TestGroup", "description": "Original description"}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201
    group_id = rep.get_json()["id"]

    group_url = url_for("api-groups.group_by_id", group_id=group_id)

    # Test updating with empty description should fail
    data = {"type": "okta_group", "name": "UpdatedGroup", "description": ""}
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test partial update without description should preserve existing description
    data = {"type": "okta_group", "name": "UpdatedGroup"}
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedGroup"
    assert result["description"] == "Original description"

    # Test updating with valid description should succeed
    data = {"type": "okta_group", "name": "UpdatedGroup2", "description": "New description"}
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedGroup2"
    assert result["description"] == "New description"


def test_update_tag_requires_description(
    client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture
) -> None:
    """Test that tag updates require descriptions when REQUIRE_DESCRIPTIONS=True"""
    tags_url = url_for("api-tags.tags")

    # First create a tag with a description
    data: dict[str, Any] = {"name": "TestTag", "description": "Original description"}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201
    tag_id = rep.get_json()["id"]

    tag_url = url_for("api-tags.tag_by_id", tag_id=tag_id)

    # Test updating with empty description should fail
    data = {"name": "UpdatedTag", "description": ""}
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 400
    response_data = rep.get_json()
    assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()

    # Test partial update without description should preserve existing description
    data = {"name": "UpdatedTag"}
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedTag"
    assert result["description"] == "Original description"

    # Test updating with valid description should succeed
    data = {"name": "UpdatedTag2", "description": "New description"}
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200
    result = rep.get_json()
    assert result["name"] == "UpdatedTag2"
    assert result["description"] == "New description"
