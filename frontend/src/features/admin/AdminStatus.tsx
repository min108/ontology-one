import { Tag } from 'antd';
import { CheckCircle2, CircleDashed, CircleX, Clock3 } from 'lucide-react';
import styles from './Admin.module.css';
export function AdminStatus({ status, label }: { status: string; label: string }) {
  const Icon = ['active', 'success', 'current'].includes(status)
    ? CheckCircle2
    : ['failure', 'failed', 'denied', 'provision_failed'].includes(status)
      ? CircleX
      : status === 'provisioning'
        ? Clock3
        : CircleDashed;
  return (
    <Tag className={styles['status-tag']} icon={<Icon aria-hidden="true" />}>
      {label}
    </Tag>
  );
}
