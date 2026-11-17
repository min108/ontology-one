import { Alert, Button } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

export function ReportButton({
  sessionId,
  runId,
  onRevise,
}: {
  sessionId: string;
  runId: string;
  onRevise?: (message: string, sourceRunId: string) => Promise<string>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [issues, setIssues] = useState<string[]>([]),
    [repairUsed, setRepairUsed] = useState(false),
    [reportRun, setReportRun] = useState(runId);
  const lifetime = useLifetime(),
    navigate = useNavigate();
  async function create(revise = false) {
    if (busy || (revise && (repairUsed || !onRevise))) return;
    const corrections = [...issues];
    setBusy(true);
    setError('');
    setIssues([]);
    let source = window.location.href;
    let revisionStarted = false;
    try {
      let selectedRun = reportRun;
      if (revise && onRevise) {
        selectedRun = await onRevise(terms.labels.r21_revision_prompt + '\n' + corrections.join('\n'), runId);
        revisionStarted = true;
        setRepairUsed(true);
        setReportRun(selectedRun);
        const previous = new URL(source),
          current = new URL(window.location.href);
        if (
          current.href !== previous.href &&
          (current.origin !== previous.origin ||
            current.pathname !== previous.pathname ||
            current.hash !== previous.hash ||
            current.searchParams.get('session') !== sessionId ||
            current.searchParams.get('run') !== selectedRun)
        )
          return;
        source = window.location.href;
        while (!lifetime.current?.signal.aborted) {
          const run = await api<{ status: string }>(`/agent/sessions/${sessionId}/runs/${selectedRun}`, {
            signal: lifetime.current?.signal,
          });
          if (run.status === 'completed') break;
          if (!['queued', 'running'].includes(run.status)) throw new Error(terms.labels.r21_revision_failed);
          await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
        }
        if (lifetime.current?.signal.aborted) return;
      }
      const result = await api<{ data: { id: string } }>(
        `/agent/sessions/${sessionId}/runs/${selectedRun}/report`,
        { method: 'POST', body: '{}', signal: lifetime.current?.signal },
      );
      if (!lifetime.current?.signal.aborted && window.location.href === source)
        navigate('/apply/artifacts/' + result.data.id);
    } catch (err) {
      if (!lifetime.current?.signal.aborted) {
        setError(err instanceof Error ? err.message : terms.labels.r2_report_failed);
        if (revise && !revisionStarted) setIssues(corrections);
        const payload =
          err instanceof ApiError
            ? (err.detail as {
                detail?: { code?: unknown; issues?: unknown };
                error?: { details?: { code?: unknown; issues?: unknown } };
              } | null)
            : null;
        const detail = payload?.error?.details || payload?.detail;
        if (
          ['NUMERIC_UNVERIFIED', 'CITATION_UNVERIFIED'].includes(String(detail?.code)) &&
          Array.isArray(detail?.issues)
        )
          setIssues(detail.issues.filter((value): value is string => typeof value === 'string'));
      }
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy(false);
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
        {issues.length > 0 && onRevise && (
          <Button autoInsertSpace={false} disabled={busy || repairUsed} onClick={() => void create(true)}>
            {terms.actions.r21_report_revise}
          </Button>
        )}
      </div>
      {error && (
        <Alert
          type="error"
          showIcon
          title={error}
          description={
            issues.length ? (
              <ul>
                {issues.map((issue, index) => (
                  <li key={index}>{issue}</li>
                ))}
              </ul>
            ) : undefined
          }
        />
      )}
    </div>
  );
}
