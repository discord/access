from setuptools import find_packages, setup

__version__ = "2.0"

setup(
    name="access",
    version=__version__,
    packages=find_packages(exclude=["tests"]),
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
        "python-dotenv",
        "click",
    ],
    entry_points={
        "console_scripts": [
            "access = api.manage:cli",
        ],
    },
)
