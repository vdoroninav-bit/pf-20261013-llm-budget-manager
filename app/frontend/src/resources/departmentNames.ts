// Справочник подразделений для подписей и выбора (listDepartments, до 200 записей — максимум page_size).
import { useGetList } from 'react-admin';
import type { Schemas } from '../api/types';

export function useDepartmentNames(): Map<string, string> {
  const { data } = useGetList<Schemas['Department']>('departments', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'name', order: 'ASC' },
  });
  return new Map((data ?? []).map((d) => [d.id, d.name]));
}
