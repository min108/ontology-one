import { Alert, Button, Card, Drawer, Input } from 'antd';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { activityHref } from '@legacy/components/HarnessActivity';
import { api } from '../../api/client';
import { workspaceQueryKey } from '../../api/query';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { useAddressState } from '../../app/addressState';
import { StateBlock } from '../../ui/StateBlock';
import { terms } from '../../i18n/zh-CN';
import { parseOverview, parseRuns, parseTotal, taskCounts, todoEntries } from './model';
import { openHomeSearch } from './commands';
import { readRecent } from './recent';
import styles from './Home.module.css';

export default function Home() {
  const session = useSessionStore((state) => state.session);
  const { tenantId, scopeKey } = useScopeStore();
  if (!session || !tenantId || tenantId !== session.workspace.tenant_id) return null;
  return (
    <Workbench
      key={JSON.stringify([session.user.id, tenantId, scopeKey])}
      tenantId={tenantId}
      scopeKey={scopeKey}
    />
  );
}
function Workbench({ tenantId, scopeKey }: { tenantId: string; scopeKey: string }) {
  const session = useSessionStore((state) => state.session)!;
  const { params, update } = useAddressState();
  const detail = params.get('detail') ?? '';
  const [input, setInput] = useState(''),
    [recentRevision, setRecentRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRecentRevision((n) => n + 1);
    window.addEventListener('ontology:recent-changed', refresh);
    return () => window.removeEventListener('ontology:recent-changed', refresh);
  }, []);
  const scope = { userId: session.user.id, tenantId, scopeKey };
  const recent = readRecent(scope);
  void recentRevision;
  const canEdit = session.permissions.can_edit && !session.workspace.readonly,
    canReview = session.permissions.can_review && !session.workspace.readonly;
  const overview = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-overview'),
    queryFn: async ({ signal }) => parseOverview(await api('/overview', { signal })),
  });
  const tasks = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-tasks'),
    queryFn: async ({ signal }) => taskCounts(await api('/work/catalog', { signal })),
  });
  const review = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-review'),
    queryFn: async ({ signal }) =>
      parseTotal(await api('/entities?asset_only=true&status=review&page_size=1', { signal })),
    enabled: canReview,
  });
  const returned = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-returned'),
    queryFn: async ({ signal }) =>
      parseTotal(await api('/entities?asset_only=true&status=returned&page_size=1', { signal })),
    enabled: canEdit,
  });
  const relations = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-relations'),
    queryFn: async ({ signal }) =>
      parseTotal(await api('/relations?status=candidate&page_size=1', { signal })),
    enabled: canReview,
  });
  const foreign = session.workspace.readonly && tenantId !== session.user.home_tenant_id;
  const runs = useQuery({
    queryKey: workspaceQueryKey(tenantId, scopeKey, 'home-runs'),
    queryFn: async ({ signal }) => {
      const responses = await Promise.allSettled([
        api('/harness/runs?limit=20', { signal }),
        api('/agent/runs', { signal }),
      ]);
      const good = responses.filter(
        (response): response is PromiseFulfilledResult<unknown> => response.status === 'fulfilled',
      );
      if (!good.length) throw new Error(terms.labels.home_unavailable);
      return {
        items: parseRuns(good.map((response) => response.value)),
        partial: good.length !== responses.length,
      };
    },
    enabled: !foreign,
    refetchInterval: 15000,
  });
  const rows = todoEntries(
    { canEdit, canReview },
    tasks.data ?? null,
    review.data ?? null,
    returned.data ?? null,
    relations.data ?? null,
  );
  const pendingQueries = [tasks, ...(canReview ? [review, relations] : []), ...(canEdit ? [returned] : [])];
  const retryPending = () => {
    for (const query of pendingQueries) void query.refetch();
  };
  const data = overview.data;
  const metrics = data
    ? [
        {
          id: 'assets',
          label: terms.labels.home_assets,
          value: data.assets,
          note: terms.labels.home_current_only,
        },
        {
          id: 'relations',
          label: terms.labels.home_relations,
          value: data.relations,
          note: terms.labels.home_relation_scope,
        },
        {
          id: 'organizations',
          label: terms.labels.home_organizations,
          value: data.organizations,
          note: terms.labels.home_organizations_scope,
        },
        { id: 'rows', label: terms.labels.home_rows, value: data.rows, note: terms.labels.home_rows_scope },
      ]
    : [];
  const selected = metrics.find((metric) => metric.id === detail);
  const retry = terms.labels.home_retry;
  return (
    <div
      className={styles.home}
      data-home-workbench
      role="region"
      aria-label={terms.labels.home_workbench}
      aria-busy={
        overview.isPending || pendingQueries.some((query) => query.isPending) || (!foreign && runs.isPending)
      }
    >
      <header>
        <p>
          {terms.labels.home_welcome}
          {session.user.display_name}
        </p>
        <h1>{terms.labels.home_workbench}</h1>
        <span>{session.workspace.name}</span>
      </header>
      <section className={styles.entry} aria-label={terms.labels.home_search}>
        <Input.Search
          aria-label={terms.labels.home_omni}
          placeholder={terms.labels.home_omni}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          maxLength={500}
          enterButton={terms.labels.home_search}
          onSearch={openHomeSearch}
        />
        <Button disabled={!canEdit} onClick={() => window.dispatchEvent(new Event('ontology:choose-files'))}>
          {terms.labels.home_choose_files}
        </Button>
        {!canEdit && <p>{terms.labels.home_readonly_files}</p>}
      </section>
      <div className={styles.columns}>
        <Card title={<h2>{terms.labels.home_pending}</h2>}>
          <p>{terms.labels.home_pending_scope}</p>
          {pendingQueries.some((query) => query.isError) && (
            <StateBlock
              state="error"
              description={terms.labels.home_unavailable}
              action={{ label: retry, onClick: retryPending }}
            />
          )}
          {pendingQueries.some((query) => query.isPending) ? (
            <StateBlock state="loading" action={{ label: retry, onClick: retryPending }} />
          ) : (
            <ul className={styles.links}>
              {rows.map((row) => (
                <li key={row.href}>
                  <Link to={row.href}>
                    {row.label}
                    <strong>{row.count.toLocaleString()}</strong>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {!pendingQueries.some((query) => query.isPending || query.isError) &&
            rows.every((row) => row.count === 0) && <p>{terms.labels.home_none_pending}</p>}
        </Card>
        <Card title={<h2>{terms.labels.home_running}</h2>}>
          <p>{foreign ? terms.labels.home_foreign_runs : terms.labels.home_running_scope}</p>
          {foreign ? null : runs.isPending ? (
            <StateBlock state="loading" action={{ label: retry, onClick: () => void runs.refetch() }} />
          ) : runs.isError ? (
            <StateBlock
              state="error"
              description={terms.labels.home_unavailable}
              action={{ label: retry, onClick: () => void runs.refetch() }}
            />
          ) : (
            <>
              {runs.data?.partial && <Alert type="warning" title={terms.labels.home_partial} />}
              <ul className={styles.links}>
                {runs.data?.items.map((item) => {
                  const href = activityHref(item);
                  return (
                    <li key={item.kind + ':' + item.id}>
                      {href ? <Link to={href}>{item.title}</Link> : <span>{item.title}</span>}
                    </li>
                  );
                })}
              </ul>
              {!runs.data?.items.length && <p>{terms.labels.home_none_running}</p>}
            </>
          )}
        </Card>
      </div>
      <section>
        <h2>{terms.labels.home_recent}</h2>
        {recent.length ? (
          <ul className={styles.recent}>
            {recent.map((item) => (
              <li key={item.href}>
                <Link to={item.href}>{item.title}</Link>
              </li>
            ))}
          </ul>
        ) : (
          <p>{terms.labels.home_recent_empty}</p>
        )}
      </section>
      <section>
        <h2>{terms.labels.home_overview}</h2>
        <p>{terms.labels.home_metric_scope}</p>
        {overview.isPending ? (
          <StateBlock state="loading" action={{ label: retry, onClick: () => void overview.refetch() }} />
        ) : overview.isError ? (
          <StateBlock
            state="error"
            description={terms.labels.home_unavailable}
            action={{ label: retry, onClick: () => void overview.refetch() }}
          />
        ) : (
          <div className={styles.metrics}>
            {metrics.map((metric) => (
              <button key={metric.id} data-testid="home-metric" onClick={() => update({ detail: metric.id })}>
                <span>{metric.label}</span>
                <strong>{metric.value.toLocaleString()}</strong>
              </button>
            ))}
          </div>
        )}
      </section>
      <Card title={<h2>{terms.labels.home_briefing}</h2>}>
        <p>{terms.labels.home_briefing_description}</p>
        <Link to="/briefing">{terms.labels.home_command_briefing}</Link>
      </Card>
      {data?.assets === 0 && session.permissions.manage_users && (
        <Alert
          type="info"
          title={terms.labels.home_demo_empty}
          action={<Link to="/admin/demo">{terms.labels.home_demo}</Link>}
        />
      )}
      <Drawer
        title={selected?.label ?? terms.labels.home_details}
        open={!!selected}
        onClose={() => update({ detail: null })}
      >
        {selected && data && (
          <>
            <p>{session.workspace.name}</p>
            <strong>{selected.value.toLocaleString()}</strong>
            <p>{selected.note}</p>
            {detail === 'assets' ? (
              <ul className={styles.links}>
                {['data', 'rule', 'skill', 'knowledge'].map((kind) => (
                  <li key={kind}>
                    <Link to={'/assets?kind=' + kind}>
                      {(terms.kinds as Record<string, string>)[kind]}
                      <strong>{(data.kinds[kind] ?? 0).toLocaleString()}</strong>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : detail === 'relations' ? (
              <>
                <ul>
                  {Object.entries(data.relationStatuses).map(([status, count]) => (
                    <li key={status}>
                      {Object.hasOwn(terms.statuses.relation, status)
                        ? (terms.statuses.relation as Record<string, string>)[status]
                        : terms.labels.home_other_status}
                      ：{count.toLocaleString()}
                    </li>
                  ))}
                </ul>
                <Link to="/graph">{terms.labels.home_open_catalog}</Link>
              </>
            ) : (
              <Link to={detail === 'organizations' ? '/graph?view=organization' : '/datasets'}>
                {terms.labels.home_open_catalog}
              </Link>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
