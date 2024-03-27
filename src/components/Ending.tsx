import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserGroupMember, RoleGroupMap} from '../api/apiSchemas';

dayjs.extend(RelativeTime);

function selectLastTime(
  a: OktaUserGroupMember | RoleGroupMap,
  b: OktaUserGroupMember | RoleGroupMap,
): OktaUserGroupMember | RoleGroupMap {
  if (a.ended_at == null) return a;
  if (b.ended_at == null) return b;
  if (a.ended_at > b.ended_at) {
    return a;
  } else {
    return b;
  }
}

interface EndingProps {
  memberships: Array<OktaUserGroupMember | RoleGroupMap>;
}

export default function Ending(props: EndingProps) {
  const lastMembership = props.memberships.reduce(selectLastTime);

  return lastMembership.ended_at == null ? (
    <span>Never</span>
  ) : (
    <span title={lastMembership.ended_at}>{dayjs(lastMembership.ended_at).startOf('second').fromNow()}</span>
  );
}
