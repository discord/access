from setuptools import setup

setup(
    name="access-prometheus-metrics",
    install_requires=["pluggy==1.4.0", "prometheus_client>=0.17.0"],
    py_modules=["metrics_reporter"],
    entry_points={
        "access_metrics_reporter": ["metrics_reporter = metrics_reporter"],
    },
)
