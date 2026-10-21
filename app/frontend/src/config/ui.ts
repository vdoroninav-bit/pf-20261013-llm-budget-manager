// Константы интерфейса (13_design_rulings.md, В7): это решения техлида для UI, а не NFR.
// Держатся в одном модуле, чтобы экраны T064/T072 не заводили свои значения.

/** Через сколько показывать «Загрузка дольше обычного…» и кнопку «Повторить». Запрос не обрывается. */
export const SLOW_REQUEST_MS = 10_000;
/** Автообновление панели и счётчика колокольчика — только при видимой вкладке. */
export const DASHBOARD_REFRESH_MS = 60_000;
/** Опрос «Запусков» импорта — только пока есть строки queued или running. */
export const IMPORT_RUNS_POLL_MS = 10_000;
/** Задержка пересчёта предпросмотра подписки после изменения формы (design/screens.md, поток C). */
export const SUBSCRIPTION_PREVIEW_DEBOUNCE_MS = 400;
/** Максимальный размер файла AD (13_design_rulings.md, В6; 12_openapi.yaml importOrg). */
export const ORG_FILE_MAX_BYTES = 10 * 1024 * 1024;
/** Период журнала аудита по умолчанию — последние N суток по МСК, включая сегодня (14_tech_rulings.md, 3Ф5). */
export const AUDIT_DEFAULT_DAYS = 7;
