import type { ReactNode } from 'react';
import { AppBar, Layout, Logout, UserMenu, MenuItemLink } from 'react-admin';
import LockResetIcon from '@mui/icons-material/LockReset';
import { BmMenu } from './BmMenu';
import { ThemeAttributeSync } from './ThemeAttributeSync';
import { PASSWORD_ROUTE } from '../authProvider';
import { ru } from '../i18n/ru';

/** Меню пользователя: «Сменить пароль» и «Выйти» (13_design.md §4). */
function BmUserMenu() {
  return (
    <UserMenu>
      <MenuItemLink to={PASSWORD_ROUTE} primaryText={ru.bm.menu.change_password} leftIcon={<LockResetIcon />} />
      <Logout />
    </UserMenu>
  );
}

const BmAppBar = () => <AppBar userMenu={<BmUserMenu />} />;

export function BmLayout({ children }: { children: ReactNode }) {
  return (
    <Layout menu={BmMenu} appBar={BmAppBar}>
      <ThemeAttributeSync />
      {children}
    </Layout>
  );
}
