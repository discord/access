import time
from typing import Optional

from flask_sqlalchemy import SQLAlchemy
from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.models.group_rule_conditions import GroupRuleConditions as OktaGroupRuleConditionsType
from okta.models.group_rule_expression import GroupRuleExpression as OktaGroupRuleExpressionType
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from api.models import OktaGroup
from api.services import okta
from api.services.okta_service import Group
from api.syncer import sync_groups
from tests.factories import GroupFactory


def test_group_sync_no_changes(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_groups_in_okta = GroupFactory.create_batch(3)

    initial_db_groups = seed_db(db, initial_groups_in_okta)

    initial_groups_in_okta.insert(0, GroupFactory.create_access_owner_group())

    new_db_groups = run_sync(db, mocker, initial_groups_in_okta, act_as_authority=True)

    for i in range(len(initial_groups_in_okta)):
        assert okta_groups_are_equal(
            get_group_by_id(initial_db_groups, initial_groups_in_okta[i].id),
            get_group_by_id(new_db_groups, initial_groups_in_okta[i].id),
        )


def test_group_sync_missing_group_in_okta(db: SQLAlchemy, mocker: MockerFixture) -> None:
    groups_in_okta = GroupFactory.create_batch(3)

    initial_db_groups = seed_db(db, groups_in_okta)

    groups_in_okta.insert(0, GroupFactory.create_access_owner_group())

    removed_group = groups_in_okta.pop()

    # Verify that our chosen group was not marked as deleted
    removed_group_db_entry = get_group_by_id(initial_db_groups, removed_group.id)
    assert removed_group_db_entry is not None
    assert removed_group_db_entry.deleted_at is None

    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=False)

    # Verify that our chosen group was not removed from the db, but is deleted
    assert len(new_db_groups) == len(initial_db_groups)
    removed_group_db_entry = get_group_by_id(new_db_groups, removed_group.id)
    assert removed_group_db_entry is not None
    deleted_at = removed_group_db_entry.deleted_at
    assert deleted_at is not None

    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=True)

    # Verify that being an authority does not change this
    # and that it also doesn't update the group deleted_at
    assert len(new_db_groups) == len(initial_db_groups)
    removed_group_db_entry = get_group_by_id(new_db_groups, removed_group.id)
    assert removed_group_db_entry is not None
    assert deleted_at == removed_group_db_entry.deleted_at


def test_group_sync_missing_group_in_db(db: SQLAlchemy, mocker: MockerFixture) -> None:
    groups_in_okta = GroupFactory.create_batch(3)

    initial_db_groups = seed_db(db, groups_in_okta)

    groups_in_okta.insert(0, GroupFactory.create_access_owner_group())

    new_group_in_okta = GroupFactory.create()
    groups_in_okta.append(new_group_in_okta)

    delete_group_spy = mocker.patch.object(okta, "delete_group")
    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=True)

    # Since we're authority, and the group isn't in the DB, then it shouldn't exist in okta.
    assert delete_group_spy.call_count == 1
    assert len(new_db_groups) == len(initial_db_groups)
    assert get_group_by_id(new_db_groups, new_group_in_okta.id) is None

    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=False)

    assert len(new_db_groups) == len(initial_db_groups) + 1
    assert get_group_by_id(new_db_groups, new_group_in_okta.id) is not None


