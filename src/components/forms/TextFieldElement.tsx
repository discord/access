import TextField, {TextFieldProps} from '@mui/material/TextField';
import {Controller, FieldError, FieldPath, FieldValues, RegisterOptions, useFormContext} from 'react-hook-form';

export type TextFieldElementProps<TFieldValues extends FieldValues = FieldValues> = Omit<
  TextFieldProps,
  'name' | 'error' | 'helperText'
> & {
  name: FieldPath<TFieldValues>;
  validation?: RegisterOptions<TFieldValues>;
  parseError?: (error: FieldError) => string;
  helperText?: TextFieldProps['helperText'];
};

export default function TextFieldElement<TFieldValues extends FieldValues = FieldValues>({
  name,
  validation,
  parseError,
  required,
  helperText,
  ...textFieldProps
}: TextFieldElementProps<TFieldValues>) {
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
        <TextField
          {...textFieldProps}
          {...field}
          value={field.value ?? ''}
          required={required}
          error={!!error}
          helperText={error ? (parseError ? parseError(error) : error.message ?? '') : helperText}
        />
      )}
    />
  );
}
