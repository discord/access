from setuptools import setup

setup(
    name="access-metrics",
    install_requires=["pluggy==1.4.0"],
    py_modules=["metrics_reporter"],
    entry_points={
        "access_metrics_reporter": ["metrics_reporter = metrics_reporter"],
    },
)