def test_group_sync_deleted_in_db_exists_in_okta(db: SQLAlchemy, mocker: MockerFixture) -> None:
    groups_in_okta = GroupFactory.create_batch(3)

    seed_db(db, groups_in_okta)

    groups_in_okta.insert(0, GroupFactory.create_access_owner_group())

    # Remove a group and then sync.
    removed_group = groups_in_okta.pop()
    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=True)

    removed_group_entry = get_group_by_id(new_db_groups, removed_group.id)
    assert removed_group_entry is not None
    deleted_at = removed_group_entry.deleted_at
    assert deleted_at is not None

    # Wait a second to make sure the deleted_at timestamp is different
    time.sleep(1)

    # Add the group back to okta to simulate a case where the value was updated in the DB
    # but somehow didn't get deleted from okta
    groups_in_okta.append(removed_group)
    delete_group_spy = mocker.patch.object(okta, "async_delete_group")
    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=True)

    assert delete_group_spy.call_count == 1
    removed_group_entry = get_group_by_id(new_db_groups, removed_group.id)
    assert removed_group_entry is not None
    assert removed_group_entry.deleted_at is not None
    assert removed_group_entry.deleted_at != deleted_at

    # Run the sync again but not as an authority this time. The group should be resurrected
    new_db_groups = run_sync(db, mocker, groups_in_okta, act_as_authority=False)

    removed_group_entry = get_group_by_id(new_db_groups, removed_group.id)
    assert removed_group_entry is not None
    assert removed_group_entry.deleted_at is None


def test_group_sync_externally_managed_group_in_okta(db: SQLAlchemy, mocker: MockerFixture) -> None:
    groups_in_okta = GroupFactory.create_batch(3)

    seed_db(db, groups_in_okta)

    groups_in_okta.insert(0, GroupFactory.create_access_owner_group())

    # Create an externally managed group and add it to Okta
    externally_managed_group = GroupFactory.create()
    externally_managed_group.is_managed = False
    groups_in_okta.append(externally_managed_group)

    with Session(db.engine) as session:
        # Create a rule for the externally managed group
        test_rule_expression = OktaGroupRuleExpressionType()
        test_rule_expression.value = "user.department equals \"Test\""
        test_rule_conditions = OktaGroupRuleConditionsType()
        test_rule_conditions.expression = test_rule_expression
        test_rule = OktaGroupRuleType()
        test_rule.name = 'Test'
        test_rule.conditions = test_rule_conditions
        mocker.patch.object(okta, "list_groups_with_active_rules",
                            return_value={externally_managed_group.id : [test_rule]})
        mocker.patch.object(
            okta, "list_groups", return_value=[Group(g) for g in groups_in_okta]
        )
        sync_groups(False)
        new_db_groups = session.query(OktaGroup).all()

    # Ensure that externally managed group and rule are added to db
    external_group_entry = get_group_by_id(new_db_groups, externally_managed_group.id)
    assert external_group_entry is not None
    assert external_group_entry.is_managed is False
    assert external_group_entry.externally_managed_data == {'Test' : "user.department equals \"Test\""}


def seed_db(db: SQLAlchemy, groups: list[OktaGroup]) -> list[OktaGroup]:
    with Session(db.engine) as session:
        session.add_all(
            [Group(g).update_okta_group(OktaGroup(), {}) for g in groups]
        )
        session.commit()
        return session.query(OktaGroup).all()


def run_sync(
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_groups: list[OktaGroup],
    act_as_authority: bool
) -> list[OktaGroup]:
    with Session(db.engine) as session:
        mocker.patch.object(okta, "list_groups_with_active_rules", return_value={})
        mocker.patch.object(
            okta, "list_groups", return_value=[Group(g) for g in okta_groups]
        )
        sync_groups(act_as_authority)
        return session.query(OktaGroup).all()


def get_group_by_id(group_list: list[OktaGroup], group_id: str) -> Optional[OktaGroup]:
    ret = None
    for x in group_list:
        if x.id == group_id:
            ret = x
            break

    return ret


def okta_groups_are_equal(left: Optional[OktaGroup], right: Optional[OktaGroup]) -> bool:
    # Checks if there are property differences between
    # two OktaUser objects without implementing it on the model
    # itself.
    return (
        left is not None
        and right is not None
        and left.id == right.id
        and left.name == right.name
        and left.description == right.description
        and left.created_at == right.created_at
        and left.updated_at == right.updated_at
        and left.deleted_at == right.deleted_at
    )
