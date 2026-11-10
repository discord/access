from api.operations import _lifecycle_fan_out
from api.operations._lifecycle_fan_out import defer_or_invoke_lifecycle_hook
from api.plugins.app_group_lifecycle import AppGroupLifecycleHook, invoke_app_group_lifecycle_hook


async def delete_group(group, session):
    # ruleid: group-deleted-deferred
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_DELETED, group=group)
    # ruleid: group-deleted-deferred
    await defer_or_invoke_lifecycle_hook(hook_method=AppGroupLifecycleHook.GROUP_DELETED, group=group)
    # ruleid: group-deleted-deferred
    await _lifecycle_fan_out.defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_DELETED, group=group)
    # ruleid: group-deleted-deferred
    await defer_or_invoke_lifecycle_hook("group_deleted", group=group)

    # ok: group-deleted-deferred
    await invoke_app_group_lifecycle_hook(AppGroupLifecycleHook.GROUP_DELETED, session=session, group=group)
    # ok: group-deleted-deferred
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)
