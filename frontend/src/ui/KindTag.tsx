import { Tag } from 'antd';
import { BookOpen, Building2, Database, FileText, GitBranch, Users } from 'lucide-react';
import { terms, type Kind } from '../i18n/zh-CN';
import styles from './KindTag.module.css';
export function KindTag({ kind }: { kind: Kind | string }) {
  const labels: Record<string, string> = terms.kinds;
  const Icon = ['organization', 'position'].includes(kind)
    ? Building2
    : ['person', 'party'].includes(kind)
      ? Users
      : kind === 'material'
        ? FileText
        : ['dataset', 'data', 'metric'].includes(kind)
          ? Database
          : ['link_type', 'process'].includes(kind)
            ? GitBranch
            : BookOpen;
  return (
    <Tag className={styles.tag} icon={<Icon aria-hidden="true" />}>
      {labels[kind] ?? terms.labels.unknown_kind}
    </Tag>
  );
}
