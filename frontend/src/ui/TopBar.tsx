import { Button } from 'antd';
import { Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { brand } from '../i18n/brand';
import { terms } from '../i18n/zh-CN';
import styles from './TopBar.module.css';
export interface TopBarProps {
  workspaceName: string;
  onSearch?: () => void;
  userMenu?: ReactNode;
  identity?: ReactNode;
  actions?: ReactNode;
  searchSlot?: ReactNode;
  collapsed?: boolean;
  onHome?: () => void;
}
export function TopBar({
  workspaceName,
  onSearch,
  userMenu,
  identity,
  actions,
  searchSlot,
  collapsed = false,
  onHome,
}: TopBarProps) {
  return (
    <header className={styles.bar} data-topbar data-collapsed={collapsed}>
      <a
        href="/"
        className={styles.brand}
        aria-label={brand.product_name}
        title={`${brand.product_name} · ${brand.product_name_en}`}
        data-navigation-root=""
        onClick={(event) => {
          if (
            onHome &&
            !event.defaultPrevented &&
            event.button === 0 &&
            !event.ctrlKey &&
            !event.metaKey &&
            !event.shiftKey &&
            !event.altKey
          ) {
            event.preventDefault();
            onHome();
          }
        }}
      >
        <img src="/citic-mark.svg" alt="" />
        <span hidden={collapsed}>{brand.product_name}</span>
      </a>
      <div className={styles.identity}>
        {identity ?? <span className={styles.workspace}>{workspaceName}</span>}
      </div>
      <div className={styles.actions}>
        {searchSlot ??
          (onSearch && (
            <Button
              autoInsertSpace={false}
              className={styles.search}
              icon={<Search aria-hidden="true" />}
              onClick={onSearch}
              aria-label={terms.labels.r3_ui_search}
            >
              {terms.labels.r3_ui_search}
            </Button>
          ))}
        {actions}
        {userMenu}
      </div>
    </header>
  );
}
