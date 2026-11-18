import { Alert, Button, Input } from 'antd';
import type { ElementDetail, ReviewBody } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { FoldRow } from '../../ui';
import styles from './Build.module.css';

type Gates = NonNullable<ElementDetail['gates']>;
export type GateWaivers = NonNullable<ReviewBody['waivers']>;
const copy = terms.labels;
const statusLabels = {
  pass: copy.r4_gate_pass,
  fail: copy.r4_gate_fail,
  waived: copy.r4_gate_waived,
  not_applicable: copy.r4_gate_not_applicable,
};
export function gateBlockers(gates: Gates | null | undefined, waivers: GateWaivers = {}) {
  return (gates?.checks || []).filter(
    (check) =>
      check.status === 'fail' &&
      !(check.can_waive && (check.code === 'QG1' || check.code === 'QG3') && waivers[check.code]?.trim()),
  );
}
export function GateChecklist({
  gates,
  professional = false,
  waivers,
  onWaiver,
  onRepair,
  disabled = false,
}: {
  gates: Gates | null | undefined;
  professional?: boolean;
  waivers?: GateWaivers;
  onWaiver?: (code: 'QG1' | 'QG3', value: string) => void;
  onRepair?: (code: string) => void;
  disabled?: boolean;
}) {
  if (!gates) return null;
  const waived = gates.checks.filter((check) => check.status === 'waived');
  const sourceWaived = waived.some((check) => check.code === 'QG1');
  const sourceWaiverProposed = !!waivers?.QG1?.trim();
  return (
    <div data-gate-checklist data-gates-passed={gates.passed}>
      {waived.length > 0 && (
        <p data-gate-waiver-marker>
          {waived
            .map((check) => (check.code === 'QG1' ? copy.r4_source_waived : copy.r4_conditions_waived))
            .join(' · ')}
        </p>
      )}
      {gates.hard_constraint && (sourceWaived || sourceWaiverProposed) && (
        <Alert data-gate-basis-warning type="warning" title={copy.r4_waiver_basis_warning} />
      )}
      <FoldRow
        key={gates.passed ? 'passed' : 'failed'}
        title={copy.r3_main_five_checks}
        summary={
          gates.passed ? (waived.length ? copy.r4_gates_waived : copy.r4_gates_pass) : copy.r4_gates_fail
        }
        defaultOpen={professional || !gates.passed}
      >
        <ul className={styles.checks}>
          {gates.checks.map((check) => (
            <li key={check.code} data-gate-code={check.code} data-gate-status={check.status}>
              <strong>
                {check.label} · {statusLabels[check.status]}
              </strong>
              {check.reason && <p>{check.reason}</p>}
              {check.application_reason && (
                <p data-waiver-application>
                  {copy.r4a1_legacy_application}：{check.application_reason}
                </p>
              )}
              {check.denial_reason && (
                <p>
                  {copy.r4a1_legacy_denial}：{check.denial_reason}
                </p>
              )}
              {onRepair && check.status === 'fail' && (
                <Button autoInsertSpace={false} disabled={disabled} onClick={() => onRepair(check.code)}>
                  {copy.r4a1_legacy_repair}
                </Button>
              )}
              {check.waiver_reason && (
                <p>
                  {copy.r4_gate_waiver}：{check.waiver_reason}
                </p>
              )}
              {!onWaiver &&
                (check.code === 'QG1' || check.code === 'QG3') &&
                waivers?.[check.code]?.trim() && (
                  <p>
                    {copy.r4_gate_waiver}：{waivers[check.code]}
                  </p>
                )}
              {onWaiver &&
                check.status === 'fail' &&
                (check.can_waive && (check.code === 'QG1' || check.code === 'QG3') ? (
                  <label className={styles.field}>
                    {check.label} · {copy.r4_gate_waiver}
                    <Input.TextArea
                      disabled={disabled}
                      value={waivers?.[check.code] || ''}
                      placeholder={copy.r4_gate_waiver_hint}
                      maxLength={500}
                      rows={2}
                      onChange={(event) => onWaiver(check.code as 'QG1' | 'QG3', event.target.value)}
                    />
                  </label>
                ) : (
                  <p>{copy.r4_gate_cannot_waive}</p>
                ))}
            </li>
          ))}
        </ul>
      </FoldRow>
    </div>
  );
}
