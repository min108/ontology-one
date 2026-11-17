import { Button } from 'antd';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { EvidenceDrawer } from './EvidenceDrawer';
import { terms } from '../i18n/zh-CN';
import styles from './Foundation.module.css';
export function SplitView({
  list,
  children,
  aside,
  footer,
}: {
  list: ReactNode;
  children: ReactNode;
  aside?: ReactNode;
  footer?: ReactNode;
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  return (
    <div className={styles.splitWrap}>
      <div className={`${styles.split} ${aside ? styles.three : ''}`} data-split-view>
        <aside className={styles.pane} data-split-list>
          {list}
        </aside>
        <div className={styles.pane} data-split-main>
          {aside && (
            <div className={styles.mobileEvidence}>
              <Button onClick={() => setEvidenceOpen(true)}>{terms.actions.r3_ui_evidence}</Button>
            </div>
          )}
          {children}
        </div>
        {aside && (
          <aside className={`${styles.pane} ${styles.evidence}`} data-split-evidence>
            {aside}
          </aside>
        )}
      </div>
      {footer}
      {aside && (
        <EvidenceDrawer open={evidenceOpen} onClose={() => setEvidenceOpen(false)}>
          {aside}
        </EvidenceDrawer>
      )}
    </div>
  );
}
