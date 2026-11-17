import { Input, Select } from 'antd';
import type { TableColumnsType } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import { PageHeader, PageLayout, DataTable, StatusTag, KindTag, StateBlock, formatDateTime } from '../../ui';
import { ResourceState, useResource } from '../admin/shared';
import { useBuildIdentity } from '../build/shared';
import { askHref, sentence } from '../build/model';
import styles from '../build/Build.module.css';

const copy = terms.labels;
export const assetKinds = ['data', 'rule', 'skill', 'knowledge'] as const;
interface Item {
  id: string;
  title: string;
  summary: string;
  kind: string;
  status: string;
  version?: number;
  updated_at?: string;
  source_title?: string;
}
export default function CatalogPage() {
  const identity = useBuildIdentity();
  return <Catalog key={identity.key} />;
}
function Catalog() {
  const [params, setParams] = useSearchParams();
  const professional = useUiStore((state) => state.viewMode === 'professional');
  const [text, setText] = useState(params.get('q') || '');
  const kind = params.get('kind') || '',
    page = Math.max(1, Number(params.get('page')) || 1);
  const query = useResource<{ data: { items: Item[]; total: number } }>(
    '/v2/elements?' + new URLSearchParams({ q: params.get('q') || '', kind, page: String(page) }),
  );
  function filter(q: string, value: string, nextPage = 1) {
    setParams(new URLSearchParams({ q, kind: value, page: String(nextPage) }));
  }
  const columns: TableColumnsType<Item> = [
    {
      key: 'name',
      title: copy.r3_main_catalog_name,
      render: (_, item) => <Link to={'/ontology/elements/' + encodeURIComponent(item.id)}>{item.title}</Link>,
    },
    {
      key: 'kind',
      title: copy.r3_main_catalog_kind,
      className: styles['kind-column'],
      render: (_, item) => <KindTag kind={item.kind} />,
    },
    {
      key: 'status',
      title: copy.r3_main_catalog_status,
      className: styles['status-column'],
      render: (_, item) => <StatusTag status={item.status} />,
    },
    {
      key: 'source',
      title: copy.r3_main_catalog_source,
      className: styles['source-column'],
      ellipsis: true,
      render: (_, item) => item.source_title || copy.r3_main_catalog_source_empty,
    },
    {
      key: 'updated',
      title: copy.r3_main_catalog_updated,
      className: styles['time-column'],
      render: (_, item) => (item.updated_at ? formatDateTime(item.updated_at) : copy.identity_not_recorded),
    },

    {
      key: 'actions',
      title: copy.r3_main_catalog_actions,
      className: styles['action-column'],
      render: (_, item) => (
        <div className={styles.actions}>
          <Link to={'/ontology/elements/' + encodeURIComponent(item.id)}>{copy.r3_main_catalog_open}</Link>
          {item.status === 'published' && <Link to={askHref(item.id)}>{copy.r3_main_ask}</Link>}
        </div>
      ),
    },
    {
      key: 'version',
      title: copy.r3_main_catalog_version,
      className: styles['status-column'],
      render: (_, item) => (item.version ? `v${item.version}` : copy.identity_not_recorded),
    },
  ];
  return (
    <PageLayout
      variant="list"
      header={
        <PageHeader
          title={copy.r2_catalog_title}
          lead={
            query.data ? sentence(copy.r3_main_catalog_total, { count: query.data.data.total }) : undefined
          }
        />
      }
    >
      <div className={styles.toolbar}>
        <Input.Search
          className={styles.search}
          aria-label={copy.r2_catalog_search}
          placeholder={copy.r3_main_catalog_search}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onSearch={(q) => filter(q, kind)}
        />
        <Select
          aria-label={copy.r2_catalog_all}
          value={kind}
          onChange={(value) => filter(params.get('q') || '', value)}
          options={[
            { value: '', label: copy.r2_catalog_all },
            ...assetKinds.map((value) => ({ value, label: terms.kinds[value] })),
          ]}
        />
      </div>
      {!query.data ? (
        <ResourceState error={query.error} reload={query.reload} />
      ) : !query.data.data.items.length ? (
        <StateBlock
          state="empty"
          description={copy.r3_main_catalog_none}
          action={{
            label: copy.r3_main_catalog_clear,
            onClick: () => {
              setText('');
              filter('', '');
            },
          }}
        />
      ) : (
        <DataTable<Item>
          rowKey="id"
          columns={columns}
          viewMode={professional ? 'professional' : 'simple'}
          dataSource={query.data.data.items}
          emptyAction={{
            label: copy.r3_main_catalog_clear,
            onClick: () => {
              setText('');
              filter('', '');
            },
          }}
          tableProps={{
            className: styles['catalog-table'],
            tableLayout: 'fixed',
            onRow: (_, index) =>
              ({
                'data-testid': index === 0 ? 'table-first-row' : undefined,
              }) as React.HTMLAttributes<HTMLElement>,
          }}
          pagination={{
            current: page,
            total: query.data.data.total,
            pageSize: 25,
            showSizeChanger: false,
            onChange: (value) => filter(params.get('q') || '', kind, value),
          }}
        />
      )}
    </PageLayout>
  );
}
