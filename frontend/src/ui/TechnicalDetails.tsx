import type { ReactNode } from 'react';
import { useUiStore } from '../state/ui';
import { terms } from '../i18n/zh-CN';
import { FoldRow } from './FoldRow';
import styles from './Foundation.module.css';
export function TechnicalDetails({ children }: { children: ReactNode }) {
  const professional = useUiStore((state) => state.viewMode === 'professional');
  return professional ? (
    <div data-technical-details className={styles.technical}>
      <FoldRow title={terms.labels.r3_ui_technical} summary={terms.labels.r3_ui_technical_summary}>
        {children}
      </FoldRow>
    </div>
  ) : null;
}
