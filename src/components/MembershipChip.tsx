import {OktaUserGroupMember, PolymorphicGroup, RoleGroup} from '../api/apiSchemas';
import {useNavigate} from 'react-router-dom';
import {useCurrentUser} from '../authentication';
import {canManageGroup, isGroupOwner} from '../authorization';
import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';

export interface MembershipChipProps {
  okta_user_group_member: OktaUserGroupMember;
  group: PolymorphicGroup;
  removeRoleGroup: (roleGroup: RoleGroup) => void;
  removeDirectAccessAsUser: () => void;
  removeDirectAccessAsGroupManager: () => void;
}

export default function MembershipChip({
  okta_user_group_member,
  group,
  removeRoleGroup,
  removeDirectAccessAsUser,
  removeDirectAccessAsGroupManager,
}: MembershipChipProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();
  const activeRoleGroup = okta_user_group_member.active_role_group_mapping?.active_role_group;
  const ending_date = okta_user_group_member.ended_at ?? 'Never';
  const canManageUserRoleGroup = activeRoleGroup?.id ? isGroupOwner(currentUser, activeRoleGroup.id) : false;
  const canManageThisGroup = group.is_managed && canManageGroup(currentUser, group);
  const canManageThisUser = group.is_managed && currentUser.id === okta_user_group_member.active_user?.id;

  const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

  return activeRoleGroup ? (
    <Tooltip title={ending_date} placement="right" PopperProps={moveTooltip}>
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
    </Tooltip>
  ) : (
    <Tooltip title={ending_date} placement="right" PopperProps={moveTooltip}>
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
    </Tooltip>
  );
}
