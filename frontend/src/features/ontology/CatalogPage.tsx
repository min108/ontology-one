import { Input, Pagination, Select } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { terms } from '../../i18n/zh-CN';
import { PageHeader } from '../../ui/PageHeader';
import { StatusTag } from '../../ui/StatusTag';
import { ResourceState, useResource } from '../admin/shared';
import { useBuildIdentity } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
export const assetKinds = ['data', 'rule', 'skill', 'knowledge'] as const;
interface Item {
  id: string;
  title: string;
  summary: string;
  kind: string;
  status: string;
}
export default function CatalogPage() {
  const identity = useBuildIdentity();
  return <Catalog key={identity.key} />;
}
function Catalog() {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get('q') || '');
  const kind = params.get('kind') || '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const query = useResource<{ data: { items: Item[]; total: number } }>(
    '/v2/elements?' + new URLSearchParams({ q: params.get('q') || '', kind, page: String(page) }),
  );
  function filter(q: string, value: string, nextPage = 1) {
    setParams(new URLSearchParams({ q, kind: value, page: String(nextPage) }));
  }
  const labels: Record<string, string> = terms.kinds;
  return (
    <div className={styles.page}>
      <PageHeader title={copy.r2_catalog_title} />
      <p>{copy.r2_catalog_help}</p>
      <div className={styles.actions}>
        <Input.Search
          aria-label={copy.r2_catalog_search}
          placeholder={copy.r2_catalog_search}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onSearch={(q) => filter(q, kind)}
        />
        <Select
          aria-label={copy.r2_catalog_all}
          value={kind}
          onChange={(value) => filter(params.get('q') || '', value)}
          options={[
            { value: '', label: copy.r2_catalog_all },
            ...assetKinds.map((value) => ({ value, label: labels[value] })),
          ]}
        />
      </div>
      {!query.data ? (
        <ResourceState error={query.error} reload={query.reload} />
      ) : (
        <>
          {!query.data.data.items.length && <p>{copy.r2_catalog_empty}</p>}
          {query.data.data.items.map((item) => (
            <article key={item.id} className={styles.card}>
              <p>
                {labels[item.kind]} · {item.id} <StatusTag status={item.status} />
              </p>
              <h2>
                <Link to={'/ontology/elements/' + encodeURIComponent(item.id)}>{item.title}</Link>
              </h2>
              <p>{item.summary}</p>
            </article>
          ))}
          <Pagination
            current={page}
            total={query.data.data.total}
            pageSize={25}
            showSizeChanger={false}
            onChange={(value) => filter(params.get('q') || '', kind, value)}
          />
        </>
      )}
    </div>
  );
}
