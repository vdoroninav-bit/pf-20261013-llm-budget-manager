// Корень админки: react-admin (MIT-ядро), русский словарь, тема из design/tokens.json.
// Маршруты — hash (/admin/#/...), поэтому бэкенду достаточно отдавать index.html и ассеты из /admin/.
import { useMemo } from 'react';
import { Admin } from 'react-admin';
import { createAuthProvider } from './authProvider';
import { createDataProvider } from './dataProvider';
import { createHttpClient, type FetchLike } from './api/http';
import { createSessionStore } from './api/session';
import { i18nProvider } from './i18n';
import { ru } from './i18n/ru';
import { BmLayout } from './layout/BmLayout';
import { Dashboard } from './pages/Dashboard';
import { LoginPage } from './pages/LoginPage';
import { darkTheme, lightTheme } from './theme/muiTheme';
import { createThemeStore } from './theme/themeStore';

export interface AppProps {
  /** Для тестов: подмена fetch. В браузере — window.fetch. */
  fetch?: FetchLike;
}

const browserStorage = () => {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
};

export function App({ fetch = (input, init) => window.fetch(input, init) }: AppProps) {
  const { dataProvider, authProvider, store } = useMemo(() => {
    const session = createSessionStore();
    const http = createHttpClient({ fetch, session });
    return {
      dataProvider: createDataProvider(http),
      authProvider: createAuthProvider(http, session),
      store: createThemeStore(browserStorage()),
    };
  }, [fetch]);

  return (
    <Admin
      title={ru.bm.app.title}
      dataProvider={dataProvider}
      authProvider={authProvider}
      i18nProvider={i18nProvider}
      store={store}
      lightTheme={lightTheme}
      darkTheme={darkTheme}
      layout={BmLayout}
      loginPage={LoginPage}
      dashboard={Dashboard}
      requireAuth
      disableTelemetry
    />
  );
}
