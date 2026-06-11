import * as Sentry from '@sentry/react';

import {useUserById} from './api/apiComponents';

import {OktaUserDetail} from './api/apiSchemas';

export function useCurrentUser() {
  const {data: currentUserData} = useUserById({
    pathParams: {userId: '@me'},
  });

  const currentUser = currentUserData ?? ({} as OktaUserDetail);

  if (currentUser.id && currentUser.email) {
    Sentry.setUser({
      id: currentUser.id,
      email: currentUser.email.toLowerCase(),
    });
  }

  return currentUser;
}
