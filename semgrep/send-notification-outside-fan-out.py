from api import plugins
from api.operations._fan_out import defer_notification
from api.plugins import NotificationHook, send_notification


async def notify_created(request, approvers):
    # ruleid: send-notification-outside-fan-out
    await send_notification(NotificationHook.ACCESS_REQUEST_CREATED, access_request=request, approvers=approvers)

    # ruleid: send-notification-outside-fan-out
    await plugins.send_notification(NotificationHook.ACCESS_REQUEST_CREATED, access_request=request)

    # ok: send-notification-outside-fan-out
    defer_notification(NotificationHook.ACCESS_REQUEST_CREATED, access_request=request, approvers=approvers)
