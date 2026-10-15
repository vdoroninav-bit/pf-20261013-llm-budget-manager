// Экран «Вход» (design/screens.md §1.1): логин и пароль, ошибки под формой, фокус в «Пароль» после отказа.
import { useRef, useState, type FormEvent } from 'react';
import { Alert, Box, Button, CardContent, IconButton, InputAdornment, TextField, Typography } from '@mui/material';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import { Login, useLogin } from 'react-admin';
import { ThemeAttributeSync } from '../layout/ThemeAttributeSync';
import { ru } from '../i18n/ru';
import { messageOf, statusOf } from '../components/common';

const t = ru.bm.login;

function loginErrorText(error: unknown): string {
  const status = statusOf(error);
  if (status === 0 || (status !== undefined && status >= 500)) return t.network;
  return messageOf(error);
}

function BmLoginForm() {
  const login = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await login({ username, password });
    } catch (err) {
      setError(loginErrorText(err));
      setPassword('');
      setBusy(false);
      // Фокус — в поле «Пароль» (screens.md §1.1); после перерисовки, когда поле снова доступно.
      setTimeout(() => passwordRef.current?.focus(), 0);
    }
  }

  return (
    <CardContent component="form" noValidate onSubmit={submit} aria-label={t.submit}>
      <TextField
        label={t.login}
        name="username"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        fullWidth
        margin="normal"
        required
        autoFocus
        slotProps={{ htmlInput: { autoComplete: 'username', readOnly: busy } }}
      />
      <TextField
        label={t.password}
        name="password"
        type={show ? 'text' : 'password'}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        fullWidth
        margin="normal"
        required
        inputRef={passwordRef}
        slotProps={{
          htmlInput: { autoComplete: 'current-password', readOnly: busy },
          input: {
            endAdornment: (
              <InputAdornment position="end">
                <IconButton
                  aria-label={show ? t.hide_password : t.show_password}
                  onClick={() => setShow((v) => !v)}
                  edge="end"
                >
                  {show ? <VisibilityOff /> : <Visibility />}
                </IconButton>
              </InputAdornment>
            ),
          },
        }}
      />
      {error && (
        <Alert severity="error" role="alert" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
      <Button type="submit" variant="contained" fullWidth sx={{ mt: 2 }} disabled={busy}>
        {busy ? t.submitting : t.submit}
      </Button>
    </CardContent>
  );
}

export function LoginPage() {
  return (
    <Login
      avatarIcon={null}
      sx={{
        backgroundImage: 'none',
        backgroundColor: 'background.default',
        '& .RaLogin-card': { width: '100%', maxWidth: 400, borderRadius: 'var(--bm-radius-md)' },
      }}
    >
      <ThemeAttributeSync />
      <Box component="header" sx={{ px: 2, pt: 3 }}>
        <Typography component="h1" variant="h5" sx={{ fontWeight: 600 }}>
          {ru.bm.app.title}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {ru.bm.app.subtitle}
        </Typography>
      </Box>
      <BmLoginForm />
    </Login>
  );
}
