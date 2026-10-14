// Проверка сборки: «никаких внешних URL» (T002, 12_tasks.md; 10_constitution.md — работа без интернета).
// Запускается последним шагом `npm run build`. Exit 1 и список адресов, если найдено лишнее.
//
// Почему не `grep -rE "https?://" dist`: react-dom, MUI и react-router содержат адреса, которые ничего
// не загружают (XML-namespace, база для new URL, ссылки в текстах ошибок). Их нельзя убрать без правки
// чужого кода, поэтому в JS они разрешены ровно по списку ниже. В index.html и CSS не разрешено ничего.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Разрешённые адреса в JS: точное совпадение или префикс, заканчивающийся на «/» или «?». */
const ALLOWED_IN_JS = [
  // XML-namespace для createElementNS/setAttributeNS (react-dom). Сетевых запросов нет.
  'http://www.w3.org/2000/svg',
  'http://www.w3.org/1999/xlink',
  'http://www.w3.org/1998/Math/MathML',
  'http://www.w3.org/XML/1998/namespace',
  // База для new URL() при разборе относительных путей (react-router). Сетевых запросов нет.
  'http://localhost',
  // Ссылки в текстах ошибок и предупреждений (react, MUI, react-router). Сетевых запросов нет.
  'https://react.dev/errors/',
  'https://mui.com/production-error/?',
  'https://mui.com/system/display/',
  'https://reactrouter.com/en/main/routers/picking-a-router',
  // Телеметрия react-admin: код есть в ra-core, но выключен <Admin disableTelemetry>.
  // Это проверяет тест «не отправляет телеметрию react-admin» в src/__tests__/App.test.tsx.
  'https://react-admin-telemetry.marmelab.com/react-admin-telemetry?',
];

// Абсолютные адреса с протоколом. Хвост — до кавычки, обратной кавычки, пробела, скобки или «<».
const ABSOLUTE = /https?:\/\/[^\s"'`()<>\\]+/g;
// Протокол-относительные ссылки на ресурсы в HTML (src/href) и CSS (url(), @import).
const PROTOCOL_RELATIVE = /(?:(?:src|href)\s*=\s*["']?|url\(\s*["']?|@import\s+["'])(\/\/[^\s"'`()<>]+)/g;

const trimTrailing = (url) => url.replace(/[.,;:]+$/, '');

function isAllowed(url) {
  return ALLOWED_IN_JS.some((allowed) => {
    if (url === allowed) return true;
    if (allowed.endsWith('/') || allowed.endsWith('?')) return url.startsWith(allowed);
    // Для адресов без «/» или «?» в конце допускаем только продолжение пути, а не другой хост.
    return url.startsWith(`${allowed}/`) || url.startsWith(`${allowed}?`) || url.startsWith(`${allowed}#`);
  });
}

/** Адреса из файла сборки, которых быть не должно. `file` — путь относительно dist (по расширению выбирается режим). */
export function findForbiddenUrls(file, text) {
  const isJs = /\.(m?js|cjs)$/.test(file);
  const found = [];
  for (const m of text.matchAll(ABSOLUTE)) {
    const url = trimTrailing(m[0]);
    if (!(isJs && isAllowed(url))) found.push(url);
  }
  if (!isJs) {
    for (const m of text.matchAll(PROTOCOL_RELATIVE)) found.push(m[1]);
  }
  return found;
}

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}

function main() {
  const dist = fileURLToPath(new URL('../dist', import.meta.url));
  const problems = [];
  for (const path of walk(dist)) {
    const file = relative(dist, path);
    for (const url of new Set(findForbiddenUrls(file, readFileSync(path, 'utf8')))) problems.push(`${file}: ${url}`);
  }
  if (problems.length > 0) {
    console.error('check-dist: в сборке есть внешние адреса, которых нет в списке разрешённых:');
    for (const p of problems) console.error(`  ${p}`);
    process.exit(1);
  }
  console.log('check-dist: внешних адресов нет (разрешённые namespace и тексты ошибок — см. scripts/check-dist.mjs).');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
