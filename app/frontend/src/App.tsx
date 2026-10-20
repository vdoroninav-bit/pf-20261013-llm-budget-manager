// Корень админки: react-admin (MIT-ядро), русский словарь, тема из design/tokens.json.
// Маршруты — hash (/admin/#/...), поэтому бэкенду достаточно отдавать index.html и ассеты из /admin/.
import { useMemo } from 'react';
import { Admin, CustomRoutes, Resource } from 'react-admin';
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
import { OrgImportPage } from './pages/OrgImport';
import { ImportStatusPage } from './pages/ImportStatus';
import { ProviderSourceList } from './resources/providerSources';
import { ImportRunList } from './resources/providerSources/ImportRunList';
import { ReportsPage } from './pages/Reports';
import { ReconciliationPage } from './pages/Reconciliation';
import { NotificationsPage } from './pages/Notifications';
import { EventsPage } from './resources/events';
import { AuditPage } from './resources/audit';
import { can } from './access/permissions';
import type { Role } from './api/types';
import { DepartmentCreate, DepartmentEdit, DepartmentList, DepartmentShow } from './resources/departments';
import { EmployeeCard } from './resources/employees';
import { ModelCreate, ModelEdit, ModelList, ModelShow } from './resources/models';
import { CallerCreate, CallerEdit, CallerList } from './resources/callers';
import { SubscriptionCreate, SubscriptionList, SubscriptionShow } from './resources/subscriptions';
import { FxRateList } from './resources/fxRates';
import { AdminUserCreate, AdminUserEdit, AdminUserList } from './resources/adminUsers';
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
      <CustomRoutes>
        <Route path={PASSWORD_ROUTE} element={<PasswordPage />} />
        <Route path="/org-import" element={<OrgImportPage />} />
        {/* Экран 7 (T064): вкладки «Состояние», «Источники», «Запуски»; права проверяет сам экран. */}
        <Route path="/dashboard/:department_id" element={<Dashboard />} />
        <Route path="/imports" element={<ImportStatusPage />} />
        <Route path="/imports/sources" element={<ProviderSourceList />} />
        <Route path="/imports/runs" element={<ImportRunList />} />
        {/* Экраны 8 и 11 (T072); права проверяет сам экран, прямая ссылка без прав — «Нет доступа». */}
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/reconciliation" element={<ReconciliationPage />} />
        <Route path="/events" element={<EventsPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
      </CustomRoutes>
      {(role: Role | null) => resourcesFor(role)}
    </Admin>
  );
}

/**
 * Ресурсы по роли: экраны и действия — только те, что разрешены x-roles операций (access/permissions.ts).
 * Названия ресурсов совпадают с картой api/resources.ts (dataProvider).
 */
function resourcesFor(role: Role | null) {
  return [
    <Resource
      key="departments"
      name="departments"
      list={DepartmentList}
      show={DepartmentShow}
      edit={can(role, 'updateDepartment') ? DepartmentEdit : undefined}
      create={can(role, 'createDepartment') ? DepartmentCreate : undefined}
      recordRepresentation="name"
    />,
    can(role, 'getEmployee') ? (
      <Resource key="employees" name="employees" show={EmployeeCard} edit={EmployeeCard} recordRepresentation="pseudonym" />
    ) : null,
    <Resource
      key="models"
      name="models"
      list={ModelList}
      show={ModelShow}
      edit={can(role, 'updateModel') ? ModelEdit : undefined}
      create={can(role, 'createModel') ? ModelCreate : undefined}
    />,
    can(role, 'listSubscriptions') ? (
      <Resource
        key="subscriptions"
        name="subscriptions"
        list={SubscriptionList}
        show={SubscriptionShow}
        // Маршрут карточки по screens.md §5 — /subscriptions/:id; редактирования у подписки нет, там та же карточка.
        edit={SubscriptionShow}
        create={can(role, 'createSubscription') ? SubscriptionCreate : undefined}
        recordRepresentation="plan"
      />
    ) : null,
    // Имя ресурса = маршрут экрана (screens.md §5: /admin/#/fx-rates).
    can(role, 'listFxRates') ? <Resource key="fx-rates" name="fx-rates" list={FxRateList} /> : null,
    can(role, 'listCallers') ? (
      <Resource key="callers" name="callers" list={CallerList} create={CallerCreate} edit={CallerEdit} recordRepresentation="name" />
    ) : null,
    // Экраны источников и запусков — маршруты /imports/* (T064); ресурсы нужны для кэша и выбора в форме ключа.
    can(role, 'listProviderSources') ? <Resource key="providerSources" name="providerSources" /> : null,
    can(role, 'listImportRuns') ? <Resource key="importRuns" name="importRuns" /> : null,
    can(role, 'listAdminUsers') ? (
      <Resource
        key="admin-users"
        name="admin-users"
        list={AdminUserList}
        create={AdminUserCreate}
        edit={AdminUserEdit}
        recordRepresentation="login"
      />
    ) : null,
  ];
}
