import * as Sentry from '@sentry/react';

import {useGetUserById} from './api/apiComponents';

import {OktaUser} from './api/apiSchemas';

export function useCurrentUser() {
  const {data: currentUserData} = useGetUserById({
    pathParams: {userId: '@me'},
  });

  const currentUser = currentUserData ?? ({} as OktaUser);

  if (currentUser.id && currentUser.email) {
    Sentry.setUser({
      id: currentUser.id,
      email: currentUser.email.toLowerCase(),
    });
  }

  return currentUser;
}
