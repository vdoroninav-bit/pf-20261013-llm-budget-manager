// Русский текст ошибки API: detail с сервера, иначе словарь по Problem.code (13_design.md §7.1).
import { ru } from '../i18n/ru';

type ProblemCode = keyof typeof ru.bm.problem;

export interface ProblemLike {
  status: number;
  code?: string;
  detail?: string;
}

export function problemText({ status, code, detail }: ProblemLike): string {
  if (detail && detail.trim()) return detail;
  if (code && Object.hasOwn(ru.bm.problem, code)) return ru.bm.problem[code as ProblemCode];
  if (status >= 500) return ru.bm.problem.server_error;
  return ru.bm.problem.unknown;
}
