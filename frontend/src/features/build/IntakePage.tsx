import { Alert, Button, Progress } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, invokeCapability } from '../../api/client';
import { PageHeader } from '../../ui/PageHeader';
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
  const [run, setRun] = useState<Recipe>();
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
      const value = await invokeCapability<{ data: Recipe }>(
        'recipe.start',
        { material_id: uploaded.id },
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
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api<{ data: Recipe }>('/v2/recipes/' + encodeURIComponent(runId), {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setRun(value.data);
        if (['queued', 'running'].includes(value.data.status)) timer = setTimeout(() => void poll(), 1500);
      } catch (failure) {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : copy.r2_failure);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId]);
  return (
    <div className={styles.page}>
      <PageHeader title={copy.r2_intake_title} />
      <p>{copy.r2_intake_help}</p>
      {error && <Alert type="error" title={error} role="alert" />}
      {!canEdit && <Alert type="info" title={copy.r2_readonly} />}
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
          <Button autoInsertSpace={false} disabled={!canEdit || busy} onClick={() => input.current?.click()}>
            {copy.r2_choose_file}
          </Button>
          <Button
            autoInsertSpace={false}
            type="primary"
            loading={busy}
            disabled={!file || !canEdit}
            onClick={() => void start()}
          >
            {actions.r2_start}
          </Button>
        </div>
        {busy && <p role="status">{copy.r2_uploading}</p>}
      </section>
      {run && (
        <section className={styles.card} aria-live="polite">
          <h2>{copy.r2_progress}</h2>
          <StatusTag domain="recipe" status={run.status} />
          <p>{run.stage_message}</p>
          <Progress percent={Math.round(((run.progress?.done || 0) / (run.progress?.total || 4)) * 100)} />
          {run.changeset_id && <Link to={'/build/changesets/' + run.changeset_id}>{actions.r2_view}</Link>}
        </section>
      )}
    </div>
  );
}
