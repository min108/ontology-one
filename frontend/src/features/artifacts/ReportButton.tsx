import { Alert, Button } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

export function ReportButton({ sessionId, runId }: { sessionId: string; runId: string }) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const lifetime = useLifetime(),
    navigate = useNavigate();
  async function create() {
    setBusy(true);
    setError('');
    const source = window.location.href;
    try {
      const result = await api<{ data: { id: string } }>(
        `/agent/sessions/${sessionId}/runs/${runId}/report`,
        { method: 'POST', body: '{}', signal: lifetime.current?.signal },
      );
      if (!lifetime.current?.signal.aborted && window.location.href === source)
        navigate('/apply/artifacts/' + result.data.id);
    } catch (err) {
      if (!lifetime.current?.signal.aborted)
        setError(err instanceof Error ? err.message : terms.labels.r2_report_failed);
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
      {error && <Alert type="error" showIcon message={error} />}
    </div>
  );
}
