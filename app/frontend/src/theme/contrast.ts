// Контраст цветов по формуле WCAG 2.x (relative luminance, contrast ratio), без зависимостей.
// Используется тестом contrast.test.ts для пар из 13_design.md §8 (14_tech_rulings.md, пакет 2, Ф1).

export type Rgb = [r: number, g: number, b: number];

/** «#RGB» или «#RRGGBB» → [r, g, b] 0–255. Цвета с прозрачностью не поддерживаются: их контраст зависит от фона. */
export function parseColor(hex: string): Rgb {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`Цвет «${hex}» не в формате #RGB или #RRGGBB`);
  const h = m[1]!.length === 3 ? [...m[1]!].map((c) => c + c).join('') : m[1]!;
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb;
}

/** Линеаризация канала sRGB (WCAG 2.x, порог 0.04045). */
const linear = (channel: number) => {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};

/** Относительная яркость: 0.2126 R + 0.7152 G + 0.0722 B. */
export function relativeLuminance(color: string): number {
  const [r, g, b] = parseColor(color);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

/** Отношение контраста (L1 + 0.05) / (L2 + 0.05), L1 — светлее. От 1 до 21. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}
