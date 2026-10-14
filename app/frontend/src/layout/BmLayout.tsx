import type { ReactNode } from 'react';
import { Layout } from 'react-admin';
import { ThemeAttributeSync } from './ThemeAttributeSync';

export function BmLayout({ children }: { children: ReactNode }) {
  return (
    <Layout>
      <ThemeAttributeSync />
      {children}
    </Layout>
  );
}
