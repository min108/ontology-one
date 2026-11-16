import { Button, Input } from 'antd';
import { Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { terms } from '../i18n/zh-CN';
import styles from './FilterBar.module.css';
export function FilterBar({
  value,
  onChange,
  children,
  onReset,
}: {
  value: string;
  onChange: (value: string) => void;
  children?: ReactNode;
  onReset?: () => void;
}) {
  return (
    <div role="search" className={styles.bar}>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        prefix={<Search />}
        aria-label={terms.labels.filter}
        placeholder={terms.labels.filter_placeholder}
        allowClear
      />
      {children}
      {onReset && (
        <Button autoInsertSpace={false} onClick={onReset}>
          {terms.actions.reset}
        </Button>
      )}
    </div>
  );
}
