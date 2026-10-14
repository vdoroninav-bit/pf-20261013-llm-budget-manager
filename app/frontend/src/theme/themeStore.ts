// Стор react-admin держится в памяти: в браузере не остаётся ничего, кроме выбора темы под ключом bm_theme
// (13_design.md §10). Без сохранённого выбора тема берётся из prefers-color-scheme.
import { memoryStore, type Store } from 'ra-core';
import type { ThemeMode } from './muiTheme';

export const THEME_STORAGE_KEY = 'bm_theme';
/** Ключ, под которым react-admin (useTheme) хранит режим темы. */
const RA_THEME_KEY = 'theme';

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const isMode = (v: unknown): v is ThemeMode => v === 'light' || v === 'dark';

/** Безопасно для режимов, где хранилище недоступно (приватный режим, запрет cookie). */
export function readThemePreference(storage: KeyValueStorage | undefined): ThemeMode | undefined {
  try {
    const v = storage?.getItem(THEME_STORAGE_KEY);
    return isMode(v) ? v : undefined;
  } catch {
    return undefined;
  }
}

export function createThemeStore(storage: KeyValueStorage | undefined): Store {
  const saved = readThemePreference(storage);
  const store = memoryStore(saved ? { [RA_THEME_KEY]: saved } : {});
  const setItem = store.setItem.bind(store);
  return {
    ...store,
    setItem(key, value) {
      setItem(key, value);
      if (key === RA_THEME_KEY && isMode(value)) {
        try {
          storage?.setItem(THEME_STORAGE_KEY, value);
        } catch {
          // Хранилище недоступно — выбор действует до перезагрузки.
        }
      }
    },
  };
}

/** Режим темы дублируется атрибутом data-theme на <html> для CSS-переменных --bm-*. */
export function applyThemeAttribute(mode: ThemeMode, root: HTMLElement = document.documentElement) {
  root.dataset.theme = mode;
}
