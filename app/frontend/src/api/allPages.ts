// Загрузка списка всеми страницами (14_tech_rulings.md, пакет 3, 3Ф2).
// Контракт ограничивает page_size до 200 (components/parameters/PageSize); обрезать выбор молча нельзя —
// поэтому страницы запрашиваются по очереди, пока не набран total.
import type { DataProvider, RaRecord } from 'react-admin';
import { MAX_PAGE_SIZE } from './resources';

export async function fetchAllPages<R extends RaRecord>(
  dp: DataProvider,
  resource: string,
  filter: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<R[]> {
  const rows: R[] = [];
  for (let page = 1; ; page += 1) {
    const { data, total } = await dp.getList<R>(resource, {
      filter,
      pagination: { page, perPage: MAX_PAGE_SIZE },
      sort: { field: '', order: 'ASC' },
      signal,
    });
    rows.push(...data);
    // Пустая страница раньше total (записи удалили между запросами) — стоп, иначе цикл без конца.
    if (data.length === 0 || total === undefined || rows.length >= total) return rows;
  }
}
