import logging

from flask import request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from marshmallow import ValidationError

from api.apispec import FlaskApiSpecDecorators
from api.plugins.metrics_reporter import get_metrics_reporter_hook
from api.views.schemas import MetricsSchema

logger = logging.getLogger(__name__)


class MetricsResource(MethodResource):
    @FlaskApiSpecDecorators.request_schema(MetricsSchema)
    def post(self) -> ResponseReturnValue:
        try:
            metrics_data = MetricsSchema().load(request.get_json())
        except ValidationError as e:
            return {"errors": e.messages}, 400

        metric_type = metrics_data["type"]
        data = metrics_data["data"]

        metrics_hook = get_metrics_reporter_hook()

        tags = {"source": "frontend", "metric_type": metric_type}

        if "tags" in data:
            tags.update(data["tags"])

        metric_name = data.get("name", f"frontend.{metric_type}")

        if metric_type == "counter":
            value = data.get("value", 1.0)
            metrics_hook.record_counter(metric_name=metric_name, value=value, tags=tags)

        elif metric_type == "gauge":
            value = data.get("value", 0.0)
            metrics_hook.record_gauge(metric_name=metric_name, value=value, tags=tags)

        elif metric_type == "histogram":
            value = data.get("value", 0.0)
            buckets = data.get("buckets", None)
            metrics_hook.record_histogram(metric_name=metric_name, value=value, tags=tags, buckets=buckets)

        elif metric_type == "timing":
            # Special handling for timing metrics
            value = data.get("duration", 0.0)
            metrics_hook.record_histogram(
                metric_name=f"{metric_name}.duration_ms", value=value, tags={**tags, "unit": "ms"}
            )

        else:
            return {"error": f"Unknown metric type: {metric_type}"}, 400

        logger.info(f"Recorded {metric_type} metric: {metric_name} = {value} with tags {tags}")

        return "", 200
