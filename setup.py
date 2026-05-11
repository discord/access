from setuptools import find_packages, setup

__version__ = "2.0"

setup(
    name="access",
    version=__version__,
    packages=find_packages(exclude=["tests"]),
    # Bundle config/*.json with the wheel so `pip install .` produces a
    # self-contained install. Without this the `access` console script
    # fails at import time with ConfigFileNotFoundError because the
    # default config lookup is relative to the install location of
    # api/access_config.py (site-packages), not the source tree.
    package_data={"config": ["*.json"]},
    install_requires=[
        "fastapi",
        "uvicorn[standard]",
        "pydantic>=2.7",
        "pydantic-settings",
        "sqlalchemy",
        "alembic",
        "httpx",
        "PyJWT[crypto]",
        "cachetools",
        "authlib",
        "itsdangerous",
        "click",
    ],
    entry_points={
        "console_scripts": [
            "access = api.manage:cli",
        ],
    },
)
