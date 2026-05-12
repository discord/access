from api.config import Settings


def test_app_creator_ids() -> None:
    assert Settings(APP_CREATOR_ID=None).app_creator_ids == []
    assert Settings(APP_CREATOR_ID="test1").app_creator_ids == ["test1"]
    assert Settings(APP_CREATOR_ID="test1,test2").app_creator_ids == ["test1", "test2"]
