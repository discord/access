from setuptools import setup

setup(
    name="access-notifications",
    # Every runtime dep is declared here, so a plain `uv pip install
    # ./notifications_slack` yields a working plugin. slack_sdk's AsyncWebClient
    # imports aiohttp, which slack-sdk does not depend on itself, so aiohttp has
    # to be named explicitly or the hook fails at import time.
    install_requires=["pluggy==1.5.0", "slack-sdk==3.27.2", "aiohttp==3.14.3"],
    py_modules=["notifications"],
    entry_points={
        "access_notifications": ["notifications = notifications"],
    },
)
