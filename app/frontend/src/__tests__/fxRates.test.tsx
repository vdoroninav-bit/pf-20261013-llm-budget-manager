// @vitest-environment jsdom
// Экран 5.4 «Курсы USD/₽» (design/screens.md §5.4): listFxRates, putFxRate; listSubscriptions — признак USD-позиций.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { fail, list, ok } from './support/contractApi';
import { fxRate, subscription } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

const NN = ' ';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T12:00:00+03:00'));
});
afterEach(() => {
  vi.useRealTimers();
  resetApp();
});

const putEcho = ({ pathParams, body }: { pathParams: Record<string, string>; body: { usd_rub: string } }) =>
  ok(fxRate({ month: pathParams.month!, usd_rub: body.usd_rub, entered_at: '2026-10-14T12:00:00+03:00' }));

describe('5.4 Курсы USD/₽', () => {
  it('список: месяц словами, курс с 4 знаками, кто и когда; маршрут #/fx-rates', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'admin',
      handlers: {
        listFxRates: list([fxRate({ month: '2026-09', usd_rub: '91.2' }), fxRate()]),
        listSubscriptions: list([]),
      },
    });
    expect(await screen.findByText('Октябрь 2026')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(['Месяц', 'Курс', 'Ввёл', 'Когда']);
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]!).getByText('Октябрь 2026')).toBeTruthy();
    expect(within(rows[1]!).getByText('Сентябрь 2026')).toBeTruthy();
    expect(within(rows[0]!).getByText(`92,5000${NN}₽ за 1${NN}$`)).toBeTruthy();
    expect(within(rows[1]!).getByText(`91,2000${NN}₽ за 1${NN}$`)).toBeTruthy();
    expect(within(rows[0]!).getByText('01.10.2026, 10:00 МСК')).toBeTruthy();
    expect(api.callsOf('listFxRates')[0]!.query).toEqual({});
    const tabs = screen.getByRole('navigation', { name: 'Подписки и курсы' });
    expect(within(tabs).getByRole('link', { name: 'Курсы USD/₽' }).getAttribute('aria-current')).toBe('page');
    expect(screen.queryByText(/Нет курса на октябрь 2026/)).toBeNull();
    expect(api.violations).toEqual([]);
  });

  it('«Ввести курс» за новый месяц → putFxRate без подтверждения', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'admin',
      handlers: { listFxRates: list([fxRate()]), listSubscriptions: list([]), putFxRate: putEcho },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Ввести курс' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Месяц/), { target: { value: '2026-11' } });
    fireEvent.change(within(dialog).getByLabelText(/^Курс USD\/₽/), { target: { value: '93,1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить курс' }));
    await waitFor(() => expect(api.callsOf('putFxRate')).toHaveLength(1));
    expect(api.callsOf('putFxRate')[0]!.path).toBe('/api/v1/fx-rates/2026-11');
    expect(api.callsOf('putFxRate')[0]!.body).toEqual({ usd_rub: '93.1' });
    expect(await screen.findByText('Курс сохранён')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('курс за месяц уже есть — диалог замены, затем putFxRate', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'admin',
      handlers: { listFxRates: list([fxRate()]), listSubscriptions: list([]), putFxRate: putEcho },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Ввести курс' }));
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText(/^Месяц/) as HTMLInputElement).value).toBe('2026-10');
    fireEvent.change(within(dialog).getByLabelText(/^Курс USD\/₽/), { target: { value: '95' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить курс' }));
    expect(
      await screen.findByText(
        'Заменить курс за октябрь 2026? Уже списанные суммы не пересчитаются, новый курс применится к ещё не обработанным данным месяца.',
      ),
    ).toBeTruthy();
    expect(api.callsOf('putFxRate')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Заменить курс' }));
    await waitFor(() => expect(api.callsOf('putFxRate')).toHaveLength(1));
    expect(api.callsOf('putFxRate')[0]!.body).toEqual({ usd_rub: '95' });
    expect(api.violations).toEqual([]);
  });

  it('неверный курс — ошибка под полем, запрос не уходит', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'admin',
      handlers: { listFxRates: list([]), listSubscriptions: list([]) },
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Ввести курс' }))[0]!);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Курс USD\/₽/), { target: { value: '92,12345' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить курс' }));
    expect(await within(dialog).findByText('Введите курс, например 92,5 (до 4 знаков после запятой)')).toBeTruthy();
    expect(api.callsOf('putFxRate')).toHaveLength(0);
  });

  it('422 от сервера — текст в диалоге', async () => {
    renderApp('#/fx-rates', {
      role: 'admin',
      handlers: {
        listFxRates: list([]),
        listSubscriptions: list([]),
        putFxRate: fail(422, 'validation_error', 'Курс должен быть больше нуля'),
      },
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Ввести курс' }))[0]!);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Курс USD\/₽/), { target: { value: '0' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить курс' }));
    expect(await within(dialog).findByText('Курс должен быть больше нуля')).toBeTruthy();
  });

  it('пусто — «Курсы не введены…»', async () => {
    renderApp('#/fx-rates', { role: 'admin', handlers: { listFxRates: list([]), listSubscriptions: list([]) } });
    expect(await screen.findByText('Курсы не введены. Без курса цены и подписки в USD не списываются.')).toBeTruthy();
  });

  it('нет курса на текущий месяц, а подписки в USD есть — предупреждение с прошлым месяцем', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'admin',
      handlers: {
        listFxRates: list([fxRate({ month: '2026-08' }), fxRate({ month: '2026-09' })]),
        listSubscriptions: list([subscription({ currency: 'USD', seat_price: '30', fx_warning: 'last_known_rate_used' })]),
      },
    });
    expect(await screen.findByText('Нет курса на октябрь 2026. Используется курс сентября.')).toBeTruthy();
    expect(api.callsOf('listSubscriptions')[0]!.query).toEqual({ active_on: '2026-10-14', page: '1', page_size: '200' });
    expect(api.violations).toEqual([]);
  });

  it('наблюдатель-финансист: чтение без «Ввести курс»', async () => {
    const { api } = renderApp('#/fx-rates', {
      role: 'finance_viewer',
      handlers: { listFxRates: list([fxRate()]), listSubscriptions: list([]) },
    });
    expect(await screen.findByText('Октябрь 2026')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Ввести курс' })).toBeNull();
    expect(api.violations).toEqual([]);
  });
});
