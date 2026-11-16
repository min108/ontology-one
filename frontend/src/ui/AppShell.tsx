import type { ReactNode } from 'react';
import { DesignProvider } from '../design/DesignProvider';
import styles from './AppShell.module.css';
export interface AppShellProps {
  navigation: ReactNode;
  topBar: ReactNode;
  identity?: ReactNode;
  children: ReactNode;
}
export function AppShell({ navigation, topBar, identity, children }: AppShellProps) {
  return (
    <DesignProvider>
      <div className={styles.shell}>
        <aside className={styles.navigation}>{navigation}</aside>
        <div className={styles.body}>
          {topBar}
          {identity}
          <main className={styles.main}>{children}</main>
        </div>
      </div>
    </DesignProvider>
  );
}
