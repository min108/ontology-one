import type { HTMLAttributes, ReactNode } from 'react';
import styles from './PageLayout.module.css';
export interface PageLayoutProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  header: ReactNode;
  variant?: 'list' | 'archive' | 'split' | 'workbench';
}
export function PageLayout({ header, variant = 'list', children, className = '', ...rest }: PageLayoutProps) {
  return (
    <section
      {...rest}
      data-page-template={variant}
      className={`${styles.page} ${styles[variant]} ${className}`}
    >
      {header}
      <div data-page-content className={styles.content}>
        {children}
      </div>
    </section>
  );
}
