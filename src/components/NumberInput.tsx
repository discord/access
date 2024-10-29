import * as React from 'react';

import {
  Unstable_NumberInput as BaseNumberInput,
  NumberInputProps as NumInputProps,
  numberInputClasses,
} from '@mui/base/Unstable_NumberInput';
import {styled} from '@mui/material/styles';

const NumInput = React.forwardRef(function CustomNumberInput(
  props: NumInputProps,
  ref: React.ForwardedRef<HTMLDivElement>,
) {
  return (
    <BaseNumberInput
      slots={{
        root: InputRoot,
        input: InputElement,
        incrementButton: Button,
        decrementButton: Button,
      }}
      slotProps={{
        incrementButton: {
          children: <span className="arrow">▴</span>,
        },
        decrementButton: {
          children: <span className="arrow">▾</span>,
        },
      }}
      {...props}
      ref={ref}
    />
  );
});

interface NumberInputProps {
  label: string;
  min?: number;
  max?: number;
  default?: number;
  endAdornment?: string;
  setValue: (val: number | undefined) => void;
}

export default function NumberInput(props: NumberInputProps) {
  return (
    <NumInput
      aria-label={props.label}
      min={props.min}
      max={props.max}
      defaultValue={props.default}
      onChange={(event, val) => {
        event.preventDefault();
        props.setValue(val ?? undefined);
      }}
      endAdornment={<InputAdornment>{props.endAdornment}</InputAdornment>}
    />
  );
}

const InputAdornment = styled('div')(
  ({theme}) => `
    margin: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    grid-row: 1/3;
    color: ${theme.palette.text.secondary};
  `,
);

const InputRoot = styled('div')(
  ({theme}) => `
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 400;
    border-radius: 4px;
    border: 1px solid ${theme.palette.action.disabled};
    display: grid;
    grid-template-columns: auto 1fr auto 19px;
    grid-template-rows: 1fr 1fr;
    overflow: hidden;
    padding: 4px;
  
    &.${numberInputClasses.focused} {
      border: 1px solid transparent;
      outline: 2px solid ${theme.palette.primary.main};
    }
  
    &:hover:not(.${numberInputClasses.focused}) {
      border-color: ${theme.palette.action.active};
    }
  
    // firefox
    &:focus-visible {
      outline: 0;
    }
  `,
);

const InputElement = styled('input')(
  ({theme}) => `
    font-size: 0.875rem;
    font-family: inherit;
    font-weight: 400;
    line-height: 1.5;
    grid-row: 1/3;
    color: ${theme.palette.text.primary};
    background: inherit;
    border: none;
    border-radius: inherit;
    padding: 8px 12px;
    outline: 0;
  `,
);

const Button = styled('button')(
  ({theme}) => `
    display: flex;
    flex-flow: row nowrap;
    justify-content: center;
    align-items: center;
    appearance: none;
    padding: 0;
    width: 19px;
    height: 20px;
    font-family: system-ui, sans-serif;
    font-size: 0.875rem;
    line-height: 1;
    box-sizing: border-box;
    border: 1px solid ${theme.palette.action.disabled};
    transition-property: all;
    transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    transition-duration: 120ms;
    color: ${theme.palette.text.primary};
    background-color: transparent;
    grid-column: 4/5;
  
    &:hover {
      background: ${theme.palette.action.hover};
      cursor: pointer;
    }
  
    &.${numberInputClasses.incrementButton} {
      grid-row: 1/2;
      border-top-left-radius: 4px;
      border-top-right-radius: 4px;
      border-bottom: 0px;
    }
  
    &.${numberInputClasses.decrementButton} {
      grid-row: 2/3;
      border-bottom-left-radius: 4px;
      border-bottom-right-radius: 4px;
    }
  
    & .arrow {
      transform: translateY(-1px);
    }
  `,
);
