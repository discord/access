from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import GroupAuditResource, GroupList, GroupMemberResource, GroupResource

bp_name = "api-groups"
bp_url_prefix = "/api/groups"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(GroupResource, "/<string:group_id>", endpoint="group_by_id")
api.add_resource(
    GroupAuditResource, "/<string:group_id>/audit", endpoint="group_audit_by_id"
)
api.add_resource(
    GroupMemberResource, "/<string:group_id>/members", endpoint="group_members_by_id"
)
api.add_resource(GroupList, "", endpoint="groups")


def register_docs() -> None:
    docs.register(GroupResource, blueprint=bp_name, endpoint="group_by_id")
    docs.register(
        GroupMemberResource, blueprint=bp_name, endpoint="group_members_by_id"
    )
    docs.register(GroupList, blueprint=bp_name, endpoint="groups")
