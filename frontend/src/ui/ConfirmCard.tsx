import { Button, Card, Space } from 'antd';
import { terms } from '../i18n/zh-CN';
import styles from './ConfirmCard.module.css';
export function ConfirmCard({
  level,
  summary,
  impact,
  gates = [],
  onConfirm,
  onCancel,
  confirmLabel = terms.actions.submit,
  busy = false,
}: {
  level: 'L1' | 'L2';
  summary: string;
  impact: string;
  gates?: string[];
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  busy?: boolean;
}) {
  return (
    <Card title={terms.labels.confirm_title} className={level === 'L2' ? styles.important : styles.standard}>
      <p>{summary}</p>
      <p>
        <strong>{terms.labels.impact}</strong> · <span>{impact}</span>
      </p>
      {gates.length > 0 && (
        <ul aria-label={terms.labels.gates}>
          {gates.map((gate) => (
            <li key={gate}>{gate}</li>
          ))}
        </ul>
      )}
      <Space>
        <Button autoInsertSpace={false} type="primary" loading={busy} onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button autoInsertSpace={false} disabled={busy} onClick={onCancel}>
          {terms.actions.cancel}
        </Button>
      </Space>
    </Card>
  );
}
