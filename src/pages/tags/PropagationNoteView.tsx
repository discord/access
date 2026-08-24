import React from 'react';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

import {propagationParts} from './propagationNote';

// Renders the tag-propagation sentence with the "do" / "do not" clause bolded.
// Built directly from propagationParts() (see propagationNote.ts) so the
// rendered copy can never drift from the plain-string helper the tests assert
// against — see PropagationNoteView.test.tsx.
export default function PropagationNoteView({propagateToRoles}: {propagateToRoles: boolean}) {
  const [before, emphasis, after] = propagationParts(propagateToRoles);
  return (
    <Typography variant="body2" sx={{marginTop: '4px'}}>
      {before}
      <Box component="span" sx={{fontWeight: 'bold'}}>
        {emphasis}
      </Box>
      {after}
    </Typography>
  );
}
