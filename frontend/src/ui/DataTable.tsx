import { Button, Checkbox, Popover, Segmented, Table } from 'antd';
import type { TableColumnsType, TableProps } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import type { Key } from 'react';
import { terms } from '../i18n/zh-CN';
import { StateBlock } from './StateBlock';
import type { Action, ViewMode } from './types';
import styles from './DataTable.module.css';
const columnWidths: Record<string, string> = {
  kind: 'var(--zy-col-tag)',
  type: 'var(--zy-col-tag)',
  status: 'var(--zy-col-tag)',
  role: 'var(--zy-col-tag)',
  updated: 'var(--zy-col-time)',
  created: 'var(--zy-col-time)',
  time: 'var(--zy-col-time)',
  actions: 'var(--zy-col-time)',
  version: 'var(--zy-col-short)',
  count: 'var(--zy-col-short)',
  source: 'var(--zy-col-text)',
};
export interface DataTableProps<T extends object> {
  columns: TableColumnsType<T>;
  dataSource: T[];
  rowKey: string | ((record: T) => Key);
  viewMode?: ViewMode;
  urlKey?: string;
  emptyAction: Action;
  pagination?: TableProps<T>['pagination'];
  tableProps?: Omit<TableProps<T>, 'columns' | 'dataSource' | 'rowKey' | 'pagination'>;
}
export function DataTable<T extends object>({
  columns,
  dataSource,
  rowKey,
  viewMode = 'simple',
  urlKey = 'table',
  emptyAction,
  pagination,
  tableProps,
}: DataTableProps<T>) {
  const available = columns.map((column, index) => String(column.key ?? index));
  const defaults = viewMode === 'professional' ? available : available.slice(0, 6);
  const read = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const saved = params
      .get(`${urlKey}.columns`)
      ?.split(',')
      .filter((key) => available.includes(key));
    const allowed =
      viewMode === 'professional' ? saved : saved?.filter((key) => defaults.includes(key)).slice(0, 6);
    return {
      columns: allowed?.length ? allowed : defaults,
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
  const shown = columns
    .filter((_column, index) => settings.columns.includes(available[index]))
    .map((column) => ({ ...column, width: column.width ?? columnWidths[String(column.key)] }));
  const pageSettings =
    pagination === false
      ? false
      : {
          showTotal: (total: number, range: [number, number]) =>
            terms.labels.r3_ui_table_total
              .replace('{start}', String(range[0]))
              .replace('{end}', String(range[1]))
              .replace('{total}', String(total)),
          ...(pagination ?? {
            current: settings.page,
            pageSize: 10,
            hideOnSinglePage: true,
            onChange: (page: number) => update({ ...settings, page }),
          }),
        };
  if (!dataSource.length && !tableProps?.loading) return <StateBlock state="empty" action={emptyAction} />;
  return (
    <div className={styles.table}>
      {viewMode === 'professional' && (
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
      )}
      <Table<T>
        {...tableProps}
        columns={shown}
        dataSource={dataSource}
        rowKey={rowKey}
        size={viewMode === 'professional' && settings.compact ? 'small' : 'middle'}
        pagination={pageSettings}
        locale={{ emptyText: <StateBlock state="empty" action={emptyAction} /> }}
      />
    </div>
  );
}
