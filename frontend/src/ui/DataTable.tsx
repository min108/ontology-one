import { Button, Checkbox, Popover, Segmented, Table } from 'antd';
import type { TableColumnsType } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import type { Key } from 'react';
import { terms } from '../i18n/zh-CN';
import { StateBlock } from './StateBlock';
import type { Action, ViewMode } from './types';
import styles from './DataTable.module.css';
export interface DataTableProps<T extends object> {
  columns: TableColumnsType<T>;
  dataSource: T[];
  rowKey: string | ((record: T) => Key);
  viewMode?: ViewMode;
  urlKey?: string;
  emptyAction: Action;
}
export function DataTable<T extends object>({
  columns,
  dataSource,
  rowKey,
  viewMode = 'simple',
  urlKey = 'table',
  emptyAction,
}: DataTableProps<T>) {
  const available = columns.map((column, index) => String(column.key ?? index));
  const defaults = available.slice(0, 6);
  const read = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const saved = params
      .get(`${urlKey}.columns`)
      ?.split(',')
      .filter((key) => available.includes(key));
    return {
      columns: saved?.length ? saved : defaults,
      compact: viewMode === 'professional' && params.get(`${urlKey}.density`) === 'compact',
      page: Math.max(1, Number(params.get(`${urlKey}.page`)) || 1),
    };
    // Column identity determines allowed URL values; a new column array recomputes the settings.
  }, [available.join(','), viewMode, urlKey]);
  const [settings, setSettings] = useState(read);
  useEffect(() => {
    const restore = () => setSettings(read());
    restore();
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [read]);
  function update(next: typeof settings) {
    const url = new URL(window.location.href);
    url.searchParams.set(`${urlKey}.columns`, next.columns.join(','));
    url.searchParams.set(`${urlKey}.density`, next.compact ? 'compact' : 'comfortable');
    url.searchParams.set(`${urlKey}.page`, String(next.page));
    window.history.pushState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setSettings(next);
  }
  const shown = columns.filter((_column, index) => settings.columns.includes(available[index]));
  return (
    <div className={styles.table}>
      <div className={styles.controls}>
        <Popover
          trigger="click"
          content={
            <Checkbox.Group
              value={settings.columns}
              onChange={(values) => {
                if (values.length) update({ ...settings, columns: values.map(String) });
              }}
              options={columns.map((column, index) => ({
                label: typeof column.title === 'string' ? column.title : available[index],
                value: available[index],
              }))}
            />
          }
        >
          <Button>{terms.actions.columns}</Button>
        </Popover>
        {viewMode === 'professional' && (
          <Segmented
            aria-label={terms.labels.density}
            options={[
              { label: terms.actions.comfortable, value: false },
              { label: terms.actions.compact, value: true },
            ]}
            value={settings.compact}
            onChange={(compact) => update({ ...settings, compact })}
          />
        )}
      </div>
      <Table<T>
        columns={shown}
        dataSource={dataSource}
        rowKey={rowKey}
        size={viewMode === 'professional' && settings.compact ? 'small' : 'middle'}
        pagination={{
          current: settings.page,
          pageSize: 10,
          hideOnSinglePage: true,
          onChange: (page) => update({ ...settings, page }),
        }}
        locale={{ emptyText: <StateBlock state="empty" action={emptyAction} /> }}
      />
    </div>
  );
}
