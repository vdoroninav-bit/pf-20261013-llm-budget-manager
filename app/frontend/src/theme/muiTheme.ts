// MUI-тема из design/tokens.json по таблице muiThemeMap. Значения берутся из JSON, а не из CSS-переменных,
// чтобы MUI мог считать производные оттенки (tokens.json, $usage.mui). Шрифты только системные.
import { deepmerge } from '@mui/utils';
import { defaultDarkTheme, defaultLightTheme, type RaThemeOptions } from 'react-admin';
import tokens from '../../../../design/tokens.json';

export type ThemeMode = 'light' | 'dark';

const px = (v: string) => Number.parseInt(v, 10);

export function createBmTheme(mode: ThemeMode): RaThemeOptions {
  const c = tokens.color[mode];
  const base = mode === 'dark' ? defaultDarkTheme : defaultLightTheme;
  const own: RaThemeOptions = {
    palette: {
      mode,
      primary: { main: c.primary, dark: c['primary-hover'], contrastText: c['text-on-accent'] },
      secondary: { main: c.primary, contrastText: c['text-on-accent'] },
      error: { main: c.danger },
      warning: { main: c.warning },
      success: { main: c.success },
      background: { default: c.bg, paper: c.surface },
      text: { primary: c.text, secondary: c['text-muted'] },
      divider: c.border,
      action: { hover: c['row-hover'], selected: c['row-selected'] },
    },
    typography: {
      fontFamily: tokens.font.sans,
      fontSize: 14,
      h6: { fontWeight: px(tokens.fontWeight.semibold) },
    },
    shape: { borderRadius: px(tokens.radius.sm) },
    sidebar: { width: px(tokens.size.sidebar), closedWidth: px(tokens.size['sidebar-collapsed']) },
    components: {
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: { root: { border: `1px solid ${c.border}`, backgroundImage: 'none' } },
      },
      MuiAppBar: {
        defaultProps: { elevation: 0, color: 'default' },
        styleOverrides: {
          root: { backgroundColor: c.surface, color: c.text, border: 0, borderBottom: `1px solid ${c.border}` },
        },
      },
      MuiDrawer: { styleOverrides: { paper: { border: 0 } } },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: { root: { textTransform: 'none', minHeight: px(tokens.size['control-height']) } },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { borderRadius: px(tokens.radius.md), boxShadow: tokens.shadow[mode].dialog },
        },
      },
      MuiTableCell: {
        styleOverrides: { root: { fontSize: tokens.fontSize.sm } },
      },
      RaToolbar: {
        styleOverrides: { root: { backgroundColor: c['surface-sunken'] } },
      },
    },
  };
  // Палитру и типографику не смешиваем с базой react-admin: в ней свои фоны и вторичный цвет.
  const merged = deepmerge(base, own) as RaThemeOptions;
  return { ...merged, palette: own.palette, typography: { ...merged.typography, ...own.typography } };
}

export const lightTheme = createBmTheme('light');
export const darkTheme = createBmTheme('dark');
