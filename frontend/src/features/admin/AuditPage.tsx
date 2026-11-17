import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Table } from 'antd';
import type { TableColumnsType } from 'antd';
import { terms } from '../../i18n/zh-CN';
import { useSessionStore } from '../../state/session';
import { useUiStore } from '../../state/ui';
import { addressPage, useAddressState } from '../../app/addressState';
import { PageHeader } from '../../ui/PageHeader';
import { PageLayout } from '../../ui/PageLayout';
import { BusinessDatePicker } from '../../ui/BusinessDatePicker';
import { EvidenceDrawer } from '../../ui/EvidenceDrawer';
import { TechnicalDetails } from '../../ui/TechnicalDetails';
import { StateBlock } from '../../ui/StateBlock';
import { copy, action, Forbidden, QueryState, Timestamp, useIdentityQuery } from '../identity/common';
import type { AuditOutput, User, Org, List } from '../identity/contracts';
import styles from '../identity/Identity.module.css';
import adminStyles from './Admin.module.css';
import { AdminStatus } from './AdminStatus';
type Row = NonNullable<AuditOutput['items']>[number];
const filterKeys = ['actor_id', 'tenant_id', 'action', 'outcome', 'since', 'until'] as const;
type Filters = Record<(typeof filterKeys)[number], string>;
const actionLabel = (value: string) =>
  Object.hasOwn(terms.identity_audit_actions, value)
    ? terms.identity_audit_actions[value as keyof typeof terms.identity_audit_actions]
    : copy.identity_audit_other;
const outcomeLabel = (value: string) =>
  Object.hasOwn(terms.identity_audit_outcomes, value)
    ? terms.identity_audit_outcomes[value as keyof typeof terms.identity_audit_outcomes]
    : copy.r3_admin_unknown_outcome;
