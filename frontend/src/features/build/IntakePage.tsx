import { Alert, Button, Progress } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, invokeCapability } from '../../api/client';
import { PageHeader, PageLayout, NextStepBar, OriginTag } from '../../ui';
import { useQuery } from '@tanstack/react-query';
import { workspaceQueryKey } from '../../api/query';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { ResourceState, useResource } from '../admin/shared';
import type { RecipeStart, RecipeResult, ProposalResult } from '../../api/generated/types';
import { sentence } from './model';
import { StatusTag } from '../../ui/StatusTag';
import { BuildForbidden } from './shared';
import { fileProblems } from '../home/intake';
import { useIncomingFiles } from '../home/useIncomingFiles';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import { actions, copy, type Recipe, useBuildIdentity, useLifetime } from './shared';
import styles from './Build.module.css';

export default function IntakePage() {
  const identity = useBuildIdentity();
  return identity.canRead ? <Intake key={identity.key} canEdit={identity.canEdit} /> : <BuildForbidden />;
}
function Intake({ canEdit }: { canEdit: boolean }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [file, setFile] = useState<File>();
  const [autoStart, setAutoStart] = useState(false);
  const [runId, setRunId] = useState(params.get('run') || '');
  const [startedRun, setRun] = useState<Recipe>();
  const session = useSessionStore((state) => state.session);
  const scopeKey = useScopeStore((state) => state.scopeKey);
  const runQuery = useQuery({
    queryKey: workspaceQueryKey(session?.workspace.tenant_id || '', scopeKey, 'intake:' + runId),
    enabled: !!runId,
    queryFn: ({ signal }) => api<RecipeResult>('/v2/recipes/' + encodeURIComponent(runId), { signal }),
    refetchInterval: (query) =>
      ['queued', 'running'].includes(query.state.data?.data.status || '') ? 1500 : false,
  });
  const run = runQuery.data?.data || (startedRun?.id === runId ? startedRun : undefined);
  const result = useResource<ProposalResult>(
    '/v2/changesets/' + encodeURIComponent(run?.changeset_id || ''),
    !!run?.changeset_id,
  );
  const itemCount = result.data?.data.items.length;
  const checkCount =
    result.data?.data.items.filter((item) => item.tier !== 'sure' && !item.processed_elsewhere).length || 0;
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const lifetime = useLifetime();
  useWorkspaceGuard(Boolean(file) || busy);
  function choose(incoming: File[]) {
    if (!canEdit || busy) return;
    const errors = fileProblems(incoming, 'materials');
    if (incoming.length !== 1 || errors.length) {
      setError(errors.join(' ') || copy.r2_no_file);
      return;
    }
    setFile(incoming[0]);
    setError('');
  }
  const start = useCallback(async () => {
    const signal = lifetime.current?.signal;
    if (!file || busy || !canEdit || signal?.aborted) return;
    setBusy(true);
    setError('');
    const origin = window.location.href;
    try {
      const form = new FormData();
      form.set('file', file);
      form.set('upload_token', crypto.randomUUID());
      const uploaded = await api<{ id: string }>('/materials/upload', { method: 'POST', body: form, signal });
      const value = await invokeCapability<RecipeResult>(
        'recipe.start',
        { material_id: uploaded.id } satisfies RecipeStart,
        { signal },
      );
      if (signal?.aborted) return;
      setFile(undefined);
      setRunId(value.data.id);
      setRun(value.data);
      if (window.location.href === origin) navigate('/build/intake?run=' + value.data.id, { replace: true });
      window.dispatchEvent(new Event('ontology:harness-runs-changed'));
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }, [file, busy, canEdit, lifetime, navigate]);
  useIncomingFiles(
    'materials',
    (incoming) => {
      if (incoming.length !== 1 || fileProblems(incoming, 'materials').length) return false;
      setFile(incoming[0]);
      setAutoStart(true);
      return true;
    },
    canEdit && !file && !busy,
    '/build/intake',
  );
  useEffect(() => {
    if (!autoStart || !file || busy || !canEdit) return;
    setAutoStart(false);
    void start();
  }, [autoStart, file, busy, canEdit, start]);
  return (
    <PageLayout
      variant="archive"
      header={
        <PageHeader
          title={copy.r2_intake_title}
          lead={
            itemCount !== undefined
              ? sentence(copy.r3_main_intake_done, { count: itemCount, check: checkCount })
              : run
                ? copy.r3_main_intake_running
                : copy.r2_intake_help
          }
          primaryAction={
            run?.changeset_id
              ? {
                  label: copy.r3_main_intake_view,
                  onClick: () => navigate('/build/changesets/' + run.changeset_id),
                }
              : {
                  label: busy ? copy.r2_uploading : actions.r2_start,
                  disabled: !file || !canEdit || busy,
                  onClick: () => void start(),
                }
          }
        />
      }
    >
      {error && <Alert type="error" title={error} role="alert" />}
      {!canEdit && <Alert type="info" title={copy.r2_readonly} />}
      {!run && (
        <section
          className={styles.intake}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            choose(Array.from(event.dataTransfer.files));
          }}
        >
          <input
            ref={input}
            hidden
            type="file"
            aria-label={copy.r2_choose_file}
            accept=".txt,.md,.pdf,.docx,.html,.htm"
            onChange={(event) => {
              choose(Array.from(event.target.files || []));
              event.target.value = '';
            }}
          />
          <p>{file?.name || copy.r2_file_help}</p>
          <div className={styles.actions}>
            <Button
              autoInsertSpace={false}
              disabled={!canEdit || busy}
              onClick={() => input.current?.click()}
            >
              {copy.r2_choose_file}
            </Button>
          </div>
          {busy && <p role="status">{copy.r2_uploading}</p>}
        </section>
      )}
      {runQuery.isError && <ResourceState error={copy.r2_failure} reload={() => void runQuery.refetch()} />}
      {run && (
        <section className={styles.card} aria-live="polite">
          <h2>{copy.r2_progress}</h2>
          <StatusTag domain="recipe" status={run.status} />
          <p>{run.stage_message}</p>
          <Progress percent={Math.round(((run.progress?.done || 0) / (run.progress?.total || 4)) * 100)} />
          <OriginTag origin={run.used_model ? 'ai' : 'local'} />
          {run.changeset_id && (
            <Button
              type="text"
              onClick={() => {
                setRunId('');
                setRun(undefined);
                navigate('/build/intake', { replace: true });
              }}
            >
              {copy.r3_main_intake_new}
            </Button>
          )}
        </section>
      )}
      <NextStepBar current={1} description={copy.r3_main_next_check} />
    </PageLayout>
  );
}
