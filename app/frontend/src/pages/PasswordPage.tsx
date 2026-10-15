// Экран «Смена пароля» (design/screens.md §1.2). Принудительно — после входа с временным паролем
// (must_change_password) и при 403 password_change_required; добровольно — из меню пользователя.
import { useState, type FormEvent } from 'react';
import { Alert, Box, Button, Card, CardContent, TextField, Typography } from '@mui/material';
import { Title, useAuthProvider, useGetIdentity, useLogout, useNotify, useRedirect } from 'react-admin';
import type { BmAuthProvider, BmIdentity } from '../authProvider';
import { ru } from '../i18n/ru';
import { codeOf, messageOf } from '../components/common';

const t = ru.bm.password;
const MIN_LENGTH = 12;

type Field = 'old' | 'new' | 'repeat';

export function PasswordPage() {
  const auth = useAuthProvider() as BmAuthProvider | undefined;
  const { identity, isPending, refetch: refetchIdentity } = useGetIdentity();
  const forced = !!(identity as BmIdentity | undefined)?.mustChangePassword;
  const logout = useLogout();
  const notify = useNotify();
  const redirect = useRedirect();
  const [values, setValues] = useState<Record<Field, string>>({ old: '', new: '', repeat: '' });
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (f: Field) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [f]: e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: Partial<Record<Field, string>> = {};
    if (!values.old) next.old = ru.ra.validation.required;
    if (values.new.length < MIN_LENGTH) next.new = t.hint;
    if (values.repeat !== values.new) next.repeat = t.mismatch;
    setErrors(next);
    setFailure(null);
    const first = (['old', 'new', 'repeat'] as const).find((f) => next[f]);
    if (first) {
      document.getElementById(`bm-password-${first}`)?.focus();
      return;
    }
    if (!auth) return;
    setBusy(true);
    try {
      await auth.changePassword({ old_password: values.old, new_password: values.new });
      notify(t.done, { type: 'success' });
      void refetchIdentity();
      redirect('/');
    } catch (err) {
      const fieldErrors = (err as { body?: { errors?: Record<string, unknown> } }).body?.errors;
      if (codeOf(err) === 'invalid_credentials') setErrors({ old: t.wrong_old });
      else if (fieldErrors && ('new_password' in fieldErrors || 'old_password' in fieldErrors)) {
        setErrors({
          ...(typeof fieldErrors.old_password === 'string' ? { old: fieldErrors.old_password } : {}),
          ...(typeof fieldErrors.new_password === 'string' ? { new: fieldErrors.new_password } : {}),
        });
      } else setFailure(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  const field = (f: Field, label: string, autoComplete: string, helper?: string) => (
    <TextField
      id={`bm-password-${f}`}
      label={label}
      type="password"
      value={values[f]}
      onChange={set(f)}
      error={!!errors[f]}
      helperText={errors[f] ?? helper ?? ' '}
      fullWidth
      margin="normal"
      disabled={busy}
      slotProps={{ htmlInput: { autoComplete } }}
    />
  );

  // Пока неизвестно, принудительная ли смена, форму не показываем: от этого зависят кнопки «Отмена»/«Выйти».
  if (isPending) return null;

  return (
    <Card sx={{ mt: 2, maxWidth: 480 }}>
      <Title title={t.title} />
      <CardContent component="form" noValidate onSubmit={submit}>
        {forced && <Typography sx={{ mb: 1 }}>{t.forced}</Typography>}
        {field('old', t.old, 'current-password')}
        {field('new', t.new, 'new-password', t.hint)}
        {field('repeat', t.repeat, 'new-password')}
        {failure && (
          <Alert severity="error" role="alert" sx={{ mt: 1 }}>
            {failure}
          </Alert>
        )}
        <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
          <Button type="submit" variant="contained" disabled={busy}>
            {busy ? t.submitting : t.submit}
          </Button>
          {forced ? (
            <Button onClick={() => logout()}>{t.logout}</Button>
          ) : (
            <Button onClick={() => redirect('/')}>{ru.ra.action.cancel}</Button>
          )}
        </Box>
      </CardContent>
    </Card>
  );
}
