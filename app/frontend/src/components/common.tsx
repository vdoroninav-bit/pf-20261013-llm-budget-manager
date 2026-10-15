// Общие компоненты экранов (13_design.md §6): EmptyState, ProblemAlert, ConfirmDialog, MoneyText, Mono, useRole.
import { useEffect, useState, type ReactNode } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Typography,
} from '@mui/material';
import { useListContext, usePermissions } from 'react-admin';
import type { Role } from '../api/types';
import { formatMoney } from '../format/money';
import { ru } from '../i18n/ru';
import { SLOW_REQUEST_MS } from '../config/ui';

const t = ru.bm;

export function useRole(): Role | undefined {
  const { permissions } = usePermissions<Role>();
  return permissions;
}

export const statusOf = (error: unknown): number | undefined =>
  error && typeof error === 'object' && 'status' in error ? Number((error as { status: unknown }).status) : undefined;

export const codeOf = (error: unknown): string | undefined => {
  const body = error && typeof error === 'object' ? (error as { body?: { code?: unknown } }).body : undefined;
  return typeof body?.code === 'string' ? body.code : undefined;
};

export const messageOf = (error: unknown): string =>
  error instanceof Error && error.message ? error.message : ru.bm.problem.unknown;

/** Машинный идентификатор: моноширинный, не переводится (13_design.md §8, «Язык»). */
export function Mono({ children }: { children: ReactNode }) {
  return (
    <Box component="span" lang="en" translate="no" sx={{ fontFamily: 'var(--bm-font-mono, monospace)' }}>
      {children}
    </Box>
  );
}

/** Сумма Money: «12 345,67 ₽», отрицательная — цвет danger и слово «перерасход». */
export function MoneyText({ value }: { value: string | null | undefined }) {
  const negative = typeof value === 'string' && value.startsWith('-');
  return (
    <Box
      component="span"
      sx={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', color: negative ? 'error.main' : undefined }}
    >
      {formatMoney(value)}
    </Box>
  );
}

export interface EmptyStateProps {
  message: string;
  actions?: ReactNode;
  variant?: 'empty' | 'forbidden';
}

export function EmptyState({ message, actions, variant = 'empty' }: EmptyStateProps) {
  return (
    <Box role={variant === 'forbidden' ? 'alert' : 'status'} sx={{ p: 3, maxWidth: 720 }}>
      <Typography>{message}</Typography>
      {actions && <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>{actions}</Box>}
    </Box>
  );
}

export const Forbidden = () => <EmptyState variant="forbidden" message={t.state.forbidden} />;

/** Ошибка загрузки: 403 — «Нет доступа», иначе текст Problem и «Повторить». */
export function ProblemAlert({ error, onRetry, children }: { error: unknown; onRetry?: () => void; children?: ReactNode }) {
  if (statusOf(error) === 403) return <Forbidden />;
  return (
    <Alert
      severity="error"
      sx={{ m: 2 }}
      action={
        onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            {t.state.retry}
          </Button>
        ) : undefined
      }
    >
      {messageOf(error)}
      {children}
    </Alert>
  );
}

/** Для <List error={…}>: ошибка списка с «Повторить». */
export function ListError() {
  const { error, refetch } = useListContext();
  return <ProblemAlert error={error} onRetry={() => void refetch()} />;
}

/** «Загрузка дольше обычного…» и «Повторить» через SLOW_REQUEST_MS (13_design_rulings.md, В7). Запрос не обрывается. */
export function SlowHint({ pending, onRetry }: { pending: boolean; onRetry?: () => void }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!pending) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), SLOW_REQUEST_MS);
    return () => clearTimeout(timer);
  }, [pending]);
  if (!slow) return null;
  return (
    <Alert
      severity="info"
      role="status"
      action={
        onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            {t.state.retry}
          </Button>
        ) : undefined
      }
    >
      {t.state.loading_slow}
    </Alert>
  );
}

/** Подсказка о медленной загрузке внутри списка react-admin. */
export function ListSlowHint() {
  const { isPending, refetch } = useListContext();
  return <SlowHint pending={isPending} onRetry={() => void refetch()} />;
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  text: string;
  confirm: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** Подтверждение значимого действия. Для danger фокус по умолчанию на «Отмена» (13_design.md §8). */
export function ConfirmDialog({ open, title, text, confirm, danger, onConfirm, onClose }: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="bm-confirm-title"
      aria-describedby="bm-confirm-text"
    >
      <DialogTitle id="bm-confirm-title">{title}</DialogTitle>
      <DialogContent>
        <DialogContentText id="bm-confirm-text">{text}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} autoFocus={danger}>
          {ru.ra.action.cancel}
        </Button>
        <Button
          onClick={onConfirm}
          color={danger ? 'error' : 'primary'}
          variant="contained"
          autoFocus={!danger}
        >
          {confirm}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
