"""
Setup script for the App Group Lifecycle Audit Logger Plugin.

This registers the plugin with Access via the setuptools entry_points mechanism.
"""

from setuptools import setup

setup(
    name="app_group_lifecycle_audit_logger",
    version="0.1.0",
    description="Example app group lifecycle plugin that logs all group events",
    author="Discord",
    packages=["app_group_lifecycle_audit_logger"],
    package_dir={"app_group_lifecycle_audit_logger": "."},
    install_requires=[
        "Flask",
        "SQLAlchemy",
    ],
    # Register the plugin with the app group lifecycle plugin system
    entry_points={
        "access_app_group_lifecycle": [
            "audit_logger=app_group_lifecycle_audit_logger.plugin:audit_logger_plugin",
        ],
    },
)
