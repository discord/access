"""
Setup script for the App Group Lifecycle Google Group Management Plugin.

This registers the plugin with Access via the setuptools entry_points mechanism.
"""

from setuptools import setup

setup(
    name="app_group_lifecycle_google",
    version="0.1.0",
    description="Plugin for managing Google groups linked to Access groups",
    author="Discord",
    packages=["app_group_lifecycle_google"],
    package_dir={"app_group_lifecycle_google": "."},
    install_requires=[
        "Flask",
        "SQLAlchemy",
        "google-api-python-client>=2.0.0",
        "google-auth>=2.0.0",
        "requests>=2.28.0",
    ],
    # Register the plugin with the app group lifecycle plugin system
    entry_points={
        "access_app_group_lifecycle": [
            "google_group_manager=app_group_lifecycle_google.plugin:google_group_manager_plugin",
        ],
    },
)
