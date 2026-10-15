import * as React from 'react';
import {Link as RouterLink} from 'react-router-dom';

import Box from '@mui/material/Box';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

import {EffectiveConstraintDetail, EffectiveConstraintSourceDetail} from '../api/apiSchemas';
import {timeLimitLabel} from '../constraints';
import {byConstraintOrder, constraintDetail, constraintLabel} from '../constraintCopy';
import {ConstraintHelpButton, ConstraintHelpRegion, useConstraintHelp} from './ConstraintHelp';

const TIME_LIMIT_CONSTRAINTS = ['member_time_limit', 'owner_time_limit'];

// Keyed off `entry.constraint` rather than the `name` the API also sends, so this
// panel and the tag pages call a constraint the same thing. `constraintLabel`
// falls back to the key for a constraint this build has no copy for.
function constraintRowLabel(entry: EffectiveConstraintDetail): string {
  const label = constraintLabel(entry.constraint);
  const value = entry.value;
  if (typeof value === 'number' && TIME_LIMIT_CONSTRAINTS.includes(entry.constraint)) {
    return `${label}: ${timeLimitLabel(value)}`;
  }
  // Booleans are simple flags: their presence in the list is the information,
  // so appending ": Yes" would be noise. That holds because the API reports
  // only constraints in force -- a flag every tag setting it turns off is left
  // out of the response entirely (`_constraint_entry` in `api/models/tag.py`),
  // which matters since the tag form writes all four boolean keys on every
  // save. Were a `False` to arrive here it would render as a restriction.
  if (typeof value === 'boolean') {
    return label;
  }
  return `${label}: ${value}`;
}

// The text before the source's name, by origin. `direct` is absent on
// purpose: it names no other site, so there is nothing to link.
const ORIGIN_PREFIX: Record<string, string> = {
  app: 'via app ',
  member_association: 'via membership in ',
  owner_association: 'via ownership of ',
};

// Where the named source lives. An app origin's source is an App, not a
// group, which is why the API's fields are named for the origin rather than
// for a group.
function sourceHref(source: EffectiveConstraintSourceDetail): string | null {
  if (source.source_name == null) {
    return null;
  }
  switch (source.origin) {
    case 'app':
      return `/apps/${encodeURIComponent(source.source_name)}`;
    case 'member_association':
    case 'owner_association':
      return `/groups/${encodeURIComponent(source.source_name)}`;
    default:
      return null;
  }
}

function OriginText({source}: {source: EffectiveConstraintSourceDetail}) {
  const prefix = ORIGIN_PREFIX[source.origin];
  if (prefix == null) {
    // `direct`, or an origin this build has no copy for. Echo it back rather
    // than asserting a specific (and possibly wrong) meaning.
    return <>{source.origin}</>;
  }
  const href = sourceHref(source);
  if (href == null) {
    // `active_app` filters soft-deleted apps, so the name can be missing while
    // the origin is still worth stating. Trimmed so it does not read as though
    // a name were about to follow.
    return <>{prefix.trim()}</>;
  }
  return (
    <>
      {prefix}
      <Link component={RouterLink} to={href}>
        {source.source_name}
      </Link>
    </>
  );
}

/**
 * One constraint in force, with its sources and its help.
 *
 * The help omits the paragraph about a tag's scope: this row can coalesce several
 * tags, which need not agree on it, so the scope is a question for the tag pages
 * the source column already links to.
 */
function ConstraintRow({entry}: {entry: EffectiveConstraintDetail}) {
  const help = useConstraintHelp(entry.constraint);
  const label = constraintLabel(entry.constraint);
  const paragraphs = constraintDetail(entry.constraint);
  return (
    <>
      <TableRow>
        <TableCell sx={paragraphs.length > 0 && help.expanded ? {borderBottom: 'none'} : undefined}>
          <Box sx={{display: 'flex', alignItems: 'center', gap: '4px'}}>
            {constraintRowLabel(entry)}
            {paragraphs.length > 0 && (
              <ConstraintHelpButton
                label={label}
                expanded={help.expanded}
                onToggle={help.toggle}
                regionId={help.regionId}
              />
            )}
          </Box>
        </TableCell>
        <TableCell sx={paragraphs.length > 0 && help.expanded ? {borderBottom: 'none'} : undefined}>
          {(entry.sources ?? []).map((source, index) => (
            <div key={`${source.tag_id}-${source.origin}-${source.source_id ?? index}`}>
              <Link component={RouterLink} to={`/tags/${encodeURIComponent(source.tag_name)}`}>
                {source.tag_name}
              </Link>
              {', '}
              <OriginText source={source} />
            </div>
          ))}
        </TableCell>
      </TableRow>
      {/* Only while open: a permanently mounted row would leave an empty `tr`
          under every constraint, which a screen reader reads out as a row. */}
      {paragraphs.length > 0 && help.expanded && (
        <TableRow>
          <TableCell colSpan={2} sx={{paddingTop: 0}}>
            <ConstraintHelpRegion sections={[{paragraphs}]} expanded regionId={help.regionId} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

export default function EffectiveConstraints({constraints}: {constraints: EffectiveConstraintDetail[]}) {
  const [expanded, setExpanded] = React.useState(false);

  if (constraints.length === 0) {
    return null;
  }

  // The API returns entries in `Tag.CONSTRAINTS` order, which is its own concern
  // and not one this panel should inherit. Sorted here so every listing of
  // constraints in the app reads in the same order.
  const ordered = [...constraints].sort((a, b) => byConstraintOrder(a.constraint, b.constraint));

  return (
    <Accordion expanded={expanded} onChange={(_e, isExpanded) => setExpanded(isExpanded)}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="h6" color="text.accent">
          Effective constraints ({constraints.length})
        </Typography>
      </AccordionSummary>
      <AccordionDetails>
        <TableContainer component={Paper} elevation={0}>
          <Table size="small" aria-label="effective constraints">
            <TableHead>
              <TableRow>
                <TableCell>Constraint</TableCell>
                <TableCell>Source</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {ordered.map((entry) => (
                <ConstraintRow key={entry.constraint} entry={entry} />
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </AccordionDetails>
    </Accordion>
  );
}
