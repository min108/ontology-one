import { Breadcrumb, Button } from 'antd';
import type { ReactNode } from 'react';
import type { Action } from './types';
import styles from './PageHeader.module.css';
export interface PageHeaderProps {
  title: string;
  breadcrumbs?: { title: ReactNode; href?: string }[];
  status?: ReactNode;
  primaryAction?: Action;
}
export function PageHeader({ title, breadcrumbs, status, primaryAction }: PageHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        {breadcrumbs && <Breadcrumb items={breadcrumbs} />}
        <div className={styles.title}>
          <h1>{title}</h1>
          {status}
        </div>
      </div>
      {primaryAction && (
        <Button
          autoInsertSpace={false}
          type="primary"
          onClick={primaryAction.onClick}
          disabled={primaryAction.disabled}
        >
          {primaryAction.label}
        </Button>
      )}
    </header>
  );
}
