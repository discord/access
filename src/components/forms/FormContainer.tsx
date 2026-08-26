import React from 'react';
import {FieldValues, FormProvider, SubmitHandler, useForm, UseFormProps} from 'react-hook-form';

export interface FormContainerProps<TFieldValues extends FieldValues = FieldValues> extends UseFormProps<TFieldValues> {
  onSuccess?: SubmitHandler<TFieldValues>;
  children?: React.ReactNode;
}

/**
 * Owns a react-hook-form instance and publishes it via FormProvider, so the field
 * wrappers in this directory (and any bare `Controller`) can reach it with `useFormContext`.
 */
export default function FormContainer<TFieldValues extends FieldValues = FieldValues>({
  onSuccess,
  children,
  ...useFormProps
}: FormContainerProps<TFieldValues>) {
  const methods = useForm<TFieldValues>(useFormProps);
  return (
    <FormProvider {...methods}>
      <form noValidate onSubmit={onSuccess ? methods.handleSubmit(onSuccess) : undefined}>
        {children}
      </form>
    </FormProvider>
  );
}
