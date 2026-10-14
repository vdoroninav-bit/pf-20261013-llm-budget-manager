import { describe, expect, it } from 'vitest';
import tokens from '../../../../design/tokens.json';
import { tokensToCss } from '../theme/cssVars';
import { createBmTheme } from '../theme/muiTheme';
import { createThemeStore, readThemePreference, THEME_STORAGE_KEY } from '../theme/themeStore';

/** Вытаскивает объявления из блока CSS по селектору. */
const block = (css: string, selector: string): Record<string, string> => {
  const start = css.indexOf(`${selector} {`);
  expect(start, `нет блока ${selector}`).toBeGreaterThanOrEqual(0);
  const body = css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start));
  return Object.fromEntries(
    body
      .split(';')
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => [d.slice(0, d.indexOf(':')).trim(), d.slice(d.indexOf(':') + 1).trim()]),
  );
};

describe('tokens.json → CSS-переменные', () => {
  const css = tokensToCss(tokens);

  it('цвета светлой темы на :root, тёмной — в [data-theme="dark"], по правилу --bm-color-<ключ>', () => {
    const root = block(css, ':root');
    const dark = block(css, ':root[data-theme="dark"]');
    expect(root['--bm-color-bg']).toBe('#F1F4F2');
    expect(dark['--bm-color-bg']).toBe('#161C21');
    expect(root['--bm-color-overlay']).toBe('rgba(23, 34, 43, 0.48)');
    for (const key of Object.keys(tokens.color.light)) expect(root).toHaveProperty(`--bm-color-${key}`);
    for (const key of Object.keys(tokens.color.dark)) expect(dark).toHaveProperty(`--bm-color-${key}`);
  });

  it('прочие группы — --bm-<группа>-<ключ>; служебные $-поля и muiThemeMap не выводятся', () => {
    const root = block(css, ':root');
    expect(root['--bm-space-4']).toBe('16px');
    expect(root['--bm-font-sans']).toBe(tokens.font.sans);
    expect(root['--bm-radius-md']).toBe('6px');
    expect(root['--bm-size-meter-height']).toBe('20px');
    expect(root['--bm-motion-duration-base']).toBe('200ms');
    expect(root['--bm-pattern-reservation-hatch']).toContain('var(--bm-color-src-reservation)');
    // Имена групп в kebab-case: fontSize → font-size.
    expect(root['--bm-font-size-sm']).toBe('13px');
    expect(root['--bm-line-height-normal']).toBe('1.45');
    expect(root['--bm-font-weight-semibold']).toBe('600');
    expect(Object.keys(root).some((k) => k.includes('$') || k.toLowerCase().includes('mui'))).toBe(false);
  });

  it('роли текста разворачиваются в готовые значения', () => {
    const root = block(css, ':root');
    expect(root['--bm-type-remaining-figure-size']).toBe('32px');
    expect(root['--bm-type-remaining-figure-weight']).toBe('600');
    expect(root['--bm-type-remaining-figure-line-height']).toBe('1.2');
    expect(root['--bm-type-money-cell-size']).toBe('13px');
  });

  it('тени зависят от темы', () => {
    expect(block(css, ':root')['--bm-shadow-dialog']).toBe(tokens.shadow.light.dialog);
    expect(block(css, ':root[data-theme="dark"]')['--bm-shadow-dialog']).toBe(tokens.shadow.dark.dialog);
  });

  it('prefers-reduced-motion обнуляет длительности', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*:root \{[^}]*--bm-motion-duration-base: 0ms/);
  });

  it('в CSS нет внешних адресов', () => {
    expect(css).not.toMatch(/https?:\/\//);
  });
});

describe('MUI-тема из tokens.json (muiThemeMap)', () => {
  it.each(['light', 'dark'] as const)('%s: палитра, шрифт, радиус', (mode) => {
    const t = createBmTheme(mode);
    const c = tokens.color[mode];
    expect(t.palette?.mode).toBe(mode);
    expect(t.palette?.primary).toMatchObject({ main: c.primary, contrastText: c['text-on-accent'] });
    expect(t.palette?.error).toMatchObject({ main: c.danger });
    expect(t.palette?.warning).toMatchObject({ main: c.warning });
    expect(t.palette?.success).toMatchObject({ main: c.success });
    expect(t.palette?.background).toEqual({ default: c.bg, paper: c.surface });
    expect(t.palette?.text).toMatchObject({ primary: c.text, secondary: c['text-muted'] });
    expect(t.palette?.divider).toBe(c.border);
    expect(t.typography).toMatchObject({ fontFamily: tokens.font.sans, fontSize: 14 });
    expect(t.shape).toEqual({ borderRadius: 3 });
  });

  it('панели — рамка вместо тени, кнопки без капслока и без тени', () => {
    const t = createBmTheme('light');
    expect(t.components?.MuiPaper?.defaultProps?.elevation).toBe(0);
    expect(JSON.stringify(t.components?.MuiPaper?.styleOverrides)).toContain(`1px solid ${tokens.color.light.border}`);
    expect(t.components?.MuiButton?.defaultProps?.disableElevation).toBe(true);
    expect(JSON.stringify(t.components?.MuiButton?.styleOverrides)).toContain('"textTransform":"none"');
  });

  it('в теме нет внешних шрифтов и адресов', () => {
    expect(JSON.stringify(createBmTheme('light'))).not.toMatch(/https?:\/\/|@font-face|Roboto"/);
  });
});

describe('выбор темы: localStorage bm_theme и data-theme на <html>', () => {
  const memStorage = () => {
    const m = new Map<string, string>();
    return {
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
      keys: () => [...m.keys()],
    };
  };

  it('без сохранённого выбора тема не задана (решает prefers-color-scheme)', () => {
    expect(readThemePreference(memStorage())).toBeUndefined();
  });

  it('сохранённый выбор читается, мусор игнорируется', () => {
    const s = memStorage();
    s.setItem(THEME_STORAGE_KEY, 'dark');
    expect(readThemePreference(s)).toBe('dark');
    s.setItem(THEME_STORAGE_KEY, 'purple');
    expect(readThemePreference(s)).toBeUndefined();
  });

  it('стор react-admin в памяти; в localStorage пишется только bm_theme', () => {
    const s = memStorage();
    const store = createThemeStore(s);
    store.setup();
    store.setItem('theme', 'dark');
    store.setItem('departments.listParams', { page: 2 });
    expect(store.getItem('theme')).toBe('dark');
    expect(store.getItem('departments.listParams')).toEqual({ page: 2 });
    expect(s.keys()).toEqual([THEME_STORAGE_KEY]);
    expect(s.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('стор начинается с сохранённой темы', () => {
    const s = memStorage();
    s.setItem(THEME_STORAGE_KEY, 'light');
    const store = createThemeStore(s);
    store.setup();
    expect(store.getItem('theme')).toBe('light');
  });
});
