// Контраст пар цветов из 13_design.md §8 по значениям design/tokens.json, в обеих темах
// (14_tech_rulings.md, пакет 2, Ф1: вместо axe — формула WCAG 2.x без зависимостей).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contrastRatio, parseColor, relativeLuminance } from '../theme/contrast';

const tokens = JSON.parse(readFileSync(resolve(process.cwd(), '../../design/tokens.json'), 'utf8')) as {
  color: Record<'light' | 'dark', Record<string, string>>;
};

describe('формула WCAG 2.x', () => {
  it('чёрный на белом — 21:1, одинаковые цвета — 1:1, порядок не важен', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 5);
    expect(contrastRatio('#FFFFFF', '#000000')).toBeCloseTo(21, 5);
    expect(contrastRatio('#7D8985', '#7D8985')).toBe(1);
  });

  it('относительная яркость по WCAG: порог линейного участка 0.04045', () => {
    expect(relativeLuminance('#FFFFFF')).toBeCloseTo(1, 10);
    expect(relativeLuminance('#000000')).toBe(0);
    // Канал 10/255 = 0.0392 < 0.04045 — линейный участок: 0.0392 / 12.92 × 0.7152 (зелёный).
    expect(relativeLuminance('#000A00')).toBeCloseTo((10 / 255 / 12.92) * 0.7152, 10);
    // Эталон: серый #777777 на белом ≈ 4.48:1 (известное «чуть ниже AA»).
    expect(contrastRatio('#777777', '#FFFFFF')).toBeCloseTo(4.48, 2);
  });

  it('разбирает #RGB и #RRGGBB, остальное — ошибка (rgba с прозрачностью не сравнивается)', () => {
    expect(parseColor('#fff')).toEqual([255, 255, 255]);
    expect(parseColor('#2F55C8')).toEqual([47, 85, 200]);
    expect(() => parseColor('rgba(0, 0, 0, 0.6)')).toThrow();
  });
});

type Pair = [fg: string, bg: string, min: number];

/** Пары из 13_design.md §8. 4,5:1 — текст (WCAG 1.4.3 AA), 3:1 — рамка полей (1.4.11). */
const BOTH: Pair[] = [
  ['text', 'surface', 4.5],
  ['text', 'bg', 4.5],
  ['text-muted', 'surface', 4.5],
  ['text-muted', 'bg', 4.5],
  ['primary', 'surface', 4.5],
  ['danger', 'surface', 4.5],
  ['warning', 'surface', 4.5],
  ['success', 'surface', 4.5],
  ['src-import', 'surface', 4.5],
  ['src-subscription', 'surface', 4.5],
  ['src-token', 'surface', 4.5],
  // Текст на кнопке primary: светлая — белый, тёмная — text-on-accent (§8).
  ['text-on-accent', 'primary', 4.5],
  ['border-strong', 'surface', 3],
];
/** Мягкие подложки чипов и плашек: в §8 указаны для светлой темы. */
const LIGHT_ONLY: Pair[] = [
  ['primary', 'primary-soft', 4.5],
  ['danger', 'danger-soft', 4.5],
  ['warning', 'warning-soft', 4.5],
];

describe('контраст токенов дизайна (13_design.md §8)', () => {
  const cases = [
    ...(['light', 'dark'] as const).flatMap((theme) => BOTH.map((p) => [theme, ...p] as const)),
    ...LIGHT_ONLY.map((p) => ['light', ...p] as const),
  ];

  it.each(cases)('%s: %s на %s ≥ %s:1', (theme, fg, bg, min) => {
    const palette = tokens.color[theme];
    expect(palette[fg], `${theme}.${fg}`).toBeDefined();
    expect(palette[bg], `${theme}.${bg}`).toBeDefined();
    const ratio = contrastRatio(palette[fg]!, palette[bg]!);
    expect(ratio, `${theme}: ${fg} ${palette[fg]} на ${bg} ${palette[bg]} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
      min,
    );
  });

  it('в проверку попали все пары таблицы §8: 13 пар в двух темах и 3 подложки светлой темы', () => {
    expect(cases).toHaveLength(29);
  });
});
