import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserGroupMemberDetail, RoleGroupMapDetail, AuditUserGroupRow, AuditGroupRoleRow} from '../api/apiSchemas';

dayjs.extend(RelativeTime);

type MembershipLike = OktaUserGroupMemberDetail | RoleGroupMapDetail | AuditUserGroupRow | AuditGroupRoleRow;

function selectLastTime(a: MembershipLike, b: MembershipLike): MembershipLike {
  if (a.ended_at == null) return a;
  if (b.ended_at == null) return b;
  if (dayjs(a.ended_at).isAfter(dayjs(b.ended_at))) {
    return a;
  } else {
    return b;
  }
}

interface EndingProps {
  memberships: Array<MembershipLike>;
}

export default function Ending(props: EndingProps) {
  const lastMembership = props.memberships.reduce(selectLastTime);

  return lastMembership.ended_at == null ? (
    <span>Never</span>
  ) : (
    <span title={lastMembership.ended_at}>{dayjs(lastMembership.ended_at).startOf('second').fromNow()}</span>
  );
}
