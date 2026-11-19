import { Button, Input } from 'antd';
import { useState } from 'react';
import type { CandidatePatch } from '../../api/generated/types';
import { FoldRow } from '../../ui';
import { copy, type Item } from './shared';
import styles from './Build.module.css';

export function GateApplications({
  item,
  disabled,
  onSave,
}: {
  item: Item;
  disabled: boolean;
  onSave: (item: Item, applications: NonNullable<CandidatePatch['waiver_applications']>) => void;
}) {
  const [snapshot] = useState(item);
  const [applications, setApplications] = useState<NonNullable<CandidatePatch['waiver_applications']>>(
    item.after_data.waiver_applications || {},
  );
  const checks =
    item.gates?.checks.filter(
      (check) => check.status === 'fail' && check.can_waive && (check.code === 'QG1' || check.code === 'QG3'),
    ) || [];
  if (!checks.length) return null;
  return (
    <FoldRow title={copy.r4a1_gate_application} summary={copy.r4a1_gate_application_note}>
      <div data-gate-applications>
        {checks.map((check) => (
          <label key={check.code} className={styles.field}>
            {check.label} · {copy.r4a1_gate_application}
            <Input.TextArea
              aria-label={check.label + ' · ' + copy.r4a1_gate_application}
              value={applications[check.code as 'QG1' | 'QG3'] || ''}
              disabled={disabled}
              rows={2}
              maxLength={500}
              placeholder={copy.r4a1_gate_application_hint}
              onChange={(event) => setApplications({ ...applications, [check.code]: event.target.value })}
            />
            {check.application_reason && <span>{check.application_reason}</span>}
          </label>
        ))}
        <Button
          disabled={disabled}
          onClick={() =>
            onSave(
              snapshot,
              Object.fromEntries(Object.entries(applications).filter(([, value]) => value?.trim())),
            )
          }
        >
          {copy.r4a1_gate_application_save}
        </Button>
      </div>
    </FoldRow>
  );
}
