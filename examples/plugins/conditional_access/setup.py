from setuptools import setup

setup(
    name="access-conditional-access",
    # A range, not an exact pin: pluggy is supplied by the app, and plugins
    # install into the app's venv, so an exact pin here would silently
    # re-resolve the app's locked pluggy.
    install_requires=["pluggy>=1.5,<2"],
    py_modules=["conditional_access"],
    entry_points={
        "access_conditional_access": ["conditional_access = conditional_access"],
    },
)
