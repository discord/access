import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import {Controller, useFormContext, useWatch} from 'react-hook-form';
import {SelectElement} from 'react-hook-form-mui';

import accessConfig from '../config/accessConfig';

type UntilOption = {id: string; label: string};

const REQUEST_UNTIL_ID_TO_LABELS: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
  indefinite: 'Indefinite',
  custom: 'Custom',
} as const;

const RESOLVE_UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;

const untilOptions = (labels: Record<string, string>): UntilOption[] =>
  Object.entries(labels).map(([id, label]) => ({id, label}));

const REQUEST_UNTIL_OPTIONS = untilOptions(REQUEST_UNTIL_ID_TO_LABELS);
const RESOLVE_UNTIL_OPTIONS = untilOptions(RESOLVE_UNTIL_ID_TO_LABELS);

// The durations in `labels` still on offer under `timeLimit`, and the longest of them.
// Indefinite is not among them, and neither is any duration over the limit; Custom
// stays, since its own picker is bounded separately.
function filterUntilLabels(labels: Record<string, string>, timeLimit: number): [string | undefined, UntilOption[]] {
  const withinLimit = Object.entries(labels).filter(([key]) => !isNaN(Number(key)) && Number(key) <= timeLimit);
  return [withinLimit.at(-1)?.[0], untilOptions({...Object.fromEntries(withinLimit), custom: 'Custom'})];
}

/**
 * The end date a request's length selection asks for.
 *
 * Args:
 *   until: The selected option id: a number of seconds, 'indefinite', or 'custom'.
 *   custom: The custom end date, read when `until` is 'custom'.
 *
 * Returns:
 *   The end date as an ISO string, or undefined for Indefinite.
 */
export function endingAt(until: string | undefined, custom: string | undefined): string | undefined {
  switch (until) {
    case 'indefinite':
      return undefined;
    case 'custom':
      return (custom as unknown as Dayjs).toISOString();
    default:
      return dayjs()
        .add(parseInt(until ?? '0', 10), 'seconds')
        .toISOString();
  }
}

/**
 * The length option an end date asks for, measured from when it was asked.
 *
 * Args:
 *   endingAt: The end date, or none for indefinite access.
 *   askedAt: When the end date was asked for.
 *
 * Returns:
 *   'indefinite', the id of the matching duration, or 'custom' when none matches.
 */
export function untilIdFor(endingAt: string | null | undefined, askedAt: string | null | undefined): string {
  if (!endingAt) {
    return 'indefinite';
  }
  const delta = Math.round(dayjs(endingAt).diff(dayjs(askedAt), 'second') / 100) * 100;
  return delta in RESOLVE_UNTIL_ID_TO_LABELS ? delta.toString() : 'custom';
}

/**
 * The end date an approver's length selection sends with a resolution.
 *
 * Undefined keeps the requested end date and null asks for indefinite access. Indefinite is
 * sent only over a requested end date, since without one it is what was asked for.
 *
 * Args:
 *   until: The selected option id: a number of seconds, 'indefinite', or 'custom'.
 *   custom: The custom end date, read when `until` is 'custom'.
 *   requested: The end date the request asked for, or none for indefinite access.
 *
 * Returns:
 *   The end date as an ISO string, null for indefinite access, or undefined to keep the
 *   requested end date.
 */
export function resolvedEndingAt(
  until: string | undefined,
  custom: string | undefined,
  requested: string | null | undefined,
): string | null | undefined {
  switch (until) {
    case undefined:
      return undefined;
    case 'indefinite':
      return requested ? null : undefined;
    case 'custom':
      return custom ? dayjs(custom).toISOString() : undefined;
    default:
      return dayjs().add(parseInt(until, 10), 'seconds').toISOString();
  }
}

interface AccessLengthFieldProps {
  name: 'ownershipUntil' | 'membershipUntil';
  label: string;
  timeLimit: number | null;
  blocked: boolean;
  onChange: (value: string) => void;
}

/**
 * A select for how long a requested grant should last.
 *
 * Offers only the durations the tags in force allow, and moves the field off one they do
 * not. A request the constraints forbid is not refused on submit (`ApproveGroupRequest`
 * shortens it on approval instead), so a duration left on offer here is one the requester
 * is told they asked for and does not get.
 *
 * Args:
 *   name: The form field holding the selected option id.
 *   label: The field label.
 *   timeLimit: The tag time limit in seconds, or null when there is none or it is unknown.
 *   blocked: Whether the constraints could not be answered.
 *   onChange: Called with the newly selected option id.
 */
