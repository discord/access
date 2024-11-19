from setuptools import setup

setup(
    name="access-notifications",
    install_requires=[
        "pluggy==1.5.0",
        "slack_sdk==3.33.3",
    ],
    py_modules=["notifications"],
    entry_points={
        "access_notifications": ["notifications = notifications"],
    },
)
