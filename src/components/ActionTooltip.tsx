import * as React from 'react';

import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';

// Nudges the label back over the icon it names, which is what every call site
// wanted from the `PopperProps` it used to pass in by hand.
const OVER_THE_ICON = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

/**
 * A label for an icon button that opens a dialog.
 *
 * An uncontrolled `Tooltip` stays open until its anchor sees a mouseleave. An
 * anchor that opens a modal never sees one: the modal covers it and marks the
 * whole app root `aria-hidden`, so the label outlives the click, hangs over the
 * dialog, and is still there after the dialog closes. Worse, focus returning to
 * the button opens a second one, so the stale poppers accumulate.
 *
 * Closing on activation is the part the anchor cannot do for itself. Everything
 * else is left to `Tooltip`, including reopening on a later hover or focus.
 *
 * @param title The label to show.
 * @param children The control to label, which owns its own dialog.
 */
export default function ActionTooltip({title, children}: {title: string; children: React.ReactNode}) {
  const [open, setOpen] = React.useState(false);
  return (
    <Tooltip
      title={title}
      placement="top"
      open={open}
      // Refuse to open while a dialog is up. Closing on activation is not enough
      // on its own: the press focuses the button, and MUI treats focus as a
      // reason to open, so the label comes straight back over the dialog.
      onOpen={() => setOpen(document.querySelector('[role="dialog"]') == null)}
      onClose={() => setOpen(false)}
      PopperProps={OVER_THE_ICON}>
      {/* On mousedown rather than click: the press focuses the button first, and
          a focused button is a reason for MUI to open the tooltip again. */}
      <Box onMouseDownCapture={() => setOpen(false)}>{children}</Box>
    </Tooltip>
  );
}
