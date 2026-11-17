import { Button, Dropdown } from 'antd';
import { ChevronDown } from 'lucide-react';
import type { Action } from './types';
import { terms } from '../i18n/zh-CN';
import styles from './Foundation.module.css';
export function RowActions({ actions }: { actions: Action[] }) {
  const visible = actions.length > 2 ? actions.slice(0, 1) : actions;
  return (
    <span data-row-actions className={styles['row-actions']}>
      {visible.map((action) => (
        <Button
          key={action.label}
          size="small"
          type="text"
          onClick={action.onClick}
          disabled={action.disabled}
          loading={action.loading}
        >
          {action.label}
        </Button>
      ))}
      {actions.length > 2 && (
        <Dropdown
          trigger={['click']}
          menu={{
            items: actions.slice(1).map((action, index) => ({
              key: String(index),
              label: action.label,
              disabled: action.disabled,
              onClick: action.onClick,
            })),
          }}
        >
          <Button size="small" type="text">
            {terms.actions.r3_ui_more}
            <ChevronDown aria-hidden="true" />
          </Button>
        </Dropdown>
      )}
    </span>
  );
}
