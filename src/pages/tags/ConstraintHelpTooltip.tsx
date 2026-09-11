import React from 'react';

import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';
import type {SxProps, Theme} from '@mui/material/styles';

import ConstraintHelpText from './ConstraintHelpText';
import type {HelpParagraph} from './constraintHelp';

/**
 * A constraint's name, with its help text attached as a tooltip.
 *
 * Both views wrap their labels in this so the accessibility wiring lives in one
 * place:
 *
 * - `describeChild` makes the help text the label's *description* rather than
 *   its name. MUI's default for a tooltip is to label the child, which for a
 *   paragraph of prose means a screen reader announces the whole explanation
 *   where the label should be, and the label itself never at all.
 * - `tabIndex` makes the trigger focusable, which is what lets the tooltip be
 *   opened at all without a pointer -- MUI opens on focus, and the
 *   `aria-describedby` that `describeChild` sets only exists while it is open.
 *   A `title` that is a ReactNode gets no native `title` attribute to fall back
 *   on, so without this the copy is unreachable for anyone not using a mouse.
 *
 * Renders the name unadorned when there is no help text, rather than a
 * focusable element that describes nothing.
 */
export default function ConstraintHelpTooltip({
  paragraphs,
  children,
  sx,
}: {
  paragraphs: HelpParagraph[];
  children: React.ReactNode;
  sx?: SxProps<Theme>;
}) {
  if (paragraphs.length === 0) {
    return <Box sx={sx}>{children}</Box>;
  }
  return (
    <Tooltip title={<ConstraintHelpText paragraphs={paragraphs} />} placement="top-start" describeChild>
      {/* `width: 'fit-content'` keeps the trigger on the text: without it the
          Box fills its container and both the hover target and the focus ring
          extend across empty space beside the label. */}
      <Box tabIndex={0} sx={{width: 'fit-content', ...sx}}>
        {children}
      </Box>
    </Tooltip>
  );
}
