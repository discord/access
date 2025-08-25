from setuptools import setup

setup(
    name="access-conditional-access",
    install_requires=["pluggy==1.5.0"],
    py_modules=["conditional_access"],
    entry_points={
        "access_conditional_access": ["conditional_access = conditional_access"],
    },
)
