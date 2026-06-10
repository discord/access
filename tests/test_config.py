from api.config import Settings


def test_app_creator_ids() -> None:
    assert Settings(APP_CREATOR_ID=None).app_creator_ids == []
    assert Settings(APP_CREATOR_ID="test1").app_creator_ids == ["test1"]
    assert Settings(APP_CREATOR_ID="test1,test2").app_creator_ids == ["test1", "test2"]


def test_app_group_deleter_ids() -> None:
    assert Settings(APP_GROUP_DELETER_ID=None).app_group_deleter_ids == []
    assert Settings(APP_GROUP_DELETER_ID="test1").app_group_deleter_ids == ["test1"]
    assert Settings(APP_GROUP_DELETER_ID="test1,test2").app_group_deleter_ids == ["test1", "test2"]


def test_trusted_hosts() -> None:
    assert Settings(ALLOWED_HOSTS="").trusted_hosts == []
    assert Settings(ALLOWED_HOSTS="access.example.com").trusted_hosts == ["access.example.com"]
    # Comma-separated, surrounding whitespace stripped, blanks dropped.
    assert Settings(ALLOWED_HOSTS=" a.example.com , *.example.com ,").trusted_hosts == [
        "a.example.com",
        "*.example.com",
    ]