/** Date-only filters cover the complete Beijing calendar day; old minute URLs remain valid. */
export function auditBoundary(value: string, end = false): string | null {
  if (!value) return '';
  if (!/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?$/.test(value)) return null;
  const local = value.length === 10 ? value + (end ? 'T23:59:59.999' : 'T00:00:00') : value;
  const date = new Date(local + '+08:00');
  if (Number.isNaN(date.getTime())) return null;
  const calendar = new Date(date.getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return calendar === value.slice(0, 10) ? date.toISOString() : null;
}
export default function AuditPage() {
  const { session, permission_keys } = useSessionStore();
  if (!session?.user.is_superadmin || !permission_keys.includes('audit.read'))
    return (
      <PageLayout variant="list" header={<PageHeader title={copy.identity_audit} />}>
        <Forbidden headquarters />
      </PageLayout>
    );
  return <AuditRecords />;
}
function AuditRecords() {
  const { params, update } = useAddressState();
  const viewMode = useUiStore((state) => state.viewMode);
  const [form, setForm] = useState(
    () => Object.fromEntries(filterKeys.map((key) => [key, params.get(key) || ''])) as Filters,
  );
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Row | null>(null);
  const signature = filterKeys.map((key) => params.get(key) || '').join('|');
  useEffect(() => {
    setForm(Object.fromEntries(filterKeys.map((key) => [key, params.get(key) || ''])) as Filters);
  }, [signature]);
  const users = useIdentityQuery<List<User>>('identity-users', '/v2/users');
  const orgs = useIdentityQuery<List<Org>>('identity-orgs', '/v2/orgs');
  const page = addressPage(params.get('page'));
  const queryParams = new URLSearchParams({ page: String(page), page_size: '30' });
  const since = auditBoundary(params.get('since') || ''),
    until = auditBoundary(params.get('until') || '', true);
  const valid = since !== null && until !== null && !(since && until && since > until);
  for (const key of filterKeys) {
    const value = key === 'since' ? since : key === 'until' ? until : params.get(key);
    if (value) queryParams.set(key, value);
  }
  const query = useIdentityQuery<AuditOutput>('identity-audit', '/v2/audit?' + queryParams, valid);
  const rows = query.data?.items || [];
  const columns: TableColumnsType<Row> = [
    {
      key: 'time',
      title: copy.identity_audit_time,
      render: (_, row) => <Timestamp value={String(row.created_at || '')} />,
    },
    {
      key: 'actor',
      title: copy.identity_audit_actor,
      render: (_, row) => String(row.actor_username || copy.identity_audit_anonymous),
    },
    {
      key: 'action',
      title: copy.identity_audit_action,
      render: (_, row) => (
        <Button type="text" size="small" autoInsertSpace={false} onClick={() => setSelected(row)}>
          {actionLabel(String(row.action || ''))}
        </Button>
      ),
    },
    {
      key: 'outcome',
      title: copy.identity_audit_outcome,
      render: (_, row) => {
        const value = String(row.outcome || '');
        return <AdminStatus status={value} label={outcomeLabel(value)} />;
      },
    },
  ];
  function filter(event: FormEvent) {
    event.preventDefault();
    const since = auditBoundary(form.since),
      until = auditBoundary(form.until, true);
    if (since === null || until === null || (since && until && since > until)) {
      setError(copy.identity_audit_time_invalid);
      return;
    }
    setError('');
    update({ ...Object.fromEntries(filterKeys.map((key) => [key, form[key].trim() || null])), page: null });
  }
  function reset() {
    setError('');
    setForm({ actor_id: '', tenant_id: '', action: '', outcome: '', since: '', until: '' });
    update({ ...Object.fromEntries(filterKeys.map((key) => [key, null])), page: null });
  }
  const selectors = [
    {
      key: 'actor_id' as const,
      label: copy.r3_admin_actor,
      items: (users.data?.items || []).map((item) => ({
        value: item.id,
        label: `${item.display_name}（${item.username}）`,
      })),
    },
    {
      key: 'tenant_id' as const,
      label: copy.r3_admin_organization,
      items: (orgs.data?.items || []).map((item) => ({ value: item.id, label: item.name })),
    },
    {
      key: 'action' as const,
      label: copy.r3_admin_action,
      items: Object.entries(terms.identity_audit_actions).map(([value, label]) => ({ value, label })),
    },
    {
      key: 'outcome' as const,
      label: copy.r3_admin_outcome,
      items: Object.entries(terms.identity_audit_outcomes).map(([value, label]) => ({ value, label })),
    },
  ];
  return (
    <PageLayout
      variant="list"
      aria-label={copy.identity_audit}
      aria-busy={query.isPending && valid}
      header={
        <PageHeader
          title={copy.identity_audit}
          secondaryAction={{ label: action.identity_refresh, onClick: () => void query.refetch() }}
        />
      }
    >
      <form className={adminStyles['audit-filters']} onSubmit={filter}>
        {selectors.map((field) => (
          <label className={styles.field} key={field.key} htmlFor={'identity-audit-' + field.key}>
            <span className={adminStyles['visually-hidden']}>{field.label}</span>
            <select
              id={'identity-audit-' + field.key}
              className={styles.control}
              value={form[field.key]}
              onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
            >
              <option value="">
                {field.label} · {copy.r3_admin_all}
              </option>
              {field.items.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        ))}
        {(['since', 'until'] as const).map((key) => (
          <BusinessDatePicker
            key={key}
            aria-label={key === 'since' ? copy.identity_audit_since : copy.identity_audit_until}
            value={form[key].slice(0, 10)}
            onChange={(value) => setForm({ ...form, [key]: value })}
          />
        ))}
        <Button autoInsertSpace={false} htmlType="submit">
          {action.identity_filter}
        </Button>
        <Button type="text" autoInsertSpace={false} onClick={reset}>
          {action.identity_reset}
        </Button>
      </form>
      {error && <Alert title={error} type="error" />}
      {(users.isError || orgs.isError) && (
        <Alert
          type="warning"
          title={copy.identity_error_preserved}
          action={
            <Button
              onClick={() => {
                void users.refetch();
                void orgs.refetch();
              }}
            >
              {action.identity_retry}
            </Button>
          }
        />
      )}
      {!valid ? (
        <StateBlock
          state="error"
          description={copy.identity_audit_time_invalid}
          action={{ label: action.identity_reset, onClick: reset }}
        />
      ) : (
        <QueryState query={query}>
          {rows.length ? (
            <div className={styles.table}>
              <Table<Row>
                columns={columns}
                rowKey={(row) => String(row.id)}
                dataSource={rows}
                size={viewMode === 'professional' ? 'middle' : 'large'}
                onRow={(row) => ({ onDoubleClick: () => setSelected(row) })}
                pagination={{
                  current: page,
                  pageSize: 30,
                  total: query.data?.total || 0,
                  showSizeChanger: false,
                  onChange: (next) => update({ page: next }),
                }}
              />
            </div>
          ) : (
            <StateBlock
              state="empty"
              description={copy.identity_no_audit}
              action={{ label: action.identity_reset, onClick: reset }}
            />
          )}
        </QueryState>
      )}
      <EvidenceDrawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={copy.r3_admin_audit_detail}
        wide
      >
        {selected && (
          <div className={styles.stack}>
            <p>
              <Timestamp value={String(selected.created_at || '')} /> ·{' '}
              {String(selected.actor_username || copy.identity_audit_anonymous)}
            </p>
            <p>
              {actionLabel(String(selected.action || ''))} · {outcomeLabel(String(selected.outcome || ''))}
            </p>
            {viewMode === 'simple' && <p>{copy.r3_admin_audit_detail_hint}</p>}
            <TechnicalDetails>
              <dl className={styles.facts}>
                {[
                  [copy.identity_audit_raw_action, selected.action],
                  [copy.identity_audit_raw_outcome, selected.outcome],
                  [copy.identity_audit_actor_id, selected.actor_id],
                  [copy.identity_audit_tenant_id, selected.tenant_id],
                  [copy.identity_audit_resource, selected.resource],
                  [copy.identity_request_id, selected.request_id],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <dt>{String(label)}</dt>
                    <dd>
                      <code>{String(value || '—')}</code>
                    </dd>
                  </div>
                ))}
              </dl>
              <pre className={styles.detail}>{JSON.stringify(selected.details, null, 2)}</pre>
            </TechnicalDetails>
          </div>
        )}
      </EvidenceDrawer>
    </PageLayout>
  );
}
