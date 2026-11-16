import { terms } from '../i18n/zh-CN';
import styles from './IdentityBar.module.css';
export interface IdentityBarProps {
  organization: string;
  role: string;
  scope: string;
}
export function IdentityBar({ organization, role, scope }: IdentityBarProps) {
  return (
    <dl className={styles.identity}>
      {[
        [terms.labels.organization, organization],
        [terms.labels.role, role],
        [terms.labels.scope, scope],
      ].map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
