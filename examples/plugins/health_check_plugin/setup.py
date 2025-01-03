from setuptools import setup

setup(
    name="health_check_plugin",
    version="0.1.0",
    packages=["health_check_plugin"],
    package_dir={"health_check_plugin": "."},  # Map package to current directory
    install_requires=[
        "Flask",
    ],
    entry_points={
        "flask.commands": [
            "health=health_check_plugin.cli:health_command",
        ],
    },
)
