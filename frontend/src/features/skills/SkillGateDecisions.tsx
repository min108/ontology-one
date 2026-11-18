import { Alert, Input } from 'antd';
import type { GateResult, GateWaivers } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { GateChecklist, gateBlockers } from '../build/GateChecklist';
import { GovernanceFacts } from '../build/GovernanceFacts';
import styles from '../build/Build.module.css';

const copy = terms.labels;
export function skillDecisionBlocked(
  action: string,
  canReview: boolean,
  packageReady: boolean,
  gates: GateResult | null | undefined,
  waivers: GateWaivers,
  denials: GateWaivers,
) {
  if (action !== 'publish' && action !== 'return') return false;
  if (!canReview || !gates) return true;
  if ((['QG1', 'QG3'] as const).some((code) => waivers[code]?.trim() && denials[code]?.trim())) return true;
  return (
    action === 'publish' &&
    (!packageReady ||
      Object.values(denials).some((value) => value?.trim()) ||
      gateBlockers(gates, waivers).length > 0)
  );
}

export function SkillGateDecisions({
  action,
  gates,
  waivers,
  denials,
  disabled,
  onWaiver,
  onDenial,
}: {
  action: string;
  gates?: GateResult | null;
  waivers: GateWaivers;
  denials: GateWaivers;
  disabled: boolean;
  onWaiver: (code: 'QG1' | 'QG3', value: string) => void;
  onDenial: (code: 'QG1' | 'QG3', value: string) => void;
}) {
  const conflict = (['QG1', 'QG3'] as const).some((code) => waivers[code]?.trim() && denials[code]?.trim());
  return (
    <section aria-label={copy.r3_main_five_checks} className={styles.stack}>
      {action === 'publish' && <GovernanceFacts gates={gates} />}
      <GateChecklist gates={gates} professional waivers={waivers} onWaiver={onWaiver} disabled={disabled} />
      {conflict && <Alert type="warning" title={copy.r4a1_legacy_decision_conflict} />}
      {action === 'return' &&
        gates?.checks
          .filter((check) => check.application_reason && (check.code === 'QG1' || check.code === 'QG3'))
          .map((check) => (
            <label className={styles.field} key={check.code}>
              {check.label} · {copy.r4a1_legacy_denial}
              <Input.TextArea
                aria-label={`${check.label} · ${copy.r4a1_legacy_denial}`}
                value={denials[check.code as 'QG1' | 'QG3'] || ''}
                disabled={disabled}
                rows={2}
                maxLength={500}
                placeholder={copy.r4a1_legacy_denial_hint}
                onChange={(event) => onDenial(check.code as 'QG1' | 'QG3', event.target.value)}
              />
            </label>
          ))}
    </section>
  );
}

export function SkillGateRecords({
  gates,
  activities = [],
}: {
  gates?: GateResult | null;
  activities?: { action: string; detail: string }[];
}) {
  return (
    <section className={styles.stack}>
      <GateChecklist gates={gates} professional />
      <h3>{copy.r2_card_activity}</h3>
      <ul className={styles.checks}>
        {activities.map((activity, index) => (
          <li key={index}>
            <strong>{activity.action}</strong>
            <p>{activity.detail}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
