import * as React from 'react';

import Box from '@mui/material/Box';
import Collapse from '@mui/material/Collapse';
import IconButton from '@mui/material/IconButton';
import Typography from '@mui/material/Typography';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';

import type {HelpParagraph} from '../constraintCopy';

/**
 * State for one constraint's help disclosure, wiring a button to a region.
 *
 * Trigger and region are separate components because their placement differs: in
 * the tag form the button sits on a control inside a two-column grid while the
 * region spans both columns beneath the row, and putting the prose inside the cell
 * would stretch its neighbour. The caller decides where each goes; this keeps the
 * `id` and the open flag consistent between them.
 *
 * @param key A key from `Tag.CONSTRAINTS`, used to build the region's element id.
 */
export function useConstraintHelp(key: string) {
  const [expanded, setExpanded] = React.useState(false);
  const regionId = `constraint-help-${key}`;
  return {
    expanded,
    toggle: () => setExpanded((open) => !open),
    regionId,
  };
}

/**
 * The control that reveals a constraint's longform help.
 *
 * A real button rather than a focusable label: it is focusable because it does
 * something, it announces as a button, and it works by touch; none of which a
 * hover tooltip on a text node manages.
 *
 * @param label The constraint's display name, which names the button for a screen
 *   reader since the icon carries no text. Quoted rather than run into a sentence,
 *   because the labels are noun phrases that no single article fits.
 */
export function ConstraintHelpButton({
  label,
  expanded,
  onToggle,
  regionId,
}: {
  label: string;
  expanded: boolean;
  onToggle: () => void;
  regionId: string;
}) {
  return (
    <IconButton
      size="small"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={regionId}
      aria-label={`What “${label}” means`}
      sx={{padding: '2px'}}>
      <HelpOutlineIcon fontSize="small" />
    </IconButton>
  );
}

/**
 * One labelled block of help inside a region.
 *
 * `label` is only needed where a single button covers more than one constraint:
 * the tag form's matrix pairs a membership and an ownership side under one row, and
 * naming each block is what tells the reader which is which.
 */
export interface HelpSection {
  label?: string;
  paragraphs: HelpParagraph[];
}

/**
 * The region a `ConstraintHelpButton` reveals.
 *
 * Renders nothing when no section has any copy, so an unrecognised constraint gets
 * no empty panel.
 */
export function ConstraintHelpRegion({
  sections,
  expanded,
  regionId,
}: {
  sections: HelpSection[];
  expanded: boolean;
  regionId: string;
}) {
  const filled = sections.filter((section) => section.paragraphs.length > 0);
  if (filled.length === 0) {
    return null;
  }
  return (
    <Collapse in={expanded} unmountOnExit>
      <Box
        id={regionId}
        sx={{
          backgroundColor: (theme) => theme.palette.action.hover,
          borderRadius: 1,
          padding: '10px 12px',
          margin: '8px 0 2px 0',
        }}>
        {filled.map((section, sectionIndex) => (
          <Box key={section.label ?? sectionIndex} sx={{marginTop: sectionIndex === 0 ? 0 : '12px'}}>
            {section.label != null && (
              <Typography variant="subtitle2" sx={{marginBottom: '2px'}}>
                {section.label}
              </Typography>
            )}
            {section.paragraphs.map((paragraph, index) => (
              <Typography key={index} variant="body2" sx={{marginTop: index === 0 ? 0 : '8px'}}>
                {paragraph.lead != null && (
                  <Box component="span" sx={{fontWeight: 'bold'}}>
                    {paragraph.lead}
                  </Box>
                )}
                {paragraph.text}
              </Typography>
            ))}
          </Box>
        ))}
      </Box>
    </Collapse>
  );
}
