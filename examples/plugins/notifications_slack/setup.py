from setuptools import setup

setup(
    name="access-notifications",
    install_requires=["pluggy==1.4.0"],
    py_modules=["notifications"],
    entry_points={
        "access_notifications": ["notifications = notifications"],
    },
)
