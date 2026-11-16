import { Tag } from 'antd';
import { CheckCircle2, CircleDashed, CircleX, Clock3 } from 'lucide-react';
import { terms, type StatusDomain } from '../i18n/zh-CN';
import styles from './StatusTag.module.css';
export function StatusTag({ domain = 'element', status }: { domain?: StatusDomain; status: string }) {
  const labels: Record<string, string> = terms.statuses[domain];
  const success = ['approved', 'published', 'completed', 'confirmed', 'pass', 'final'].includes(status);
  const danger = ['failed', 'returned', 'rejected', 'violate'].includes(status);
  const waiting = ['pending', 'review', 'queued', 'waiting_user', 'candidate'].includes(status);
  const Icon = success ? CheckCircle2 : danger ? CircleX : waiting ? Clock3 : CircleDashed;
  return (
    <Tag
      className={`${styles.tag} ${success ? styles.success : danger ? styles.danger : waiting ? styles.warning : styles.neutral}`}
      icon={<Icon aria-hidden="true" />}
    >
      {labels[status] ?? terms.labels.unknown_status}
    </Tag>
  );
}
