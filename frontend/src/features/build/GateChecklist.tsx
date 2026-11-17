import { Input } from 'antd';
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
  disabled = false,
}: {
  gates: Gates | null | undefined;
  professional?: boolean;
  waivers?: GateWaivers;
  onWaiver?: (code: 'QG1' | 'QG3', value: string) => void;
  disabled?: boolean;
}) {
  if (!gates) return null;
  return (
    <div data-gate-checklist data-gates-passed={gates.passed}>
      <FoldRow
        key={gates.passed ? 'passed' : 'failed'}
        title={copy.r3_main_five_checks}
        summary={gates.passed ? copy.r4_gates_pass : copy.r4_gates_fail}
        defaultOpen={professional || !gates.passed}
      >
        <ul className={styles.checks}>
          {gates.checks.map((check) => (
            <li key={check.code} data-gate-code={check.code} data-gate-status={check.status}>
              <strong>
                {check.label} · {statusLabels[check.status]}
              </strong>
              {check.reason && <p>{check.reason}</p>}
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
