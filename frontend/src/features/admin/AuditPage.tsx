import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Card, Input, Table, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { terms } from '../../i18n/zh-CN';
import { useSessionStore } from '../../state/session';
import { useUiStore } from '../../state/ui';
import { addressPage, useAddressState } from '../../app/addressState';
import { PageHeader } from '../../ui/PageHeader';
import { StateBlock } from '../../ui/StateBlock';
import { copy, action, Forbidden, QueryState, Timestamp, useIdentityQuery } from '../identity/common';
import type { AuditOutput } from '../identity/contracts';
import styles from '../identity/Identity.module.css';
type Row = {
  id: string;
  created_at: string;
  actor_username: string;
  actor_id: string;
  action: string;
  resource: string;
  tenant_id: string;
  outcome: string;
  request_id: string;
  details: unknown;
};
const filterKeys = ['actor_id', 'tenant_id', 'action', 'outcome', 'since', 'until'] as const;
function iso(value: string): string | null {
  if (!value) return '';
  const date = new Date(value + '+08:00');
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
export default function AuditPage() {
  const { session, permission_keys } = useSessionStore();
  if (!session?.user.is_superadmin || !permission_keys.includes('audit.read'))
    return (
      <section aria-label={copy.identity_audit}>
        <Forbidden headquarters />
      </section>
    );
  return <AuditRecords />;
}
function AuditRecords() {
  const { params, update } = useAddressState();
  const viewMode = useUiStore((state) => state.viewMode);
  const [form, setForm] = useState(
      () =>
        Object.fromEntries(filterKeys.map((key) => [key, params.get(key) || ''])) as Record<
          (typeof filterKeys)[number],
          string
        >,
    ),
    [error, setError] = useState('');
  const signature = filterKeys.map((key) => params.get(key) || '').join('|');
  useEffect(() => {
    setForm(Object.fromEntries(filterKeys.map((key) => [key, params.get(key) || ''])) as typeof form);
  }, [signature]);
  const page = addressPage(params.get('page')),
    queryParams = new URLSearchParams({ page: String(page), page_size: '30' });
  let valid = true;
  for (const key of filterKeys) {
    const value = params.get(key) || '';
    if (!value) continue;
    const normalized = key === 'since' || key === 'until' ? iso(value) : value;
    if (normalized === null) valid = false;
    else queryParams.set(key, normalized);
  }
  const query = useIdentityQuery<AuditOutput>('identity-audit', '/v2/audit?' + queryParams, valid);
  const rows: Row[] = (query.data?.items || []).map((item) => ({
    id: String(item.id || ''),
    created_at: String(item.created_at || ''),
    actor_username: String(item.actor_username || ''),
    actor_id: String(item.actor_id || ''),
    action: String(item.action || ''),
    resource: String(item.resource || ''),
    tenant_id: String(item.tenant_id || ''),
    outcome: String(item.outcome || ''),
    request_id: String(item.request_id || ''),
    details: item.details,
  }));
  const columns: TableColumnsType<Row> = [
    {
      key: 'time',
      title: copy.identity_audit_time,
      render: (_, row) => <Timestamp value={row.created_at} />,
    },
    {
      key: 'actor',
      title: copy.identity_audit_actor,
      dataIndex: 'actor_username',
      render: (value) => value || copy.identity_audit_anonymous,
    },
    {
      key: 'action',
      title: copy.identity_audit_action,
      dataIndex: 'action',
      render: (value: string) =>
        Object.hasOwn(terms.identity_audit_actions, value)
          ? terms.identity_audit_actions[value as keyof typeof terms.identity_audit_actions]
          : copy.identity_audit_other,
    },
    {
      key: 'resource',
      title: copy.identity_audit_resource,
      render: (_, row) => (
        <>
          {row.resource || '—'}
          <div>{row.tenant_id || '—'}</div>
        </>
      ),
    },
    {
      key: 'outcome',
      title: copy.identity_audit_outcome,
      render: (_, row) => (
        <Tag>
          {Object.hasOwn(terms.identity_audit_outcomes, row.outcome)
            ? terms.identity_audit_outcomes[row.outcome as keyof typeof terms.identity_audit_outcomes]
            : row.outcome}
        </Tag>
      ),
    },
    {
      key: 'details',
      title: copy.identity_audit_details,
      render: (_, row) => (
        <details>
          <summary>{action.identity_details}</summary>
          <p>
            {copy.identity_audit_raw_action}: <code>{row.action}</code>
          </p>
          <p>
            {copy.identity_audit_raw_outcome}: <code>{row.outcome}</code>
          </p>
          <p>
            {copy.identity_audit_actor_id}: <code>{row.actor_id || '—'}</code>
          </p>
          <p>
            {copy.identity_audit_tenant_id}: <code>{row.tenant_id || '—'}</code>
          </p>
          <pre className={styles.detail}>{JSON.stringify(row.details, null, 2)}</pre>
          <p>
            {copy.identity_request_id}: <code data-visual-dynamic="request-id">{row.request_id}</code>
          </p>
        </details>
      ),
    },
  ];
  function filter(event: FormEvent) {
    event.preventDefault();
    const since = iso(form.since),
      until = iso(form.until);
    if (since === null || until === null || (since && until && since > until)) {
      setError(copy.identity_audit_time_invalid);
      return;
    }
    setError('');
    update({ ...Object.fromEntries(filterKeys.map((key) => [key, form[key].trim() || null])), page: null });
  }
  const reset = () => {
    setError('');
    setForm({ actor_id: '', tenant_id: '', action: '', outcome: '', since: '', until: '' });
    update({ ...Object.fromEntries(filterKeys.map((key) => [key, null])), page: null });
  };
  return (
    <section aria-label={copy.identity_audit} aria-busy={query.isPending && valid} className={styles.page}>
      <PageHeader title={copy.identity_audit} />
      <p className={styles.help}>{copy.identity_audit_exact}</p>
      <Card>
        <form className={styles.filters} onSubmit={filter}>
          {filterKeys.map((key) => (
            <label className={styles.field} htmlFor={'identity-audit-' + key} key={key}>
              {
                {
                  actor_id: copy.identity_audit_actor_id,
                  tenant_id: copy.identity_audit_tenant_id,
                  action: copy.identity_audit_raw_action,
                  outcome: copy.identity_audit_raw_outcome,
                  since: copy.identity_audit_since,
                  until: copy.identity_audit_until,
                }[key]
              }
              <Input
                id={'identity-audit-' + key}
                type={key === 'since' || key === 'until' ? 'datetime-local' : 'text'}
                value={form[key]}
                maxLength={100}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
              />
            </label>
          ))}
          <Button autoInsertSpace={false} type="primary" htmlType="submit">
            {action.identity_filter}
          </Button>
          <Button autoInsertSpace={false} onClick={reset}>
            {action.identity_reset}
          </Button>
          <Button autoInsertSpace={false} onClick={() => void query.refetch()}>
            {action.identity_refresh}
          </Button>
        </form>
        {error && <Alert title={error} type="error" />}
      </Card>
      {!valid ? (
        <StateBlock
          state="error"
          description={copy.identity_audit_time_invalid}
          action={{ label: action.identity_reset, onClick: reset }}
        />
      ) : (
        <QueryState query={query}>
          <div className={styles.table}>
            <Table<Row>
              columns={columns}
              rowKey="id"
              dataSource={rows}
              size={viewMode === 'professional' ? 'middle' : 'large'}
              pagination={{
                current: page,
                pageSize: 30,
                total: query.data?.total || 0,
                showSizeChanger: false,
                onChange: (next) => update({ page: next }),
              }}
              locale={{
                emptyText: (
                  <StateBlock
                    state="empty"
                    description={copy.identity_no_audit}
                    action={{ label: action.identity_reset, onClick: reset }}
                  />
                ),
              }}
            />
          </div>
        </QueryState>
      )}
    </section>
  );
}
