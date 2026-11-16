from setuptools import find_packages, setup

setup(
    name="access-plugins",
    install_requires=["pluggy>=1.7.0,<2"],
    packages=find_packages(),
)
