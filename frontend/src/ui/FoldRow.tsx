import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import styles from './Foundation.module.css';
export function FoldRow({
  title,
  summary,
  children,
  defaultOpen = false,
}: {
  title: string;
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className={styles.fold} open={defaultOpen || undefined}>
      <summary>
        <strong>{title}</strong>
        <span className={styles.caption}>{summary}</span>
        <ChevronDown aria-hidden="true" />
      </summary>
      <div className={styles['fold-body']}>{children}</div>
    </details>
  );
}
