from api.models import AppGroup, RoleGroup

# ruleid: group-name-literal
ROLE_PREFIX = "Role-"
# ruleid: group-name-literal
OWNERS_SUFFIX = "Owners"


def app_group_prefix(app):
    # ruleid: group-name-literal
    return f"App-{app.name}-"


def owners_group_name(app):
    # ruleid: group-name-literal
    return f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}-Owners"


def is_role(name):
    # ruleid: group-name-literal
    return name.startswith("Role-")


def app_group_prefix_from_constants(app):
    # ok: group-name-literal
    return f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"


def is_role_from_constants(name):
    # ok: group-name-literal
    return name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX)


def owners_description(app_name):
    # ok: group-name-literal
    return f"Owners of the {app_name} application"


# ok: group-name-literal
APPLE = "Apple-Pie"
