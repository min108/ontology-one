import { Button } from 'antd';
import { ChevronLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import type { Action } from './types';
import styles from './PageHeader.module.css';
export interface PageHeaderProps {
  title: string;
  breadcrumbs?: { title: ReactNode; href?: string }[];
  back?: { label: string; href: string };
  lead?: ReactNode;
  display?: ReactNode;
  note?: ReactNode;
  status?: ReactNode;
  primaryAction?: Action;
  secondaryAction?: Action;
  extra?: ReactNode;
}
export function PageHeader({
  title,
  breadcrumbs,
  back,
  lead,
  display,
  note,
  status,
  primaryAction,
  secondaryAction,
  extra,
}: PageHeaderProps) {
  const router = useInRouterContext();
  const previous =
    back ??
    (breadcrumbs?.length
      ? {
          label: breadcrumbs[breadcrumbs.length - 1].title,
          href: breadcrumbs[breadcrumbs.length - 1].href ?? '/',
        }
      : undefined);
  const backBody = previous && (
    <>
      <ChevronLeft aria-hidden="true" />
      {previous.label}
    </>
  );
  return (
    <header data-page-header className={styles.header}>
      {previous && (
        <nav className={styles.back} aria-label={String(previous.label)}>
          {router ? <Link to={previous.href}>{backBody}</Link> : <a href={previous.href}>{backBody}</a>}
        </nav>
      )}
      <div className={styles.row}>
        <div className={styles.title}>
          <h1 data-page-title title={title}>
            {title}
          </h1>
          {status}
        </div>
        <div className={styles.actions}>
          {note && <span className={styles.note}>{note}</span>}
          {extra}
          {secondaryAction && (
            <Button
              autoInsertSpace={false}
              onClick={secondaryAction.onClick}
              disabled={secondaryAction.disabled}
              loading={secondaryAction.loading}
            >
              {secondaryAction.label}
            </Button>
          )}
          {primaryAction && (
            <Button
              autoInsertSpace={false}
              type="primary"
              onClick={primaryAction.onClick}
              disabled={primaryAction.disabled}
              loading={primaryAction.loading}
            >
              {primaryAction.label}
            </Button>
          )}
        </div>
      </div>
      {display && <div className={styles.display}>{display}</div>}
      {lead && <p className={styles.lead}>{lead}</p>}
    </header>
  );
}
