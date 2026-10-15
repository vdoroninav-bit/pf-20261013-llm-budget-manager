// OneTimeSecretDialog (13_design.md §6, screens.md §9): ключ показывается один раз.
// Закрывается только кнопкой «Готово» после флажка «Я сохранил ключ»; Esc и клик по фону не закрывают (onClose пустой).
// Ключ живёт только в состоянии этого диалога и исчезает при закрытии (13_design.md §10).
import { useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
} from '@mui/material';
import { useNotify } from 'react-admin';
import { ru } from '../i18n/ru';

const t = ru.bm.callers;

export interface OneTimeSecretDialogProps {
  title: string;
  secret: string;
  onDone: () => void;
}

export function OneTimeSecretDialog({ title, secret, onDone }: OneTimeSecretDialogProps) {
  const [saved, setSaved] = useState(false);
  const notify = useNotify();
  return (
    <Dialog open onClose={() => undefined} aria-labelledby="bm-secret-title" maxWidth="sm" fullWidth>
      <DialogTitle id="bm-secret-title">{title}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Box
            component="code"
            lang="en"
            translate="no"
            sx={{
              fontFamily: 'var(--bm-font-mono, monospace)',
              p: 1,
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              wordBreak: 'break-all',
              userSelect: 'all',
            }}
          >
            {secret}
          </Box>
          <Button
            onClick={async () => {
              try {
                await navigator.clipboard?.writeText(secret);
                notify(t.copied, { type: 'info' });
              } catch {
                // Буфер обмена недоступен (например, не HTTPS) — ключ можно выделить и скопировать вручную.
              }
            }}
          >
            {t.copy}
          </Button>
        </Box>
        <FormControlLabel
          sx={{ mt: 2 }}
          control={<Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)} />}
          label={t.saved_checkbox}
        />
      </DialogContent>
      <DialogActions>
        <Button variant="contained" disabled={!saved} onClick={onDone}>
          {t.done}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
