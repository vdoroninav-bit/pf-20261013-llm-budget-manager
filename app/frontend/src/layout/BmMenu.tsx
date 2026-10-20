// Меню по ролям (13_design.md §4): порядок фиксирован, недоступные роли пункты не показываются.
// Видимость решается той же таблицей прав, что x-roles контракта (access/permissions.ts).
import { Box } from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import MemoryIcon from '@mui/icons-material/Memory';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import KeyIcon from '@mui/icons-material/Key';
import PeopleIcon from '@mui/icons-material/People';
import CardMembershipIcon from '@mui/icons-material/CardMembership';
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import AssessmentIcon from '@mui/icons-material/Assessment';
import HistoryIcon from '@mui/icons-material/History';
import { Menu, useGetIdentity } from 'react-admin';
import { can, canSeeImportScreen } from '../access/permissions';
import type { BmIdentity } from '../authProvider';
import { ru } from '../i18n/ru';

const t = ru.bm.menu;

export function BmMenu() {
  const { identity } = useGetIdentity();
  const me = identity as BmIdentity | undefined;
  const role = me?.role;
  const ownDepartment = role === 'budget_owner' && me?.departmentId;
  return (
    <Box component="nav" aria-label={t.label}>
      <Menu>
        <Menu.Item to="/" primaryText={t.dashboard} leftIcon={<DashboardIcon />} />
        {can(role, 'listDepartments') && (
          <Menu.Item
            to={ownDepartment ? `/departments/${me.departmentId}/show` : '/departments'}
            primaryText={role === 'budget_owner' ? t.my_department : t.departments}
            leftIcon={<AccountTreeIcon />}
          />
        )}
        {can(role, 'listModels') && <Menu.Item to="/models" primaryText={t.models} leftIcon={<MemoryIcon />} />}
        {can(role, 'listSubscriptions') && (
          <Menu.Item to="/subscriptions" primaryText={t.subscriptions} leftIcon={<CardMembershipIcon />} />
        )}
        {can(role, 'importOrg') && (
          <Menu.Item to="/org-import" primaryText={t.org_import} leftIcon={<UploadFileIcon />} />
        )}
        {canSeeImportScreen(role) && (
          <Menu.Item to="/imports" primaryText={t.imports} leftIcon={<CloudDownloadIcon />} />
        )}
        {can(role, 'getSpendReport') && <Menu.Item to="/reports" primaryText={t.reports} leftIcon={<AssessmentIcon />} />}
        {can(role, 'listCallers') && <Menu.Item to="/callers" primaryText={t.callers} leftIcon={<KeyIcon />} />}
        {can(role, 'listAdminUsers') && (
          <Menu.Item to="/admin-users" primaryText={t.admin_users} leftIcon={<PeopleIcon />} />
        )}
        {can(role, 'listEvents') && <Menu.Item to="/events" primaryText={t.journals} leftIcon={<HistoryIcon />} />}
      </Menu>
    </Box>
  );
}