export function AccessLengthField({name, label, timeLimit, blocked, onChange}: AccessLengthFieldProps) {
  const {control, setValue} = useFormContext();
  const selected = useWatch({control, name});

  const [options, defaultId] = React.useMemo<[UntilOption[], string]>(() => {
    // While the answer is unknown the limit reads as null. Holding the narrower
    // list rather than re-offering the full one keeps a duration the tags forbid
    // from being briefly clickable.
    if (timeLimit == null) {
      return blocked
        ? [[], accessConfig.DEFAULT_ACCESS_TIME]
        : [REQUEST_UNTIL_OPTIONS, accessConfig.DEFAULT_ACCESS_TIME];
    }
    const [lastId, filtered] = filterUntilLabels(REQUEST_UNTIL_ID_TO_LABELS, timeLimit);
    return [filtered, lastId ?? 'custom'];
  }, [timeLimit, blocked]);

  React.useEffect(() => {
    if (timeLimit == null) return;
    if (!options.some((option) => option.id === selected)) {
      setValue(name, defaultId);
      onChange(defaultId);
    }
  }, [name, timeLimit, options, defaultId, selected, setValue, onChange]);

  return (
    <SelectElement
      fullWidth
      label={label}
      name={name}
      options={options}
      onChange={(value) => onChange(value)}
      required
    />
  );
}

interface AccessEndingFieldProps {
  name: 'resolved_ownership_ending_at' | 'resolved_membership_ending_at';
  label: string;
  timeLimit: number | null;
  constraintsBlocked: boolean;
  setUntil: (v: string | null) => void;
}

/**
 * A select for how long an approved grant should last, seeded from what was requested.
 *
 * Args:
 *   name: The form field holding the selected option id.
 *   label: The field label.
 *   timeLimit: The tag time limit in seconds, or null when there is none or it is unknown.
 *   constraintsBlocked: Whether the constraints could not be answered.
 *   setUntil: Called with the newly selected option id.
 */
export function AccessEndingField({name, label, timeLimit, constraintsBlocked, setUntil}: AccessEndingFieldProps) {
  const {control, setValue} = useFormContext();

  const [availableUntilOptions, defaultUntilId] = React.useMemo<[UntilOption[], string | undefined]>(() => {
    // While the tag-mode answer is in flight the limit reads as null. Holding
    // the narrower list rather than re-offering the full one keeps a duration
    // the chosen tags forbid from being briefly clickable.
    if (timeLimit == null) {
      return constraintsBlocked
        ? [[], accessConfig.DEFAULT_ACCESS_TIME]
        : [RESOLVE_UNTIL_OPTIONS, accessConfig.DEFAULT_ACCESS_TIME];
    }
    const [lastId, filtered] = filterUntilLabels(RESOLVE_UNTIL_ID_TO_LABELS, timeLimit);
    return [filtered, lastId];
  }, [timeLimit, constraintsBlocked]);

  const selectedUntil = useWatch({control, name});

  React.useEffect(() => {
    if (timeLimit == null) return;
    // A limit drops Indefinite and every duration over it from the list. The
    // field is seeded from the request, so it can be holding one of them: a
    // Select whose value has no option renders blank, and submits as it stands
    // for `ApproveGroupRequest` to shorten. Move it to the longest duration
    // still offered instead.
    if (!availableUntilOptions.some((option) => option.id === selectedUntil)) {
      setUntil(defaultUntilId ?? null);
      setValue(name, defaultUntilId ?? '');
    }
  }, [name, timeLimit, selectedUntil, availableUntilOptions, defaultUntilId, setValue, setUntil]);

  return (
    <FormControl margin="normal" fullWidth>
      <InputLabel id={`${name}-label`}>{label}</InputLabel>
      <Controller
        name={name}
        control={control}
        render={({field}) => (
          <Select
            {...field}
            labelId={`${name}-label`}
            label={label}
            onChange={(e) => {
              field.onChange(e);
              setUntil(e.target.value as string);
            }}>
            {availableUntilOptions.map(({id, label}) => (
              <MenuItem key={id} value={id}>
                {label}
              </MenuItem>
            ))}
          </Select>
        )}
      />
    </FormControl>
  );
}
