import React from 'react';

import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';

import {useTheme} from '@mui/material';
import EventIcon from '@mui/icons-material/Event';

import {Dayjs} from 'dayjs';

import {DatePicker, DatePickerProps} from '@mui/x-date-pickers/DatePicker';
import {PickersDay, PickersDayProps} from '@mui/x-date-pickers/PickersDay';
import {UseDateFieldProps} from '@mui/x-date-pickers/DateField';
import {BaseSingleInputFieldProps, DateValidationError, FieldSection} from '@mui/x-date-pickers/models';

function HighlightDay(props: PickersDayProps<Dayjs> & {startDate?: Dayjs; endDate?: Dayjs; rangeSelected: boolean}) {
  const {startDate, endDate, rangeSelected, ...rest} = props;

  const theme = useTheme();

  // Dates in range to be highlighted
  const isSelected =
    rangeSelected &&
    !props.outsideCurrentMonth &&
    startDate!.isBefore(props.day, 'day') &&
    props.day.isBefore(endDate!, 'day');

  // Make sure right start day is selected
  const start = !props.outsideCurrentMonth && props.day.isSame(startDate!, 'day') && rangeSelected;
  // Get the end day so that the class 'Mui-selected' can be applied (adds the circle around the day)
  const end = !props.outsideCurrentMonth && props.day.isSame(endDate!, 'day') && rangeSelected;

  let selectedClass = '';
  let style = {};
  let dayStyle = {};

  if (rangeSelected && props.day.isSame(endDate!, 'day')) {
    selectedClass = 'Mui-selected';
  }

  if (isSelected) {
    style = {backgroundColor: theme.palette.primary.light};
    dayStyle = {color: theme.palette.common.black};
  } else if (start) {
    style = {
      background: `linear-gradient(90deg, transparent 50%, ${theme.palette.primary.light} 50%)`,
    };
  } else if (end) {
    style = {background: `linear-gradient(90deg, ${theme.palette.primary.light} 50%, transparent 50%)`};
  }

  return (
    <Box component={'div'} sx={style} key={props.day.toString()}>
      <PickersDay className={selectedClass} sx={dayStyle} {...rest} />
    </Box>
  );
}

// The `false` type argument selects the text-field DOM structure the picker uses by default,
// which is the one that forwards the `InputProps`/`inputProps` that ButtonField reads below.
interface ButtonFieldProps
  extends UseDateFieldProps<Dayjs, false>,
    BaseSingleInputFieldProps<Dayjs | null, Dayjs, FieldSection, false, DateValidationError> {
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  startDate: Dayjs | null;
  tmpStartDate: Dayjs | null;
  endDate: Dayjs | null;
  rangeSelected: boolean;
}

function ButtonField(props: ButtonFieldProps) {
  const {
    setOpen,
    startDate,
    tmpStartDate,
    endDate,
    rangeSelected,
    id,
    disabled,
    InputProps: {ref} = {},
    inputProps: {'aria-label': ariaLabel} = {},
  } = props;

  const theme = useTheme();
  let displayString = '';

  if (rangeSelected) {
    displayString = startDate?.format('MM/DD/YYYY') + ' - ' + endDate?.format('MM/DD/YYYY');
  } else {
    displayString = tmpStartDate?.format('MM/DD/YYYY') + ' - MM/DD/YYYY';
  }

  return (
    <Box component={'div'} sx={{position: 'relative'}}>
      <Button
        variant="outlined"
        size="medium"
        id={id}
        disabled={disabled}
        ref={ref}
        aria-label={ariaLabel}
        onClick={() => setOpen?.((prev) => !prev)}
        sx={{
          color: theme.palette.text.secondary,
          borderColor: theme.palette.action.disabled,
          minWidth: '245px',
          fontSize: '15px',
          position: 'relative',
          zIndex: '1',
        }}>
        {displayString} <EventIcon sx={{marginLeft: '20px', color: theme.palette.text.secondary}} />
      </Button>
      <Typography
        component={'span'}
        sx={{
          fontSize: '12px',
          color: theme.palette.text.secondary,
          position: 'absolute',
          // HACK: Match dark mode MUI Paper at elevation 1, which is where this is currently used
          backgroundColor: theme.palette.mode === 'light' ? theme.palette.background.default : '#1E1E1E',
          marginLeft: '5px',
          paddingX: '3px',
          zIndex: '2',
          top: '0.01%',
          left: '1%',
          transform: 'translateY(-7px)',
        }}>
        Ending Date Range
      </Typography>
    </Box>
  );
}

interface DateRangeProps extends DatePickerProps<Dayjs> {
  startDate: Dayjs | null;
  endDate: Dayjs | null;
  // Both ends of a completed range are committed in a single call. Callers store the range in
  // the query string, and `setSearchParams` hands its updater a copy of the *committed* params,
  // so two writes from one event handler would both start from the pre-click state and the
  // second would discard the first.
  setDateRange: (newStartDate: Dayjs | null, newEndDate: Dayjs | null) => void;
  datesPicked: number;
  setDatesPicked: (newDatesPicked: number) => void;
}

export default function DateRangePicker(props: DateRangeProps) {
  const {startDate, endDate, setDateRange, datesPicked, setDatesPicked, value, onChange, ...rest} = props;

  const [open, setOpen] = React.useState(false);
  const [tmpStartDate, setTmpStartDate] = React.useState<Dayjs | null>(null);

  return (
    <Box sx={{display: 'flex', alignItems: 'center'}}>
      <DatePicker
        value={props.startDate}
        open={open}
        onClose={() => setOpen(false)}
        onOpen={() => setOpen(true)}
        closeOnSelect={false}
        onChange={(date: any) => {
          props.setDatesPicked(props.datesPicked + 1);
          if (props.datesPicked % 2 !== 0) {
            props.setDateRange(tmpStartDate, date);
          } else {
            setTmpStartDate(date);
          }
        }}
        minDate={tmpStartDate && props.datesPicked % 2 != 0 ? tmpStartDate : undefined}
        slots={{
          day: HighlightDay as any,
          field: ButtonField as any,
        }}
        slotProps={{
          day: {
            startDate: props.startDate,
            endDate: props.endDate,
            rangeSelected: props.datesPicked % 2 == 0,
          } as any,
          field: {
            setOpen: setOpen,
            startDate: props.startDate,
            tmpStartDate: tmpStartDate,
            endDate: props.endDate,
            rangeSelected: props.datesPicked % 2 == 0,
          } as any,
        }}
      />
    </Box>
  );
}
