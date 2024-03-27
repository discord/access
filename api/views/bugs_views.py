from flask import Blueprint

from api.extensions import Api
from api.views.resources import SentryProxyResource

bp_name = "api-bugs"
bp_url_prefix = "/api/bugs"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(SentryProxyResource, "/sentry", endpoint="sentry_bug")
