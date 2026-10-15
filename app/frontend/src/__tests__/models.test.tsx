// @vitest-environment jsdom
// Экран 4 «Каталог моделей и история цен» (design/screens.md §4): listModels, getModel, createModel, updateModel,
// listModelPrices, createModelPrice.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { fail, list, ok } from './support/contractApi';
import { model, price } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

// testing-library сравнивает строку с текстом узла после нормализации пробелов: U+202F там уже обычный пробел.
const NN = ' ';

describe('4.1 Список моделей', () => {
  it('рендерит русские подписи и отправляет listModels с сортировкой по контракту', async () => {
    const { api } = renderApp('#/models', {
      role: 'admin',
      handlers: {
        listModels: list([
          model(),
          model({
            id: 'text-embed',
            kind: 'embedding',
            active: false,
            supports_tools: false,
            current_price: price({ model_id: 'text-embed', currency: 'USD', input_per_1k: '0.0001' }),
          }),
        ]),
      },
    });
    expect(await screen.findByText('gigachat-pro')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual([
      'Модель',
      'Провайдер',
      'Тип',
      'Вход за 1 000 токенов',
      'Выход за 1 000',
      'Инструменты',
      'JSON',
      'Контекст',
      'Порядок',
      'Статус',
    ]);
    const row = screen.getByText('gigachat-pro').closest('tr')!;
    expect(within(row).getByText('Чат')).toBeTruthy();
    expect(within(row).getByText(`0,75${NN}₽`)).toBeTruthy();
    expect(within(row).getByText(`128${NN}000`)).toBeTruthy();
    expect(within(row).getByText('Включена')).toBeTruthy();
    const row2 = screen.getByText('text-embed').closest('tr')!;
    expect(within(row2).getByText('Эмбеддинги')).toBeTruthy();
    expect(within(row2).getByText(`0,0001${NN}$`)).toBeTruthy();
    expect(within(row2).getByText('Выключена')).toBeTruthy();
    expect(api.callsOf('listModels')[0]!.query).toEqual({ page: '1', page_size: '50', sort: 'id', order: 'asc' });

    fireEvent.click(screen.getByRole('button', { name: /Сортировать: порядок/ }));
    await waitFor(() => expect(api.callsOf('listModels').at(-1)!.query.sort).toBe('tie_order'));
    expect(api.violations).toEqual([]);
  });

  it('пусто: администратор — подсказка и «Добавить модель»; финансист — без действия', async () => {
    renderApp('#/models', { role: 'admin', handlers: { listModels: list([]) } });
    expect(await screen.findByText(/Каталог пуст. Добавьте модели, которые вызывают ваши системы/)).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Добавить модель' }).length).toBeGreaterThan(0);
  });

  it('пусто у финансиста', async () => {
    renderApp('#/models', { role: 'finance_viewer', handlers: { listModels: list([]) } });
    expect(await screen.findByText('Каталог пуст. Модели добавляет администратор.')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Добавить модель' })).toBeNull();
  });
});

describe('4.2 Добавление модели', () => {
  const fill = () => {
    fireEvent.change(screen.getByLabelText(/Идентификатор модели/), { target: { value: 'claude-sonnet' } });
    fireEvent.change(screen.getByLabelText(/Провайдер/), { target: { value: 'anthropic' } });
    fireEvent.change(screen.getByLabelText(/Максимальный контекст, токенов/), { target: { value: '200000' } });
    fireEvent.change(screen.getByLabelText(/Цена за 1 000 входных токенов/), { target: { value: '0,003' } });
    fireEvent.change(screen.getByLabelText(/Цена за 1 000 выходных токенов/), { target: { value: '0,015' } });
    fireEvent.click(screen.getByLabelText('USD'));
  };

  it('рендерит русские подписи и отправляет createModel по контракту', async () => {
    const { api } = renderApp('#/models/create', {
      role: 'admin',
      handlers: {
        createModel: ({ body }: { body: { id: string } }) =>
          ok(model({ id: body.id, current_price: price({ model_id: body.id }) }), 201),
        getModel: ok(model({ id: 'claude-sonnet' })),
        listModelPrices: list([price()]),
      },
    });
    expect(await screen.findByText('Точно так, как его передаёт вызывающая система')).toBeTruthy();
    expect(screen.getByText('Эмбеддинги никогда не перенаправляются на другую модель')).toBeTruthy();
    expect(screen.getByLabelText('Поддерживает инструменты')).toBeTruthy();
    expect(screen.getByLabelText('Поддерживает JSON-режим')).toBeTruthy();
    expect((screen.getByLabelText(/Порядок при равной цене/) as HTMLInputElement).value).toBe('100');
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить модель' }));
    await waitFor(() => expect(api.callsOf('createModel')).toHaveLength(1));
    expect(api.callsOf('createModel')[0]!.body).toEqual({
      id: 'claude-sonnet',
      provider: 'anthropic',
      kind: 'chat',
      supports_tools: false,
      supports_json: false,
      max_context: 200000,
      default_input_tokens: 1000,
      default_output_tokens: 500,
      tie_order: 100,
      price: { currency: 'USD', input_per_1k: '0.003', output_per_1k: '0.015' },
    });
    expect(await screen.findByText('Модель добавлена')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('422 fx_rate_missing — текст про курс и ссылка «Ввести курс»', async () => {
    renderApp('#/models/create', { role: 'admin', handlers: { createModel: fail(422, 'fx_rate_missing') } });
    await screen.findByLabelText(/Идентификатор модели/);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить модель' }));
    expect(
      await screen.findByText('Для цены в USD нужен курс USD/₽. Введите курс хотя бы за один месяц.'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Ввести курс' })).toBeTruthy();
  });

  it('409 conflict — «Модель с таким идентификатором уже есть.»', async () => {
    renderApp('#/models/create', { role: 'admin', handlers: { createModel: fail(409, 'conflict') } });
    await screen.findByLabelText(/Идентификатор модели/);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить модель' }));
    expect(await screen.findByText('Модель с таким идентификатором уже есть.')).toBeTruthy();
  });
});

describe('4.2–4.3 Карточка модели и история цен', () => {
  it('выключение через диалог; updateModel только с active=false', async () => {
    const { api } = renderApp('#/models/gigachat-pro', {
      role: 'admin',
      handlers: {
        getModel: ok(model()),
        updateModel: ({ body }: { body: object }) => ok({ ...model(), ...body }),
        listModelPrices: list([price()]),
      },
    });
    const toggle = await screen.findByLabelText('Модель включена');
    fireEvent.click(toggle);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Выключить модель gigachat-pro?')).toBeTruthy();
    expect(within(dialog).getByText(/Новые запросы к ней получат отказ «Модели нет в каталоге»/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Выключить модель' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((screen.getByLabelText('Модель включена') as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(api.callsOf('updateModel')).toHaveLength(1));
    expect(api.callsOf('updateModel')[0]!.body).toEqual({ active: false });
    expect(api.violations).toEqual([]);
  });

  it('история цен: listModelPrices, чип «Действует»; «Новая цена» → createModelPrice по контракту', async () => {
    const old = price({ id: '55555555-5555-4555-8555-555555555555', valid_from: '2026-09-01T00:00:00+03:00', input_per_1k: '0.50' });
    const { api } = renderApp('#/models/gigachat-pro/1', {
      role: 'admin',
      handlers: {
        getModel: ok(model()),
        listModelPrices: list([price(), old]),
        createModelPrice: ({ body }: { body: object }) => ok({ ...price(), ...body, id: '66666666-6666-4666-8666-666666666666' }, 201),
      },
    });
    expect(
      await screen.findByText('Новая цена применяется к решениям после даты начала. Уже списанные суммы не пересчитываются.'),
    ).toBeTruthy();
    expect(await screen.findByText(`0,50${NN}₽`)).toBeTruthy();
    expect(screen.getByText('Действует')).toBeTruthy();
    expect(screen.getByText('01.10.2026, 00:00 МСК')).toBeTruthy();
    expect(api.callsOf('listModelPrices')[0]!.path).toBe('/api/v1/models/gigachat-pro/prices');

    fireEvent.click(screen.getByRole('button', { name: 'Новая цена' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Цена за 1 000 входных токенов/), { target: { value: '0,8' } });
    fireEvent.change(within(dialog).getByLabelText(/Цена за 1 000 выходных токенов/), { target: { value: '1,6' } });
    fireEvent.change(within(dialog).getByLabelText(/Действует с/), { target: { value: '2026-11-01T00:00' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить цену' }));
    await waitFor(() => expect(api.callsOf('createModelPrice')).toHaveLength(1));
    expect(api.callsOf('createModelPrice')[0]!.body).toEqual({
      currency: 'RUB',
      input_per_1k: '0.8',
      output_per_1k: '1.6',
      valid_from: '2026-11-01T00:00:00+03:00',
    });
    expect(api.violations).toEqual([]);
  });

  it('финансист: карточка только для чтения, без «Новая цена»', async () => {
    const { api } = renderApp('#/models/gigachat-pro/show', {
      role: 'finance_viewer',
      handlers: { getModel: ok(model()), listModelPrices: list([price()]) },
    });
    expect(await screen.findByText('gigachat')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Новая цена' })).toBeNull();
    expect(screen.queryByLabelText('Модель включена')).toBeNull();
    expect(api.violations).toEqual([]);
  });
});
