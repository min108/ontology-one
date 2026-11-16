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
    <header className={styles.bar}>
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
      {identity ?? <span className={styles.workspace}>{workspaceName}</span>}
      <div className={styles.actions}>
        {searchSlot ??
          (onSearch && (
            <Button
              autoInsertSpace={false}
              icon={<Search />}
              onClick={onSearch}
              aria-label={terms.actions.search}
            >
              {terms.actions.search}
            </Button>
          ))}
        {actions}
        {userMenu}
      </div>
    </header>
  );
}
