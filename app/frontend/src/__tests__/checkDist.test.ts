// Проверка сборки на внешние URL (T002: «никаких внешних URL в сборке»).
// Дословный `grep -rE "https?://" dist` невыполним: react-dom и MUI содержат XML-namespace и ссылки
// в текстах ошибок. Поэтому check-dist разрешает только перечисленные строки, которые ничего не загружают.
import { describe, expect, it } from 'vitest';
// @ts-expect-error — .mjs-скрипт сборки без деклараций типов
import { findForbiddenUrls } from '../../scripts/check-dist.mjs';

describe('check-dist', () => {
  it('пропускает XML-namespace и ссылки из текстов ошибок в JS', () => {
    const js =
      'createElementNS(`http://www.w3.org/2000/svg`);var t=`https://react.dev/errors/`+e;new URL(`http://localhost`)';
    expect(findForbiddenUrls('assets/index.js', js)).toEqual([]);
  });

  it('ловит внешние шрифты, CDN и любые другие адреса', () => {
    const js = 'import(`https://cdn.example.com/x.js`);fetch("http://api.example.org/v1")';
    expect(findForbiddenUrls('assets/index.js', js)).toEqual([
      'https://cdn.example.com/x.js',
      'http://api.example.org/v1',
    ]);
  });

  it('не принимает префикс разрешённого адреса как разрешение для другого хоста', () => {
    expect(findForbiddenUrls('a.js', '`http://www.w3.org.evil.example/2000/svg`')).toEqual([
      'http://www.w3.org.evil.example/2000/svg',
    ]);
  });

  it('в index.html и CSS не допускает ни одного адреса', () => {
    expect(findForbiddenUrls('index.html', '<link href="https://fonts.googleapis.com/css">')).toEqual([
      'https://fonts.googleapis.com/css',
    ]);
    expect(findForbiddenUrls('assets/index.css', 'svg{x:url(http://www.w3.org/2000/svg)}')).toEqual([
      'http://www.w3.org/2000/svg',
    ]);
  });

  it('ловит протокол-относительные ссылки на ресурсы в HTML и CSS', () => {
    expect(findForbiddenUrls('index.html', '<script src="//cdn.example.com/a.js"></script>')).toEqual([
      '//cdn.example.com/a.js',
    ]);
    expect(findForbiddenUrls('assets/index.css', '@font-face{src:url(//fonts.example.com/f.woff2)}')).toEqual([
      '//fonts.example.com/f.woff2',
    ]);
  });
});
