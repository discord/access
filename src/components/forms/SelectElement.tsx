import FormControl from '@mui/material/FormControl';
import FormHelperText from '@mui/material/FormHelperText';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select, {SelectProps} from '@mui/material/Select';
import React, {useId} from 'react';
import {Controller, FieldError, FieldPath, FieldValues, RegisterOptions, useFormContext} from 'react-hook-form';

/**
 * Deliberately loose: some call sites pass `Record<string, string>` maps built from
 * `Object.entries`, which react-hook-form-mui also accepted.
 */
export type SelectOption = Record<string, unknown>;

export type SelectElementProps<TFieldValues extends FieldValues = FieldValues> = Omit<
  SelectProps,
  'name' | 'error' | 'onChange' | 'value'
> & {
  name: FieldPath<TFieldValues>;
  options: readonly SelectOption[];
  validation?: RegisterOptions<TFieldValues>;
  parseError?: (error: FieldError) => string;
  /**
   * Receives the selected option id. Typed `any` to match the previous
   * react-hook-form-mui signature, whose call sites feed it into narrower setters.
   */
  onChange?: (value: any) => void;
  helperText?: string;
};

export default function SelectElement<TFieldValues extends FieldValues = FieldValues>({
  name,
  options,
  validation,
  parseError,
  onChange,
  required,
  label,
  fullWidth,
  helperText,
  ...selectProps
}: SelectElementProps<TFieldValues>) {
  const {control} = useFormContext<TFieldValues>();
  const labelId = useId();
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
        <FormControl fullWidth={fullWidth} required={required} error={!!error}>
          {label ? <InputLabel id={labelId}>{label}</InputLabel> : null}
          <Select
            {...selectProps}
            {...field}
            labelId={labelId}
            label={label}
            value={field.value ?? ''}
            onChange={(event) => {
              field.onChange(event);
              onChange?.(event.target.value);
            }}>
            {options.map((option) => (
              <MenuItem key={String(option.id)} value={option.id as string | number}>
                {option.label as React.ReactNode}
              </MenuItem>
            ))}
          </Select>
          {error || helperText ? (
            <FormHelperText>
              {error ? (parseError ? parseError(error) : error.message ?? '') : helperText}
            </FormHelperText>
          ) : null}
        </FormControl>
      )}
    />
  );
}
