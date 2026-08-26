import FormControl from '@mui/material/FormControl';
import FormHelperText from '@mui/material/FormHelperText';
import ToggleButton, {ToggleButtonProps} from '@mui/material/ToggleButton';
import ToggleButtonGroup, {ToggleButtonGroupProps} from '@mui/material/ToggleButtonGroup';
import React from 'react';
import {Controller, FieldError, FieldPath, FieldValues, RegisterOptions, useFormContext} from 'react-hook-form';

/** Mirrors react-hook-form-mui: an option may carry any ToggleButton prop (`selected`, `sx`, ...). */
export type ToggleButtonOption = Omit<ToggleButtonProps, 'value' | 'children'> & {
  id: string | number;
  label: React.ReactNode;
};

export type ToggleButtonGroupElementProps<TFieldValues extends FieldValues = FieldValues> = Omit<
  ToggleButtonGroupProps,
  'name' | 'value' | 'onChange' | 'exclusive'
> & {
  name: FieldPath<TFieldValues>;
  options: readonly ToggleButtonOption[];
  exclusive?: boolean;
  /** Ignore a deselect that would leave nothing selected. */
  enforceAtLeastOneSelected?: boolean;
  required?: boolean;
  validation?: RegisterOptions<TFieldValues>;
  parseError?: (error: FieldError) => string;
  onChange?: (event: React.MouseEvent<HTMLElement>, value: unknown) => void;
};

export default function ToggleButtonGroupElement<TFieldValues extends FieldValues = FieldValues>({
  name,
  options,
  exclusive,
  enforceAtLeastOneSelected,
  required,
  validation,
  parseError,
  onChange,
  ...toggleButtonGroupProps
}: ToggleButtonGroupElementProps<TFieldValues>) {
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
        <FormControl required={required} error={!!error}>
          <ToggleButtonGroup
            {...toggleButtonGroupProps}
            exclusive={exclusive}
            value={field.value ?? (exclusive ? null : [])}
            onChange={(event, value) => {
              if (enforceAtLeastOneSelected && (value == null || (Array.isArray(value) && value.length === 0))) {
                return;
              }
              field.onChange(value);
              onChange?.(event, value);
            }}>
            {options.map(({id, label, ...buttonProps}) => (
              <ToggleButton key={id} value={id} {...buttonProps}>
                {label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          {error ? <FormHelperText>{parseError ? parseError(error) : error.message ?? ''}</FormHelperText> : null}
        </FormControl>
      )}
    />
  );
}
