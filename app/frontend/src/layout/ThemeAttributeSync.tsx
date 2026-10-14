import { useEffect } from 'react';
import { useTheme } from 'react-admin';
import { applyThemeAttribute } from '../theme/themeStore';

/** Держит data-theme на <html> равным текущему режиму react-admin (для CSS-переменных --bm-*). */
export function ThemeAttributeSync() {
  const [mode] = useTheme();
  useEffect(() => {
    applyThemeAttribute(mode === 'dark' ? 'dark' : 'light');
  }, [mode]);
  return null;
}
