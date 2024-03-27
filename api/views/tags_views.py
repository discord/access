from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import TagList, TagResource

bp_name = "api-tags"
bp_url_prefix = "/api/tags"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(TagResource, "/<string:tag_id>", endpoint="tag_by_id")
api.add_resource(TagList, "", endpoint="tags")


def register_docs() -> None:
    docs.register(TagResource, blueprint=bp_name, endpoint="tag_by_id")
    docs.register(TagList, blueprint=bp_name, endpoint="tags")
