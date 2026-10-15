// Рендер всей админки с фейковым API по контракту на нужном hash-маршруте.
import { cleanup, render } from '@testing-library/react';
import { App } from '../../App';
import { contractApi, type ContractApiOptions } from './contractApi';

export function renderApp(hash: string, options: ContractApiOptions) {
  window.location.hash = hash;
  const api = contractApi(options);
  const view = render(<App fetch={api.fetch} />);
  return { api, ...view };
}

export function resetApp() {
  cleanup();
  window.location.hash = '';
  document.documentElement.removeAttribute('data-theme');
  window.localStorage.clear();
}
