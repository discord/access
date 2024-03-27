from flask import Blueprint
from flask_restful import Api

from api.views.resources import OktaWebhookResource

bp_name = "api-webhooks"
bp_url_prefix = "/api/webhooks"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(OktaWebhookResource, "/okta", endpoint="okta_webhook")
