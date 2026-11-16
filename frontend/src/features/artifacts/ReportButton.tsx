import { Alert, Button } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

export function ReportButton({ sessionId, runId }: { sessionId: string; runId: string }) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [issues, setIssues] = useState<string[]>([]);
  const lifetime = useLifetime(),
    navigate = useNavigate();
  async function create() {
    setBusy(true);
    setError('');
    setIssues([]);
    const source = window.location.href;
    try {
      const result = await api<{ data: { id: string } }>(
        `/agent/sessions/${sessionId}/runs/${runId}/report`,
        { method: 'POST', body: '{}', signal: lifetime.current?.signal },
      );
      if (!lifetime.current?.signal.aborted && window.location.href === source)
        navigate('/apply/artifacts/' + result.data.id);
    } catch (err) {
      if (!lifetime.current?.signal.aborted) {
        setError(err instanceof Error ? err.message : terms.labels.r2_report_failed);
        const payload =
          err instanceof ApiError
            ? (err.detail as { detail?: { code?: unknown; issues?: unknown } } | null)
            : null;
        const detail = payload?.detail;
        if (detail?.code === 'NUMERIC_UNVERIFIED' && Array.isArray(detail.issues))
          setIssues(detail.issues.filter((value): value is string => typeof value === 'string'));
      }
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className={styles.stack}>
      <div>
        <Button autoInsertSpace={false} onClick={() => void create()} loading={busy}>
          {terms.actions.r2_report_create}
        </Button>
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
