from api.config import Settings


def test_app_creator_ids() -> None:
    assert Settings(APP_CREATOR_ID=None).app_creator_ids == []
    assert Settings(APP_CREATOR_ID="test1").app_creator_ids == ["test1"]
    assert Settings(APP_CREATOR_ID="test1,test2").app_creator_ids == ["test1", "test2"]


def test_app_group_deleter_ids() -> None:
    assert Settings(APP_GROUP_DELETER_ID=None).app_group_deleter_ids == []
    assert Settings(APP_GROUP_DELETER_ID="test1").app_group_deleter_ids == ["test1"]
    assert Settings(APP_GROUP_DELETER_ID="test1,test2").app_group_deleter_ids == ["test1", "test2"]
