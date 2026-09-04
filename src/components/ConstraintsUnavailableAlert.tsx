import React from 'react';

import Alert from '@mui/material/Alert';

import type {EffectiveConstraintsReader} from '../constraints';

/**
 * Explains a submit control disabled because the constraints could not be read.
 *
 * Queries are configured with `retry: false` (see `src/index.tsx`), so one
 * failed `/api/constraints/effective` leaves a dialog disabled until something
 * remounts it. Without this the user sees a greyed-out button and no reason
 * for it.
 *
 * Renders nothing while the request is merely in flight — that resolves on its
 * own and a momentary alert would be noise.
 */
export default function ConstraintsUnavailableAlert({
  constraints,
  action,
}: {
  constraints: Pick<EffectiveConstraintsReader, 'error'>;
  action: string;
}) {
  if (constraints.error == null) {
    return null;
  }
  return (
    <Alert severity="error">
      Could not load the constraints that apply here, so {action} is disabled. Reload the page to try again.
    </Alert>
  );
}
