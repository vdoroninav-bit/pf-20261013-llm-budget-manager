// @vitest-environment jsdom
// Экран 6 «Загрузка выгрузки групп AD со сводкой» (design/screens.md §6): importOrg dry_run=true, затем false.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { fail, ok } from './support/contractApi';
import { orgSummary } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';
import { ORG_FILE_MAX_BYTES } from '../config/ui';

afterEach(resetApp);

const csv = (size?: number) => {
  const text = 'login;display_name;group;is_primary\nivanova;Иванова А. С.;AI-Marketing;1\n';
  const file = new File([text], 'groups.csv', { type: 'text/csv' });
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size });
  return file;
};

const choose = (file: File) =>
  fireEvent.change(screen.getByLabelText('Выберите файл', { selector: 'input' }), { target: { files: [file] } });

describe('6 Загрузка файла AD', () => {
  it('рендерит русские подписи; проверка dry_run=true и применение dry_run=false по контракту', async () => {
    const { api } = renderApp('#/org-import', {
      role: 'admin',
      handlers: {
        importOrg: ({ body }: { body: { dry_run?: string } }) =>
          ok(orgSummary({ dry_run: body.dry_run === 'true', employees_removed: 12 })),
      },
    });
    expect(await screen.findByRole('heading', { name: 'Загрузка файла AD' })).toBeTruthy();
    expect(screen.getByText(/Перетащите CSV-файл сюда или/)).toBeTruthy();
    expect(screen.getByText(/Кодировка UTF-8, разделитель — точка с запятой/)).toBeTruthy();
    choose(csv());

    expect(await screen.findByText('Сводка проверки')).toBeTruthy();
    const first = api.callsOf('importOrg')[0]!;
    expect((first.body as Record<string, unknown>).dry_run).toBe('true');
    expect((first.body as Record<string, unknown>).file).toBeInstanceOf(File);
    expect(first.headers['x-csrf-token']).toBe('csrf-test');
    const summary = screen.getByRole('region', { name: 'Сводка проверки' });
    expect(summary.getAttribute('aria-live')).toBe('polite');
    for (const label of [
      'Строк в файле',
      'Будет применено',
      'Новых подразделений',
      'Новых сотрудников',
      'Изменено',
      'Переведено в другое подразделение',
      'Будет выведено из подразделений (нет в файле)',
    ]) {
      expect(within(summary).getByText(label), label).toBeTruthy();
    }
    expect(within(summary).getByText('Пустой логин')).toBeTruthy();
    expect(within(summary).getByText('Нет отметки основной группы — выбрана первая по порядку')).toBeTruthy();
    expect(within(summary).getByText('Строки с ошибками не применяются, остальные применяются.')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Применить изменения' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        '12 сотрудников будут выведены из подразделений, потому что их нет в файле. Их расход за текущий период сохранится. Продолжить?',
      ),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Применить' }));
    expect(await screen.findByRole('heading', { name: 'Изменения применены' })).toBeTruthy();
    expect((api.callsOf('importOrg')[1]!.body as Record<string, unknown>).dry_run).toBe('false');
    expect(screen.getByRole('link', { name: 'К подразделениям' })).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('файл больше 10 МиБ — ошибка до отправки', async () => {
    const { api } = renderApp('#/org-import', { role: 'admin', handlers: {} });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv(ORG_FILE_MAX_BYTES + 1));
    expect(
      await screen.findByText('Файл больше 10 МБ. Выгрузка групп AD обычно гораздо меньше — проверьте, что выбран нужный файл.'),
    ).toBeTruthy();
    expect(api.callsOf('importOrg')).toHaveLength(0);
  });

  it('413 file_too_large с сервера — тот же текст', async () => {
    renderApp('#/org-import', { role: 'admin', handlers: { importOrg: fail(413, 'file_too_large') } });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv());
    expect(await screen.findByText(/Файл больше 10 МБ/)).toBeTruthy();
  });

  it('422 без detail — подсказка про заголовок и кодировку', async () => {
    renderApp('#/org-import', { role: 'admin', handlers: { importOrg: fail(422, 'validation_error') } });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv());
    expect(await screen.findByText(/Файл не похож на выгрузку групп/)).toBeTruthy();
  });

  it('все строки с ошибками — «Применить» выключена', async () => {
    renderApp('#/org-import', { role: 'admin', handlers: { importOrg: ok(orgSummary({ rows_applied: 0 })) } });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv());
    expect(await screen.findByText('Применять нечего — исправьте файл и загрузите снова.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Применить изменения' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('сеть пропала при применении — текст о неизвестном результате', async () => {
    let n = 0;
    renderApp('#/org-import', {
      role: 'admin',
      handlers: {
        importOrg: () => {
          n += 1;
          if (n === 2) throw new Error('network');
          return ok(orgSummary());
        },
      },
    });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv());
    fireEvent.click(await screen.findByRole('button', { name: 'Применить изменения' }));
    expect(await screen.findByText(/Неизвестно, успели ли они сохраниться/)).toBeTruthy();
  });

  it('повтор без изменений — «Изменений нет, оргструктура совпадает с файлом.»', async () => {
    renderApp('#/org-import', {
      role: 'admin',
      handlers: {
        importOrg: ok(
          orgSummary({
            departments_added: 0,
            employees_added: 0,
            employees_updated: 0,
            employees_moved: 0,
            employees_removed: 0,
            errors: [],
            warnings: [],
          }),
        ),
      },
    });
    await screen.findByRole('heading', { name: 'Загрузка файла AD' });
    choose(csv());
    expect(await screen.findByText('Изменений нет, оргструктура совпадает с файлом.')).toBeTruthy();
  });

  it('не администратору экран недоступен', async () => {
    const { api } = renderApp('#/org-import', { role: 'finance_viewer', handlers: {} });
    expect(
      await screen.findByText('Этот раздел недоступен для вашей роли. Попытка записана в журнал аудита.'),
    ).toBeTruthy();
    await waitFor(() => expect(api.callsOf('importOrg')).toHaveLength(0));
  });
});
