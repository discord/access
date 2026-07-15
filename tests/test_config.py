from api.config import Settings


def test_app_creator_ids() -> None:
    assert Settings(APP_CREATOR_ID=None).app_creator_ids == []
    assert Settings(APP_CREATOR_ID="test1").app_creator_ids == ["test1"]
    assert Settings(APP_CREATOR_ID="test1,test2").app_creator_ids == ["test1", "test2"]


def test_app_group_deleter_ids() -> None:
    assert Settings(APP_GROUP_DELETER_ID=None).app_group_deleter_ids == []
    assert Settings(APP_GROUP_DELETER_ID="test1").app_group_deleter_ids == ["test1"]
    assert Settings(APP_GROUP_DELETER_ID="test1,test2").app_group_deleter_ids == ["test1", "test2"]


def test_expose_api_docs() -> None:
    # Always exposed in development, regardless of the toggle.
    assert Settings(ENV="development", ENABLE_API_DOCS=False).expose_api_docs is True
    # Off by default outside development.
    assert Settings(ENV="staging").expose_api_docs is False
    assert Settings(ENV="production").expose_api_docs is False
    # The toggle opts staging/production in.
    assert Settings(ENV="staging", ENABLE_API_DOCS=True).expose_api_docs is True
    assert Settings(ENV="production", ENABLE_API_DOCS=True).expose_api_docs is True


def test_trusted_hosts() -> None:
    assert Settings(ALLOWED_HOSTS="").trusted_hosts == []
    assert Settings(ALLOWED_HOSTS="access.example.com").trusted_hosts == ["access.example.com"]
    # Comma-separated, surrounding whitespace stripped, blanks dropped.
    assert Settings(ALLOWED_HOSTS=" a.example.com , *.example.com ,").trusted_hosts == [
        "a.example.com",
        "*.example.com",
    ]
