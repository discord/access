import {OktaUserDetail, AppGroupDetail, GroupDetail} from './api/apiSchemas';
import {appName} from './config/accessConfig';

export function isGroupOwner(currentUser: OktaUserDetail, groupId: string): boolean {
  const found = (currentUser.active_group_ownerships ?? []).find((ownership) => {
    return ownership.active_group?.id == groupId;
  });
  return found != null;
}

export function isAppOwnerGroupOwner(currentUser: OktaUserDetail, appId: string): boolean {
  const found = (currentUser.active_group_ownerships ?? []).find((ownership) => {
    if (ownership.active_group?.type == 'app_group') {
      const appGroup = ownership.active_group as AppGroupDetail;
      return appGroup.is_owner && appGroup.app?.id == appId;
    }
    return false;
  });
  return found != null;
}

export const ACCESS_APP_RESERVED_NAME = appName;

export function isAccessAdmin(currentUser: OktaUserDetail): boolean {
  const found = (currentUser.active_group_memberships ?? []).find((membership) => {
    if (membership.active_group?.type == 'app_group') {
      const appGroup = membership.active_group as AppGroupDetail;
      return appGroup.is_owner && appGroup.app?.name == ACCESS_APP_RESERVED_NAME;
    }
    return false;
  });
  return found != null;
}

// Helper combining all three methods above
export function canManageGroup(currentUser: OktaUserDetail, group: GroupDetail | undefined) {
  return (
    isAccessAdmin(currentUser) ||
    isGroupOwner(currentUser, group?.id ?? '') ||
    (group?.type == 'app_group' && isAppOwnerGroupOwner(currentUser, (group as AppGroupDetail).app?.id ?? ''))
  );
}
