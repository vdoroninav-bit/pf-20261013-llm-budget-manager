// Vite-плагин: design/tokens.json → статический CSS с переменными --bm-* (виртуальный модуль).
// CSS собирается в файл ассета, а не вставляется <style> из JS: так проще CSP и кэш.
import type { Plugin } from 'vite';
import tokens from '../../../design/tokens.json' with { type: 'json' };
import { tokensToCss } from '../src/theme/cssVars.ts';

const PUBLIC_ID = 'virtual:bm-tokens.css';
const RESOLVED_ID = '\0' + PUBLIC_ID;

export function bmTokensCss(): Plugin {
  return {
    name: 'bm-tokens-css',
    resolveId(id) {
      return id === PUBLIC_ID ? RESOLVED_ID : undefined;
    },
    load(id) {
      return id === RESOLVED_ID ? tokensToCss(tokens) : undefined;
    },
  };
}
