import {OktaUserGroupMember, PolymorphicGroup, RoleGroup} from '../api/apiSchemas';
import {useNavigate} from 'react-router-dom';
import {useCurrentUser} from '../authentication';
import {canManageGroup, isGroupOwner} from '../authorization';
import Chip from '@mui/material/Chip';

export interface MembershipChipProps {
  user: OktaUserGroupMember;
  group: PolymorphicGroup;
  removeRoleGroup: (roleGroup: RoleGroup) => void;
  removeDirectAccessAsUser: () => void;
  removeDirectAccessAsGroupManager: () => void;
}

export default function MembershipChip({
  user,
  group,
  removeRoleGroup,
  removeDirectAccessAsUser,
  removeDirectAccessAsGroupManager,
}: MembershipChipProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();
  const activeRoleGroup = user.active_role_group_mapping?.active_role_group;
  const canManageUserRoleGroup = activeRoleGroup?.id ? isGroupOwner(currentUser, activeRoleGroup.id) : false;
  const canManageThisGroup = group.is_managed && canManageGroup(currentUser, group);
  const canManageThisUser = group.is_managed && currentUser.id === user.active_user?.id;
  return activeRoleGroup ? (
    <Chip
      label={activeRoleGroup.name}
      variant="outlined"
      color="primary"
      onClick={() => navigate(`/roles/${activeRoleGroup.name}`)}
      onDelete={
        canManageThisGroup || canManageUserRoleGroup
          ? () => {
              removeRoleGroup(activeRoleGroup);
            }
          : undefined
      }
    />
  ) : (
    <Chip
      label="Direct"
      color="primary"
      onDelete={
        canManageThisUser
          ? () => {
              removeDirectAccessAsUser();
            }
          : canManageThisGroup
            ? () => {
                removeDirectAccessAsGroupManager();
              }
            : undefined
      }
    />
  );
}
