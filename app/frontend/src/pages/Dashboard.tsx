// Заглушка панели: показывает, кто вошёл. Панель подразделения (шкалы бюджета) — T072.
import { Card, CardContent, Typography } from '@mui/material';
import { Title, useGetIdentity, useTranslate } from 'react-admin';
import type { BmIdentity } from '../authProvider';

export function Dashboard() {
  const translate = useTranslate();
  const { identity } = useGetIdentity();
  const me = identity as BmIdentity | undefined;
  return (
    <Card sx={{ mt: 2 }}>
      <Title title={translate('ra.page.dashboard')} />
      <CardContent>
        {me && (
          <Typography>
            {translate('bm.app.signed_in_as', {
              login: me.fullName,
              role: translate(`bm.role.${me.role}`),
            })}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}
