from setuptools import setup

# Distribution name, module name, and entry-point name all stay distinct from
# the `notifications` example: both register in the `access_notifications`
# group, and the Dockerfile exposes them as independent build args, so an image
# can enable both. Sharing any of those names makes the second install silently
# overwrite the first instead of registering alongside it.
setup(
    name="access-notifications-slack",
    # Every runtime dep is declared here, so a plain `uv pip install
    # ./notifications_slack` yields a working plugin. slack_sdk's AsyncWebClient
    # imports aiohttp, which slack-sdk does not depend on itself, so aiohttp has
    # to be named explicitly or the hook fails at import time. pluggy stays a
    # range because the app supplies it.
    install_requires=["pluggy>=1.5,<2", "slack-sdk==3.27.2", "aiohttp==3.14.3"],
    py_modules=["notifications_slack"],
    entry_points={
        "access_notifications": ["notifications_slack = notifications_slack"],
    },
)
