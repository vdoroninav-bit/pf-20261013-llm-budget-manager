// Каркас экрана «Смена пароля» (design/screens.md §1.2). Маршрут нужен уже в T002: authProvider.login
// перенаправляет сюда при must_change_password. Форма (old/new/повтор, 422/401) — в T043.
import { Card } from '@mui/material';
import { Title, useTranslate } from 'react-admin';

export function PasswordPage() {
  const translate = useTranslate();
  return (
    <Card sx={{ mt: 2 }}>
      <Title title={translate('bm.password.title')} />
    </Card>
  );
}
