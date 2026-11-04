from setuptools import setup, find_packages

setup(
    name="access-plugins",
    install_requires=["pluggy>=1.7.0,<2"],
    packages=find_packages(),
)
