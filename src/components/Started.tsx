import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserGroupMemberDetail, RoleGroupMapDetail, AuditUserGroupRow, AuditGroupRoleRow} from '../api/apiSchemas';

dayjs.extend(RelativeTime);

// All the row shapes whose start time we render carry a nullable `created_at`.
type MembershipLike = OktaUserGroupMemberDetail | RoleGroupMapDetail | AuditUserGroupRow | AuditGroupRoleRow;

function selectFirstTime(a: MembershipLike, b: MembershipLike): MembershipLike {
  if (a.created_at == null) {
    return a;
  }
  if (b.created_at == null) {
    return b;
  }
  if (a.created_at < b.created_at) {
    return a;
  } else {
    return b;
  }
}

interface StartedProps {
  memberships: Array<MembershipLike>;
}

export default function Started(props: StartedProps) {
  const firstMembership = props.memberships.reduce(selectFirstTime);

  return firstMembership.created_at == null ? (
    <span>Never</span>
  ) : (
    <span title={firstMembership.created_at}>{dayjs(firstMembership.created_at).startOf('second').fromNow()}</span>
  );
}
