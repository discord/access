from api.models.app_group import (
    app_owners_group_description,
    app_owners_group_description_remainder,
)


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
