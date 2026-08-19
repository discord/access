from setuptools import setup

setup(
    name="access-metrics",
    # datadog is pinned to the version the (now removed) requirements.txt
    # installed, so the image keeps getting the exact version it did before.
    install_requires=["pluggy>=1.5,<2", "datadog==0.49.0"],
    py_modules=["metrics_reporter"],
    entry_points={
        "access_metrics_reporter": ["metrics_reporter = metrics_reporter:datadog_metrics_plugin"],
    },
)
