// Обвязка карточек react-admin: ошибки загрузки записи и сохранения в духе 13_design.md §7.1.
import type { ReactNode } from 'react';
import { Alert, Box, Typography } from '@mui/material';
import { SaveButton, Toolbar, useEditContext, useNotify, useShowContext } from 'react-admin';
import { Link } from 'react-router-dom';
import { ProblemAlert, codeOf, messageOf } from './common';
import { ru } from '../i18n/ru';

/** Для <Edit error={…}>. */
export function EditError() {
  const { error, refetch } = useEditContext();
  return <ProblemAlert error={error} onRetry={() => void refetch()} />;
}

/** Для <Show error={…}>. */
export function ShowError() {
  const { error, refetch } = useShowContext();
  return <ProblemAlert error={error} onRetry={() => void refetch()} />;
}

/** queryOptions карточки: ошибку показывает error-компонент, без автоматического ухода в список. */
export const recordQueryOptions = { onError: () => undefined, retry: false } as const;

/**
 * Обработчик ошибки сохранения: текст по Problem.code из карты экрана, иначе текст Problem.
 * Ошибки полей (422 errors[]) react-admin выводит под полями сам — уведомление дублирует только сводку.
 */
export function useSaveErrorNotifier(byCode: Record<string, string> = {}) {
  const notify = useNotify();
  return (error: unknown) => {
    const code = codeOf(error);
    notify(code && byCode[code] ? byCode[code] : messageOf(error), { type: 'error' });
  };
}

/** Пояснение на форме/карточке. */
export function Hint({ children }: { children: ReactNode }) {
  return (
    <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 720 }}>
      {children}
    </Typography>
  );
}

/** Ошибка «нет курса USD/₽» со ссылкой на ввод курса (экран 5, вкладка «Курсы»; T052). */
export function FxMissingAlert({ text }: { text: string }) {
  return (
    <Alert severity="error" role="alert" sx={{ mb: 2 }}>
      {text}{' '}
      <Link to="/fx-rates">{ru.bm.models.fx_link}</Link>
    </Alert>
  );
}

export function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Box component="section" aria-label={label} sx={{ mt: 2 }}>
      {children}
    </Box>
  );
}

/** Панель формы без «Удалить»: удаления у этих ресурсов в контракте нет. */
export function SaveOnlyToolbar({ label }: { label?: string }) {
  return (
    <Toolbar>
      <SaveButton label={label} />
    </Toolbar>
  );
}
