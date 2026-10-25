from setuptools import find_packages, setup

setup(
    name="access-plugins",
    install_requires=["pluggy>=1.6.0,<2"],
    packages=find_packages(),
)
