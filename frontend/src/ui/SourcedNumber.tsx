import { Popover } from 'antd';
import type { ReactNode } from 'react';
import { terms } from '../i18n/zh-CN';
import styles from './Foundation.module.css';
export interface NumberSource {
  source: string;
  time: string;
  caliber: string;
  version: string;
  owner: string;
}
export function SourcedNumber({ children, source }: { children: ReactNode; source?: NumberSource }) {
  if (!source || Object.values(source).some((value) => !value.trim()) || Object.keys(source).length !== 5)
    return <span>{children}</span>;
  const labels = [
    terms.labels.r3_ui_source_label,
    terms.labels.r3_ui_source_time,
    terms.labels.r3_ui_source_caliber,
    terms.labels.r3_ui_source_version,
    terms.labels.r3_ui_source_owner,
  ];
  return (
    <Popover
      trigger="click"
      content={
        <dl>
          {[source.source, source.time, source.caliber, source.version, source.owner].map((value, index) => (
            <div key={labels[index]}>
              <dt>{labels[index]}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      }
    >
      <button className={styles.sourced}>{children}</button>
    </Popover>
  );
}
