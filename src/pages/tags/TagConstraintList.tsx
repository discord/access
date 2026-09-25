import * as React from 'react';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

import {ConstraintHelpButton, ConstraintHelpRegion, useConstraintHelp} from '../../components/ConstraintHelp';
import {
  MEMBER_TIME_LIMIT,
  OWNER_TIME_LIMIT,
  constraintDetail,
  constraintLabel,
  constraintSummary,
} from '../../constraintCopy';
import {timeLimitLabel} from '../../constraints';

const TIME_LIMITS = [MEMBER_TIME_LIMIT, OWNER_TIME_LIMIT];

/**
 * How a stored constraint value reads to a person.
 *
 * Time limits are stored in seconds and shown in days. A flag reads "Yes", since
 * only flags that are switched on reach this list. Anything else is a constraint
 * this build has no copy for -- a backend serving a seventh setting -- so its
 * value is printed as it stands rather than described as a flag it is not.
 */
function constraintValue(key: string, value: number | boolean): string {
  if (TIME_LIMITS.includes(key) && typeof value === 'number') {
    return timeLimitLabel(value);
  }
  return typeof value === 'boolean' ? 'Yes' : String(value);
}

function ConstraintEntry({
  constraint,
  value,
  propagateToRoles,
  first,
}: {
  constraint: string;
  value: number | boolean;
  propagateToRoles: boolean;
  first: boolean;
}) {
  const help = useConstraintHelp(constraint);
  const label = constraintLabel(constraint);
  // Absent for a constraint this build has no copy for, in which case there is no
  // button either: it would toggle a region that renders nothing.
  const paragraphs = constraintDetail(constraint, propagateToRoles);
  return (
    <Box
      sx={{
        borderTop: first ? 'none' : '1px solid',
        borderColor: 'divider',
        padding: '10px 0',
      }}>
      <Box sx={{display: 'flex', alignItems: 'center', gap: '8px'}}>
        <Box sx={{display: 'flex', alignItems: 'center', gap: '2px', flex: 1, minWidth: 0}}>
          <Typography variant="body1">{label}</Typography>
          {paragraphs.length > 0 && (
            <ConstraintHelpButton
              label={label}
              expanded={help.expanded}
              onToggle={help.toggle}
              regionId={help.regionId}
            />
          )}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{whiteSpace: 'nowrap'}}>
          {constraintValue(constraint, value)}
        </Typography>
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{display: 'block'}}>
        {constraintSummary(constraint, propagateToRoles)}
      </Typography>
      <ConstraintHelpRegion sections={[{paragraphs}]} expanded={help.expanded} regionId={help.regionId} />
    </Box>
  );
}

/**
 * The constraints a tag carries, as a list of name, value and explanation.
 *
 * A list rather than a table: there are at most six entries and only two columns
 * worth of data, so the header row, the column rules and a total count are
 * furniture around six facts. The space that buys goes to the sentence under each
 * entry saying what the constraint does at this tag's scope.
 *
 * @param constraintKeys The keys to show, already filtered to those in force and
 *   sorted; the caller owns both, since it is also the thing counting them.
 * @param constraints The tag's stored `constraints` bag.
 * @param propagateToRoles The tag's `propagate_to_roles`, already defaulted.
 */
export default function TagConstraintList({
  constraintKeys,
  constraints,
  propagateToRoles,
}: {
  constraintKeys: string[];
  constraints: Record<string, any>;
  propagateToRoles: boolean;
}) {
  return (
    <Box sx={{marginTop: '8px'}}>
      {constraintKeys.map((key, index) => (
        <ConstraintEntry
          key={key}
          constraint={key}
          value={constraints[key]}
          propagateToRoles={propagateToRoles}
          first={index === 0}
        />
      ))}
    </Box>
  );
}
