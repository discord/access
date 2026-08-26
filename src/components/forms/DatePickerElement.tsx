import {DatePicker, DatePickerProps} from '@mui/x-date-pickers/DatePicker';
import {Dayjs} from 'dayjs';
import {Controller, FieldError, FieldPath, FieldValues, RegisterOptions, useFormContext} from 'react-hook-form';

export type DatePickerElementProps<TFieldValues extends FieldValues = FieldValues> = Omit<
  DatePickerProps<Dayjs>,
  'name' | 'value' | 'onChange' | 'minDate' | 'maxDate'
> & {
  minDate?: Dayjs | null;
  maxDate?: Dayjs | null;
  name: FieldPath<TFieldValues>;
  required?: boolean;
  validation?: RegisterOptions<TFieldValues>;
  parseError?: (error: FieldError) => string;
};

/**
 * Stores the picker's Dayjs value directly in form state, matching the behavior the
 * call sites already rely on (they cast the field and call `.toISOString()` on submit).
 */
export default function DatePickerElement<TFieldValues extends FieldValues = FieldValues>({
  name,
  required,
  validation,
  parseError,
  slotProps,
  minDate,
  maxDate,
  ...datePickerProps
}: DatePickerElementProps<TFieldValues>) {
  const {control} = useFormContext<TFieldValues>();
  const rules: RegisterOptions<TFieldValues> = {
    ...(required ? {required: 'This field is required'} : {}),
    ...validation,
  };

  return (
    <Controller
      name={name}
      control={control}
      rules={rules}
      render={({field, fieldState: {error}}) => (
        <DatePicker
          {...datePickerProps}
          minDate={minDate ?? undefined}
          maxDate={maxDate ?? undefined}
          value={(field.value ?? null) as Dayjs | null}
          onChange={(value, context) => field.onChange(value)}
          inputRef={field.ref}
          slotProps={{
            ...slotProps,
            textField: {
              required,
              error: !!error,
              helperText: error ? (parseError ? parseError(error) : error.message ?? '') : undefined,
              ...(slotProps?.textField as object),
            },
          }}
        />
      )}
    />
  );
}
