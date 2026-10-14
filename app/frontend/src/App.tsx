// Корень админки: react-admin (MIT-ядро), русский словарь, тема из design/tokens.json.
// Маршруты — hash (/admin/#/...), поэтому бэкенду достаточно отдавать index.html и ассеты из /admin/.
import { useMemo } from 'react';
import { Admin, CustomRoutes } from 'react-admin';
import { Route } from 'react-router-dom';
import { createAuthProvider, PASSWORD_ROUTE } from './authProvider';
import { createDataProvider } from './dataProvider';
import { createHttpClient, type FetchLike } from './api/http';
import { createSessionStore } from './api/session';
import { i18nProvider } from './i18n';
import { ru } from './i18n/ru';
import { BmLayout } from './layout/BmLayout';
import { Dashboard } from './pages/Dashboard';
import { LoginPage } from './pages/LoginPage';
import { PasswordPage } from './pages/PasswordPage';
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
    >
      {/* Без <Resource>/<CustomRoutes> ra-core считает админку пустой и вместо входа и панели
          рисует заглушку «Welcome to React-admin». Ресурсы меню добавляются в T043/T052/T064/T072. */}
      <CustomRoutes>
        <Route path={PASSWORD_ROUTE} element={<PasswordPage />} />
      </CustomRoutes>
    </Admin>
  );
}
