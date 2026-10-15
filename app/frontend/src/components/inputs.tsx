// Поля форм с подтверждением значимого изменения (screens.md §3.2, §4.2) и денежные/ценовые поля.
import { useId, useState } from 'react';
import {
  FormControl,
  FormControlLabel,
  FormHelperText,
  FormLabel,
  Radio,
  RadioGroup,
  Switch,
} from '@mui/material';
import { TextInput, useInput, type TextInputProps } from 'react-admin';
import { parsePriceInput } from '../format/money';
import { ru } from '../i18n/ru';
import { ConfirmDialog } from './common';

export interface Confirmation {
  title: string;
  text: string;
  confirm: string;
}

export interface RadioWithConfirmProps {
  source: string;
  label: string;
  choices: Array<{ id: string; name: string }>;
  helperText?: string;
  /** Значения, переход на которые требует подтверждения. */
  confirmFor?: Record<string, Confirmation>;
  disabled?: boolean;
}

export function RadioWithConfirm({ source, label, choices, helperText, confirmFor = {}, disabled }: RadioWithConfirmProps) {
  const { field } = useInput({ source });
  const [pending, setPending] = useState<string | null>(null);
  const labelId = useId();
  const helpId = useId();
  const confirmation = pending ? confirmFor[pending] : undefined;
  return (
    <FormControl component="fieldset" disabled={disabled} sx={{ mb: 2 }}>
      <FormLabel component="legend" id={labelId}>
        {label}
      </FormLabel>
      <RadioGroup
        aria-labelledby={labelId}
        aria-describedby={helperText ? helpId : undefined}
        name={source}
        value={field.value ?? ''}
        onChange={(e) => {
          const next = e.target.value;
          if (confirmFor[next] && next !== field.value) setPending(next);
          else field.onChange(next);
        }}
      >
        {choices.map((c) => (
          <FormControlLabel key={c.id} value={c.id} control={<Radio />} label={c.name} />
        ))}
      </RadioGroup>
      {helperText && <FormHelperText id={helpId}>{helperText}</FormHelperText>}
      {confirmation && (
        <ConfirmDialog
          open
          danger
          title={confirmation.title}
          text={confirmation.text}
          confirm={confirmation.confirm}
          onClose={() => setPending(null)}
          onConfirm={() => {
            field.onChange(pending);
            setPending(null);
          }}
        />
      )}
    </FormControl>
  );
}

export interface SwitchWithConfirmProps {
  source: string;
  label: string;
  /** Подтверждение перехода в false. */
  confirmOff?: Confirmation;
  helperText?: string;
  disabled?: boolean;
}

export function SwitchWithConfirm({ source, label, confirmOff, helperText, disabled }: SwitchWithConfirmProps) {
  const { field } = useInput({ source });
  const [asking, setAsking] = useState(false);
  const helpId = useId();
  return (
    <FormControl sx={{ mb: 2 }} disabled={disabled}>
      <FormControlLabel
        label={label}
        control={
          <Switch
            checked={!!field.value}
            disabled={disabled}
            onChange={(e) => {
              if (!e.target.checked && confirmOff) setAsking(true);
              else field.onChange(e.target.checked);
            }}
            slotProps={{ input: { 'aria-describedby': helperText ? helpId : undefined } }}
          />
        }
      />
      {helperText && <FormHelperText id={helpId}>{helperText}</FormHelperText>}
      {confirmOff && (
        <ConfirmDialog
          open={asking}
          danger
          title={confirmOff.title}
          text={confirmOff.text}
          confirm={confirmOff.confirm}
          onClose={() => setAsking(false)}
          onConfirm={() => {
            field.onChange(false);
            setAsking(false);
          }}
        />
      )}
    </FormControl>
  );
}

const validatePrice = (required: boolean) => (value: unknown) => {
  if ((value === null || value === undefined || value === '') && !required) return undefined;
  return parsePriceInput(String(value ?? '')).ok ? undefined : ru.bm.money.price_error;
};

/** PriceInput (13_design.md §6): цена за 1 000 токенов строкой, до 6 знаков, без float. */
export function PriceInput({ required = true, ...props }: TextInputProps & { required?: boolean }) {
  return (
    <TextInput
      {...props}
      validate={validatePrice(required)}
      slotProps={{ htmlInput: { inputMode: 'decimal', autoComplete: 'off' } }}
    />
  );
}
