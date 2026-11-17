import { Alert, Button } from 'antd';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import type { AgentReportRun, ArtifactResult, Report } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

function correctionIssues(error: unknown): string[] {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return [];
  const payload = error.detail as Record<string, unknown>;
  const envelope =
    payload.error && typeof payload.error === 'object'
      ? (payload.error as Record<string, unknown>).details
      : payload.detail;
  if (!envelope || typeof envelope !== 'object') return [];
  const detail = envelope as Record<string, unknown>;
  return ['NUMERIC_UNVERIFIED', 'CITATION_UNVERIFIED'].includes(String(detail.code)) &&
    Array.isArray(detail.issues)
    ? detail.issues.filter((value): value is string => typeof value === 'string')
    : [];
}

export function ReportButton({
  sessionId,
  runId,
  revisionOf,
  revisionRunId,
  onRevise,
}: {
  sessionId: string;
  runId: string;
  revisionOf?: string | null;
  revisionRunId?: string | null;
  onRevise?: (message: string, sourceRunId: string) => Promise<string>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [issues, setIssues] = useState<string[]>([]),
    [revisionFailed, setRevisionFailed] = useState(false),
    [revised, setRevised] = useState(Boolean(revisionOf || revisionRunId)),
    [revising, setRevising] = useState(false);
  const busyRef = useRef(false),
    automaticUsed = useRef(Boolean(revisionOf || revisionRunId)),
    reportRun = useRef(revisionRunId || runId),
    waitingForRun = useRef(Boolean(revisionRunId));
  const lifetime = useLifetime(),
    navigate = useNavigate();

  async function create(retryRevision = false) {
    if (busyRef.current || lifetime.current?.signal.aborted) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    let owner = window.location.href;
    const live = () => !lifetime.current?.signal.aborted && window.location.href === owner;
    async function waitForRun() {
      while (live() && waitingForRun.current) {
        const run = await api<AgentReportRun>(`/agent/sessions/${sessionId}/runs/${reportRun.current}`, {
          signal: lifetime.current?.signal,
        });
        if (!live()) return;
        if (run.status === 'completed') {
          waitingForRun.current = false;
          return;
        }
        if (!['queued', 'running'].includes(run.status)) throw new Error(terms.labels.r21_revision_failed);
        await new Promise<void>((resolve) => {
          const signal = lifetime.current?.signal;
          const done = () => {
            window.clearTimeout(timer);
            signal?.removeEventListener('abort', done);
            resolve();
          };
          const timer = window.setTimeout(done, 500);
          signal?.addEventListener('abort', done, { once: true });
        });
      }
    }
    async function generate() {
      await waitForRun();
      if (!live()) return;
      const body = {} satisfies Report;
      const result = await api<ArtifactResult>(
        `/agent/sessions/${sessionId}/runs/${reportRun.current}/report`,
        {
          method: 'POST',
          body: JSON.stringify(body),
          signal: lifetime.current?.signal,
        },
      );
      if (live()) {
        setIssues([]);
        navigate('/apply/artifacts/' + result.data.id);
      }
    }
    async function repair(corrections: string[]) {
      if (!onRevise || !live()) return;
      automaticUsed.current = true;
      setRevising(true);
      setRevisionFailed(false);
      let selected: string;
      try {
        selected = await onRevise(terms.labels.r21_revision_prompt + '\n' + corrections.join('\n'), runId);
      } catch (err) {
        if (live()) setRevisionFailed(true);
        throw err;
      }
      if (lifetime.current?.signal.aborted) return;
      const previous = new URL(owner),
        current = new URL(window.location.href);
      if (
        current.href !== owner &&
        (current.origin !== previous.origin ||
          current.pathname !== previous.pathname ||
          current.hash !== previous.hash ||
          current.searchParams.get('session') !== sessionId ||
          current.searchParams.get('run') !== selected)
      )
        return;
      owner = current.href;
      reportRun.current = selected;
      waitingForRun.current = true;
      setRevised(true);
      await generate();
    }
    try {
      if (retryRevision && revisionFailed && onRevise) await repair(issues);
      else {
        try {
          await generate();
        } catch (err) {
          if (!live()) return;
          const corrections = correctionIssues(err);
          setIssues(corrections);
          if (corrections.length && onRevise && !automaticUsed.current) await repair(corrections);
          else throw err;
        }
      }
    } catch (err) {
      if (live()) {
        setError(err instanceof Error ? err.message : terms.labels.r2_report_failed);
        const corrections = correctionIssues(err);
        if (corrections.length) setIssues(corrections);
      }
    } finally {
      busyRef.current = false;
      if (!lifetime.current?.signal.aborted) {
        setBusy(false);
        setRevising(false);
      }
    }
  }
  return (
    <div className={styles.stack}>
      <div>
        <Button
          aria-label={terms.actions.r2_report_create}
          autoInsertSpace={false}
          onClick={() => void create()}
          loading={busy}
        >
          {terms.actions.r2_report_create}
        </Button>
        {revisionFailed && onRevise && (
          <Button autoInsertSpace={false} disabled={busy} onClick={() => void create(true)}>
            {terms.actions.r3_report_retry_revision}
          </Button>
        )}
      </div>
      {revising && <p role="status">{terms.labels.r3_report_revising}</p>}
      {revised && <p>{terms.labels.r3_report_revised}</p>}
      {error && (
        <Alert
          type="error"
          showIcon
          title={error}
          description={
            <>
              {issues.length > 0 && (
                <ul>
                  {issues.map((issue, index) => (
                    <li key={index}>{issue}</li>
                  ))}
                </ul>
              )}
              <p>
                {revisionFailed
                  ? terms.labels.r3_report_retry_help
                  : revised
                    ? terms.labels.r3_report_manual
                    : terms.labels.r2_report_failure}
              </p>
            </>
          }
        />
      )}
    </div>
  );
}
