import React from 'react';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';

interface InlineReasonProps {
  reason?: string;
}

export default function InlineReason({reason}: InlineReasonProps) {
  if (!reason) {
    return (
      <Typography variant="body2" color="text.secondary">
        No reason given
      </Typography>
    );
  }

  return (
    <Box sx={{maxWidth: 300}}>
      <Typography
        variant="body2"
        sx={{
          // A role-derived justification is two labelled lines; without this
          // they render as one run-on sentence. The clamp below still bounds
          // the height.
          whiteSpace: 'pre-line',
          wordBreak: 'break-word',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          display: '-webkit-box',
          WebkitLineClamp: 3,
          WebkitBoxOrient: 'vertical',
        }}>
        {reason}
      </Typography>
    </Box>
  );
}
