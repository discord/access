import pytest
from pytest_mock import MockerFixture

from api.extensions import Db
from api.models import AppGroup
from api.models.app_group import (
    app_owners_group_description,
    app_owners_group_description_remainder,
)
from api.operations import ModifyGroupDetails
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory


# Pinned literal. The frontend mirrors this format string in
# `appOwnerGroupDescriptionPrefix` (src/pages/groups/appOwnerGroupDescription.ts);
# if you change the wording here, change it there and update both tests.
def test_base_line_literal_is_pinned() -> None:
    assert app_owners_group_description("Zendesk") == "Owners of the Zendesk application"


def test_composes_additional_text_after_a_blank_line() -> None:
    assert app_owners_group_description("Zendesk", "Also grants billing access") == (
        "Owners of the Zendesk application\n\nAlso grants billing access"
    )


def test_treats_empty_and_whitespace_additional_text_as_absent() -> None:
    base = "Owners of the Zendesk application"
    assert app_owners_group_description("Zendesk", None) == base
    assert app_owners_group_description("Zendesk", "") == base
    assert app_owners_group_description("Zendesk", "   \n  ") == base


def test_remainder_is_the_text_after_a_matching_base_line() -> None:
    assert (
        app_owners_group_description_remainder(
            "Owners of the Zendesk application\n\nAlso grants billing access", "Zendesk"
        )
        == "Also grants billing access"
    )


def test_remainder_is_empty_for_a_bare_base_line() -> None:
    assert app_owners_group_description_remainder("Owners of the Zendesk application", "Zendesk") == ""


def test_remainder_is_the_whole_description_when_the_base_line_does_not_match() -> None:
    # The reseat rule: divergent text is preserved as remainder rather than dropped.
    assert app_owners_group_description_remainder("Legacy hand-written text", "Zendesk") == ("Legacy hand-written text")
    assert app_owners_group_description_remainder("Owners of the Jira application", "Zendesk") == (
        "Owners of the Jira application"
    )


def test_remainder_normalises_crlf() -> None:
    assert (
        app_owners_group_description_remainder("Owners of the Zendesk application\r\n\r\nExtra", "Zendesk") == "Extra"
    )


def test_remainder_of_an_empty_description_is_empty() -> None:
    assert app_owners_group_description_remainder("", "Zendesk") == ""


def test_a_similarly_named_app_is_not_a_false_match() -> None:
    # "Owners of the Foo application" must not be seen as a prefix of the FooBar line.
    assert app_owners_group_description_remainder("Owners of the FooBar application", "Foo") == (
        "Owners of the FooBar application"
    )


def test_compose_and_split_round_trip() -> None:
    composed = app_owners_group_description("Zendesk", "Also grants billing access")
    assert app_owners_group_description_remainder(composed, "Zendesk") == "Also grants billing access"


def test_remainder_preserves_indentation_on_a_conforming_description() -> None:
    # The remainder is markdown; leading spaces on its first content line are a nested list
    # item, not incidental whitespace to discard.
    description = "Owners of the Zendesk application\n\n    - nested item\n    - another item"
    assert app_owners_group_description_remainder(description, "Zendesk") == ("    - nested item\n    - another item")


def test_remainder_preserves_indentation_on_a_non_conforming_description() -> None:
    # Same principle for the whole-description (non-matching-base-line) branch.
    description = "    - nested item\n    - another item"
    assert app_owners_group_description_remainder(description, "Zendesk") == ("    - nested item\n    - another item")


def test_compose_and_split_round_trip_preserves_indentation() -> None:
    composed = app_owners_group_description("Zendesk", "    - nested item\n    - another item")
    assert app_owners_group_description_remainder(composed, "Zendesk") == "    - nested item\n    - another item"
    assert (
        app_owners_group_description("Zendesk", app_owners_group_description_remainder(composed, "Zendesk")) == composed
    )


def test_composing_after_a_crlf_normalises_the_separator() -> None:
    # A CRLF blank-line separator (e.g. from a browser textarea) must not survive verbatim --
    # otherwise compose -> remainder -> compose is not stable for that input.
    assert app_owners_group_description("Zendesk", "\r\n\r\nExtra text") == (
        "Owners of the Zendesk application\n\nExtra text"
    )


async def _owner_group(db: Db, app_name: str = "Zendesk") -> AppGroup:
    """Create an app and its owner group with a conforming description, mirroring what CreateApp produces."""
    app = await AppFactory.create_async(name=app_name, description="")
    return await AppGroupFactory.create_async(
        app_id=app.id,
        is_owner=True,
        name=(
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}"
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ),
        description=app_owners_group_description(app_name),
    )


async def test_accepts_the_bare_base_line(db: Db, mocker: MockerFixture) -> None:
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    await ModifyGroupDetails(group=group, description="Owners of the Zendesk application").execute()
    assert group.description == "Owners of the Zendesk application"


async def test_accepts_the_base_line_followed_by_a_blank_line_and_text(db: Db, mocker: MockerFixture) -> None:
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    await ModifyGroupDetails(
        group=group, description="Owners of the Zendesk application\n\nAlso grants billing"
    ).execute()
    assert group.description == "Owners of the Zendesk application\n\nAlso grants billing"


async def test_normalises_crlf_before_checking(db: Db, mocker: MockerFixture) -> None:
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    await ModifyGroupDetails(
        group=group, description="Owners of the Zendesk application\r\n\r\nAlso grants billing"
    ).execute()
    assert group.description == "Owners of the Zendesk application\n\nAlso grants billing"


async def test_rejects_a_description_that_drops_the_base_line(db: Db, mocker: MockerFixture) -> None:
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    with pytest.raises(ValueError, match="Owners of the Zendesk application"):
        await ModifyGroupDetails(group=group, description="Something else entirely").execute()


async def test_rejects_text_appended_on_the_same_line(db: Db, mocker: MockerFixture) -> None:
    # One paragraph is not the requested format; it must be a blank line.
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    with pytest.raises(ValueError):
        await ModifyGroupDetails(
            group=group, description="Owners of the Zendesk application and also billing"
        ).execute()


async def test_rejects_a_single_newline_separator(db: Db, mocker: MockerFixture) -> None:
    group = await _owner_group(db)
    mocker.patch.object(okta, "update_group")

    with pytest.raises(ValueError):
        await ModifyGroupDetails(
            group=group, description="Owners of the Zendesk application\nAlso grants billing"
        ).execute()


async def test_a_non_owner_app_group_description_is_unconstrained(db: Db, mocker: MockerFixture) -> None:
    app = await AppFactory.create_async(name="Zendesk", description="")
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False, description="anything")
    mocker.patch.object(okta, "update_group")

    await ModifyGroupDetails(group=group, description="literally anything").execute()
    assert group.description == "literally anything"
