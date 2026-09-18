import React from 'react';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

import type {HelpParagraph} from './constraintHelp';

/**
 * Renders constraint help text inside a `Tooltip`.
 *
 * A paragraph's `lead` is emphasised and its `text` is not, so the two
 * propagation cases stand out from the prose around them. Split rather than
 * marked up because MUI's `title` takes a ReactNode and would render markdown
 * literally; keeping the copy as data also lets `constraintHelp.ts` stay a pure
 * module that vitest can test. Same approach as `PropagationNoteView`.
 */
export default function ConstraintHelpText({paragraphs}: {paragraphs: HelpParagraph[]}) {
  return (
    <>
      {paragraphs.map((paragraph, index) => (
        <Typography key={index} variant="body2" sx={{marginTop: index === 0 ? 0 : '8px'}}>
          {paragraph.lead != null && (
            <Box component="span" sx={{fontWeight: 'bold'}}>
              {paragraph.lead}
            </Box>
          )}
          {paragraph.text}
        </Typography>
      ))}
    </>
  );
}
