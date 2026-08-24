import * as React from 'react';
import {Link as RouterLink} from 'react-router-dom';

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

const TIME_LIMIT_CONSTRAINTS = ['member_time_limit', 'owner_time_limit'];

function constraintLabel(entry: EffectiveConstraintDetail): string {
  if (TIME_LIMIT_CONSTRAINTS.includes(entry.constraint)) {
    // `value` is typed `void` by the generator (a known quirk — see
    // apiSchemas.ts), but is a number of seconds at runtime. Round to the
    // nearest day: constraint values aren't guaranteed to divide evenly by
    // 86400 (e.g. a CLI-driven cap), and an unrounded quotient renders an
    // ugly, falsely-precise fraction of a day.
    const days = Math.round(Number(entry.value) / 86400);
    return `${entry.name} — ${days} days`;
  }
  // Booleans are simple flags: their presence in the list is the information,
  // so appending "— Yes" would be noise.
  if (typeof entry.value === 'boolean') {
    return entry.name;
  }
  return `${entry.name} — ${entry.value}`;
}

function originLabel(source: EffectiveConstraintSourceDetail): string {
  switch (source.origin) {
    case 'app':
      return `via app ${source.source_group_name ?? ''}`.trim();
    case 'member_association':
      return `via membership in ${source.source_group_name}`;
    case 'owner_association':
      return `via ownership of ${source.source_group_name}`;
    case 'direct':
      return 'direct';
    default:
      // An origin we don't have specific copy for. Echo it back rather than
      // asserting a specific (and possibly wrong) meaning like "direct".
      return source.origin;
  }
}

export default function EffectiveConstraints({constraints}: {constraints: EffectiveConstraintDetail[]}) {
  const [expanded, setExpanded] = React.useState(false);

  if (constraints.length === 0) {
    return null;
  }

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
              {constraints.map((entry) => (
                <TableRow key={entry.constraint}>
                  <TableCell>{constraintLabel(entry)}</TableCell>
                  <TableCell>
                    {(entry.sources ?? []).map((source, index) => (
                      <div key={`${source.tag_id}-${source.origin}-${source.source_group_id ?? index}`}>
                        <Link component={RouterLink} to={`/tags/${source.tag_name}`}>
                          {source.tag_name}
                        </Link>
                        {`, ${originLabel(source)}`}
                      </div>
                    ))}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </AccordionDetails>
    </Accordion>
  );
}
