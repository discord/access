import React from 'react';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

// Renders the tag-propagation sentence with the "do" / "do not" clause bolded.
// Split into three parts so only that clause is emphasised; the surrounding
// text is identical either way.
export default function PropagationNoteView({propagateToRoles}: {propagateToRoles: boolean}) {
  return (
    <Typography variant="body2" sx={{marginTop: '4px'}}>
      {'These constraints '}
      <Box component="span" sx={{fontWeight: 'bold'}}>
        {propagateToRoles ? 'do' : 'do not'}
      </Box>
      {' apply to roles that own or are members of groups with this tag.'}
    </Typography>
  );
}
