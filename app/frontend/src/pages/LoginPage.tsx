// Каркас экрана «Вход» (design/screens.md §1.1). Состояния ошибок и фокус доводятся в T043.
import { Box, Typography } from '@mui/material';
import { Login, LoginForm, useTranslate } from 'react-admin';
import { ThemeAttributeSync } from '../layout/ThemeAttributeSync';

export function LoginPage() {
  const translate = useTranslate();
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
          {translate('bm.app.title')}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {translate('bm.app.subtitle')}
        </Typography>
      </Box>
      <LoginForm />
    </Login>
  );
}
