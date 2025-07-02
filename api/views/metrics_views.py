from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources.metrics import MetricsResource

bp_name = "api-metrics"
bp_url_prefix = "/api/metrics"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(MetricsResource, "", endpoint="metrics")


def register_docs() -> None:
    docs.register(MetricsResource, blueprint=bp_name, endpoint="metrics")
