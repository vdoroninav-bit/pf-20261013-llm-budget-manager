// design/tokens.json → CSS-переменные --bm-<группа>-<ключ> (правило из tokens.json, $usage.cssVariables).
// Светлая тема — на :root, тёмная — на :root[data-theme="dark"]. Имена групп в kebab-case (fontSize → font-size).
// Не выводятся: служебные поля «$…», muiThemeMap (читается createBmTheme). typeRoles разворачиваются
// в --bm-type-<роль>-size|weight|line-height с готовыми значениями.

type Leaf = string | number;
type Tree = { [k: string]: Leaf | Tree };

export interface DesignTokens {
  color: { light: Record<string, string>; dark: Record<string, string> };
  shadow: { light: Record<string, string>; dark: Record<string, string> } & Record<string, unknown>;
  fontSize: Record<string, string>;
  fontWeight: Record<string, string>;
  lineHeight: Record<string, string>;
  typeRoles: Record<string, { size: string; weight: string; lineHeight: string; numeric?: boolean }>;
  motion: Record<string, string>;
  [group: string]: unknown;
}

const THEMED = new Set(['color', 'shadow']);
const SKIP = new Set(['muiThemeMap', 'typeRoles']);

const kebab = (s: string) => s.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();
const isService = (k: string) => k.startsWith('$');

function flatten(tree: Tree, prefix: string, out: Array<[string, string]>) {
  for (const [k, v] of Object.entries(tree)) {
    if (isService(k)) continue;
    const name = `${prefix}-${kebab(k)}`;
    if (v !== null && typeof v === 'object') flatten(v, name, out);
    else out.push([name, String(v)]);
  }
}

const decl = (vars: Array<[string, string]>) => vars.map(([k, v]) => `  ${k}: ${v};`).join('\n');

export function tokensToCss(input: unknown): string {
  const tokens = input as DesignTokens;
  const common: Array<[string, string]> = [];
  const light: Array<[string, string]> = [];
  const dark: Array<[string, string]> = [];

  for (const [group, value] of Object.entries(tokens)) {
    if (isService(group) || SKIP.has(group) || value === null || typeof value !== 'object') continue;
    const prefix = `--bm-${kebab(group)}`;
    if (THEMED.has(group)) {
      const themed = value as Record<string, Tree>;
      flatten(themed.light ?? {}, prefix, light);
      flatten(themed.dark ?? {}, prefix, dark);
    } else {
      flatten(value as Tree, prefix, common);
    }
  }

  for (const [role, spec] of Object.entries(tokens.typeRoles ?? {})) {
    if (isService(role)) continue;
    const base = `--bm-type-${kebab(role)}`;
    common.push([`${base}-size`, tokens.fontSize[spec.size] ?? spec.size]);
    common.push([`${base}-weight`, tokens.fontWeight[spec.weight] ?? spec.weight]);
    common.push([`${base}-line-height`, tokens.lineHeight[spec.lineHeight] ?? spec.lineHeight]);
  }

  const durations = Object.keys(tokens.motion ?? {}).filter((k) => k.startsWith('duration'));
  const reduced: Array<[string, string]> = durations.map((k) => [`--bm-motion-${kebab(k)}`, '0ms']);

  return [
    '/* Сгенерировано из design/tokens.json (build/tokensCssPlugin.ts). Не редактировать вручную. */',
    `:root {\n${decl([...common, ...light])}\n  color-scheme: light;\n}`,
    `:root[data-theme="dark"] {\n${decl(dark)}\n  color-scheme: dark;\n}`,
    `@media (prefers-reduced-motion: reduce) {\n  :root {\n${decl(reduced).replace(/^/gm, '  ')}\n  }\n}`,
    '',
  ].join('\n\n');
}
