import { Alert, Drawer } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { activityHref } from '@legacy/components/HarnessActivity';
import { api } from '../../api/client';
import { workspaceQueryKey } from '../../api/query';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { useAddressState } from '../../app/addressState';
import { StateBlock, PageLayout, PageHeader, NextStepBar, HelpTip } from '../../ui';
import { useUiStore } from '../../state/ui';
import { terms } from '../../i18n/zh-CN';
import { parseOverview, parseRuns, parseTotal, taskCounts, todoEntries } from './model';
import { readRecent } from './recent';
import { sentence } from '../build/model';
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
  const navigate = useNavigate();
  const session = useSessionStore((state) => state.session)!;
  const { params, update } = useAddressState();
  const detail = params.get('detail') ?? '';
  const [recentRevision, setRecentRevision] = useState(0);
  const professional = useUiStore((state) => state.viewMode === 'professional');
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
  const positive = rows.filter((row) => row.count > 0);
  const pendingCount = positive.reduce((total, row) => total + row.count, 0);
  const focus = positive.find((row) => row.step) || positive[0];
  const isEmpty =
    data && !data.assets && !data.relations && !data.rows && !positive.length && !runs.data?.items.length;
  const pending = pendingQueries.some((query) => query.isPending);
  const intakeAction = {
    label: terms.labels.home_choose_files,
    disabled: !canEdit,
    onClick: () => window.dispatchEvent(new Event('ontology:choose-files')),
  };
  return (
    <PageLayout
      variant="workbench"
      data-home-workbench
      role="region"
      aria-label={terms.labels.home_workbench}
      aria-busy={overview.isPending || pending || (!foreign && runs.isPending)}
      header={
        <PageHeader
          title={terms.labels.home_workbench}
          display={
            <span data-testid="home-focus">
              {isEmpty
                ? terms.labels.r3_main_start
                : positive.length
                  ? sentence(terms.labels.r3_main_focus, { count: pendingCount })
                  : terms.labels.r3_main_ready}
            </span>
          }
          lead={isEmpty ? terms.labels.r3_main_start_lead : undefined}
          primaryAction={
            focus
              ? {
                  label: terms.labels.r31_home_process,
                  onClick: () => navigate(focus.href),
                }
              : intakeAction
          }
          secondaryAction={focus ? intakeAction : undefined}
          note={!canEdit ? terms.labels.home_readonly_files : undefined}
        />
      }
    >
      <NextStepBar
        current={isEmpty ? 1 : focus?.step || 0}
        full
        description={
          <span data-testid="home-workflow" data-current-step={isEmpty ? 1 : focus?.step || 0}>
            {isEmpty ? terms.labels.r3_main_start_lead : focus ? focus.label : terms.labels.r31_home_workflow}
          </span>
        }
      />
      {overview.isError && (
        <StateBlock
          state="error"
          description={terms.labels.home_unavailable}
          action={{ label: retry, onClick: () => void overview.refetch() }}
        />
      )}
      {overview.isPending && (
        <StateBlock state="loading" action={{ label: retry, onClick: () => void overview.refetch() }} />
      )}
      {pendingQueries.some((query) => query.isError) && (
        <StateBlock
          state="error"
          description={terms.labels.home_unavailable}
          action={{ label: retry, onClick: retryPending }}
        />
      )}
      <div className={styles.columns}>
        {(positive.length > 0 || pending) && (
          <section className={styles.card} data-home-count={pendingCount || undefined}>
            <h2>
              {terms.labels.home_pending}
              {pendingCount > 0 ? ` · ${pendingCount}` : ''}
              <HelpTip term="home_pending" />
            </h2>
            {pending ? (
              <StateBlock state="loading" action={{ label: retry, onClick: retryPending }} />
            ) : (
              <ul className={styles.links}>
                {positive.slice(0, 2).map((row) => (
                  <li key={row.href} data-home-count={row.count}>
                    <Link to={row.href}>
                      {row.label}
                      <strong>{row.count.toLocaleString()}</strong>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {!foreign && (runs.isPending || runs.isError || !!runs.data?.items.length) && (
          <section className={styles.card} data-home-count={runs.data?.items.length || undefined}>
            <h2>
              {terms.labels.home_running}
              {runs.data?.items.length ? ` · ${runs.data.items.length}` : ''}
            </h2>
            {runs.isPending ? (
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
                  {runs.data?.items.slice(0, 2).map((item) => {
                    const href = activityHref(item);
                    return (
                      <li key={item.kind + ':' + item.id}>
                        {href ? <Link to={href}>{item.title}</Link> : <span>{item.title}</span>}
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
          </section>
        )}
        {recent.length > 0 && (
          <section className={styles.card} data-home-count={recent.length}>
            <h2>
              {terms.labels.home_recent} · {recent.length}
            </h2>
            <ul className={styles.links}>
              {recent.slice(0, 2).map((item) => (
                <li key={item.href}>
                  <Link to={item.href}>{item.title}</Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
      {foreign && <p>{terms.labels.home_foreign_runs}</p>}
      <section className={styles.card}>
        <h2>{terms.labels.r3_main_common}</h2>
        <div className={styles.actions}>
          <Link to="/ontology/catalog">{terms.labels.r2_catalog_title}</Link>
          <Link to="/applications">{terms.actions.r2_ask}</Link>
          <Link to="/briefing">{terms.labels.home_command_briefing}</Link>
        </div>
      </section>
      {professional && metrics.some((metric) => metric.value > 0) && (
        <section>
          <h2>{terms.labels.home_overview}</h2>
          <div className={styles.metrics}>
            {metrics
              .filter((metric) => metric.value > 0)
              .map((metric) => (
                <button
                  key={metric.id}
                  data-testid="home-metric"
                  data-home-count={metric.value}
                  onClick={() => update({ detail: metric.id })}
                >
                  <span>{metric.label}</span>
                  <strong>{metric.value.toLocaleString()}</strong>
                </button>
              ))}
          </div>
        </section>
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
    </PageLayout>
  );
}
