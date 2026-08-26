import Autocomplete, {AutocompleteProps} from '@mui/material/Autocomplete';
import TextField, {TextFieldProps} from '@mui/material/TextField';
import {Controller, FieldError, FieldPath, FieldValues, RegisterOptions, useFormContext} from 'react-hook-form';

/**
 * TypeScript does not infer through a conditional type, so routing `TOption` through
 * `Deferred` keeps `autocompleteProps` from participating in inference. `TOption` is then
 * fixed by `options` alone, which is what lets the callbacks inside `autocompleteProps`
 * (getOptionLabel, isOptionEqualToValue, ...) type correctly with no call-site annotation.
 * On TypeScript >= 5.4 this can be replaced with the built-in `NoInfer<TOption>`.
 */
type Deferred<T> = T extends unknown ? T : never;
export interface AutocompleteElementProps<TOption, TFieldValues extends FieldValues = FieldValues> {
  name: FieldPath<TFieldValues>;
  label?: string;
  options: readonly TOption[];
  required?: boolean;
  loading?: boolean;
  validation?: RegisterOptions<TFieldValues>;
  parseError?: (error: FieldError) => string;
  autocompleteProps?: Partial<
    Omit<AutocompleteProps<Deferred<TOption>, false, false, false>, 'options' | 'renderInput' | 'value'>
  >;
  textFieldProps?: Partial<TextFieldProps>;
}

export default function AutocompleteElement<TOption, TFieldValues extends FieldValues = FieldValues>({
  name,
  label,
  options,
  required,
  loading,
  validation,
  parseError,
  autocompleteProps,
  textFieldProps,
}: AutocompleteElementProps<TOption, TFieldValues>) {
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
        <Autocomplete
          {...(autocompleteProps as object)}
          options={options}
          loading={loading}
          value={(field.value ?? null) as TOption | null}
          onChange={(event, value, reason, details) => {
            field.onChange(value);
            autocompleteProps?.onChange?.(event, value as Deferred<TOption>, reason, details);
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              {...textFieldProps}
              label={label}
              required={required}
              error={!!error}
              helperText={error ? (parseError ? parseError(error) : error.message ?? '') : textFieldProps?.helperText}
            />
          )}
        />
      )}
    />
  );
}
