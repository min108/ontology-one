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
}
export function TopBar({ workspaceName, onSearch, userMenu }: TopBarProps) {
  return (
    <header className={styles.bar}>
      <a href="/" className={styles.brand} title={brand.product_name_en}>
        {brand.product_name}
      </a>
      <span className={styles.workspace}>{workspaceName}</span>
      <div className={styles.actions}>
        {onSearch && (
          <Button
            autoInsertSpace={false}
            icon={<Search />}
            onClick={onSearch}
            aria-label={terms.actions.search}
          >
            {terms.actions.search}
          </Button>
        )}
        {userMenu}
      </div>
    </header>
  );
}
