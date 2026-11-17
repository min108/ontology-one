import type { ReactNode } from 'react';
import { DesignProvider } from '../design/DesignProvider';
import styles from './AppShell.module.css';
export interface AppShellProps {
  navigation: ReactNode;
  topBar: ReactNode;
  identity?: ReactNode;
  children: ReactNode;
  assistant?: ReactNode;
  assistantOpen?: boolean;
  collapsed?: boolean;
}
export function AppShell({
  navigation,
  topBar,
  identity,
  children,
  assistant,
  assistantOpen = false,
  collapsed = false,
}: AppShellProps) {
  return (
    <DesignProvider>
      <div
        className={`${styles.shell} ${assistantOpen ? styles['with-assistant'] : ''}`}
        data-app-shell
        data-nav-collapsed={collapsed}
      >
        <div className={styles.top}>
          {topBar}
          {identity}
        </div>
        <aside className={styles.navigation}>{navigation}</aside>
        <main className={styles.main}>{children}</main>
        {assistant && (
          <aside className={styles.assistant} hidden={!assistantOpen} data-assistant-panel>
            {assistant}
          </aside>
        )}
      </div>
    </DesignProvider>
  );
}
