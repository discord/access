import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {OktaUserGroupMember, RoleGroupMap} from '../api/apiSchemas';

dayjs.extend(RelativeTime);

function selectFirstTime(
  a: OktaUserGroupMember | RoleGroupMap,
  b: OktaUserGroupMember | RoleGroupMap,
): OktaUserGroupMember | RoleGroupMap {
  if (a.created_at === undefined) {
    return a;
  }
  if (b.created_at === undefined) {
    return b;
  }
  if (a.created_at < b.created_at) {
    return a;
  } else {
    return b;
  }
}

interface StartedProps {
  memberships: Array<OktaUserGroupMember | RoleGroupMap>;
}

export default function Started(props: StartedProps) {
  const firstMembership = props.memberships.reduce(selectFirstTime);

  return firstMembership.created_at == null ? (
    <span>Never</span>
  ) : (
    <span title={firstMembership.created_at}>{dayjs(firstMembership.created_at).startOf('second').fromNow()}</span>
  );
}
