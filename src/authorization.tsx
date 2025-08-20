import {OktaUser, AppGroup, PolymorphicGroup} from './api/apiSchemas';
import {appName} from './config/accessConfig';

export function isGroupOwner(currentUser: OktaUser, groupId: string): boolean {
  const found = (currentUser.active_group_ownerships ?? []).find((ownership) => {
    return ownership.active_group?.id == groupId;
  });
  return found != null;
}

export function isAppOwnerGroupOwner(currentUser: OktaUser, appId: string): boolean {
  const found = (currentUser.active_group_ownerships ?? []).find((ownership) => {
    if (ownership.active_group?.type == 'app_group') {
      const appGroup = ownership.active_group as AppGroup;
      return appGroup.is_owner && appGroup.app?.id == appId;
    }
    return false;
  });
  return found != null;
}

export const ACCESS_APP_RESERVED_NAME = appName;

export function isAccessAdmin(currentUser: OktaUser): boolean {
  const found = (currentUser.active_group_memberships ?? []).find((membership) => {
    if (membership.active_group?.type == 'app_group') {
      const appGroup = membership.active_group as AppGroup;
      return appGroup.is_owner && appGroup.app?.name == ACCESS_APP_RESERVED_NAME;
    }
    return false;
  });
  return found != null;
}

// Helper combining all three methods above
export function canManageGroup(currentUser: OktaUser, group: PolymorphicGroup | undefined) {
  return (
    isAccessAdmin(currentUser) ||
    isGroupOwner(currentUser, group?.id ?? '') ||
    (group?.type == 'app_group' && isAppOwnerGroupOwner(currentUser, (group as AppGroup).app?.id ?? ''))
  );
}
