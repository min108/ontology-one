import { Alert, Button, Input } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { invokeCapability } from '../../api/client';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import type {
  ElementSubmit,
  ElementDetail,
  ElementDetailResult,
  ElementUpdate,
  GateWaivers,
} from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { BusinessDatePicker } from '../../ui/BusinessDatePicker';
import { useLifetime } from '../build/shared';
import { businessText } from '../build/businessText';
import { AuthoritySubjectSelect, SubjectSelect } from './SubjectSelect';
import styles from '../build/Build.module.css';

const copy = terms.labels;
const editable = new Set([
  'definition',
  'unit',
  'grain',
  'scope',
  'conditions',
  'exceptions',
  'valid_from',
  'valid_to',
  'boundary',
  'usage_conditions',
]);

export function ElementRepair({
  detail,
  disabled,
  onSaved,
}: {
  detail: ElementDetail;
  disabled: boolean;
  onSaved: (message: string) => void;
}) {
  const { entity, gates } = detail;
  const fields = entity.payload.managed_skill
    ? []
    : (gates?.fields || []).filter((field) => editable.has(field.key));
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((field) => [field.key, field.value])),
  );
  const [subject, setSubject] = useState(businessText(entity.payload.applies_to_id));
  const spec = entity.payload.authority_spec;
  const authority = spec && typeof spec === 'object' && !Array.isArray(spec) ? spec : null;
  const originalSubjects = authority
    ? [
        ...new Set(
          ['org_ids', 'person_ids'].flatMap((key) => {
            const ids = (authority as Record<string, unknown>)[key];
            return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : [];
          }),
        ),
      ]
    : [];
  const [subjects, setSubjects] = useState(originalSubjects);
  const subjectsChanged =
    JSON.stringify([...subjects].sort()) !== JSON.stringify([...originalSubjects].sort());
  const missingAuthorityOrg =
    !!authority &&
    subjectsChanged &&
    !subjects.some((id) =>
      detail.applicability_options?.some((option) => option.id === id && option.kind === 'organization'),
    );
  const [applications, setApplications] = useState<GateWaivers>(() =>
    Object.fromEntries(
      (gates?.checks || [])
        .filter((check) => check.application_reason && ['QG1', 'QG3'].includes(check.code))
        .map((check) => [check.code, check.application_reason]),
    ),
  );
  const [reason, setReason] = useState(copy.r4a1_legacy_reason_default);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const lifetime = useLifetime();
  const changed =
    fields.some((field) => values[field.key] !== field.value) ||
    subject !== businessText(entity.payload.applies_to_id) ||
    subjectsChanged ||
    (gates?.checks || []).some(
      (check) =>
        (check.code === 'QG1' || check.code === 'QG3') &&
        (applications[check.code] || '') !== (check.application_reason || ''),
    );
  useWorkspaceGuard(changed);
  async function save() {
    const signal = lifetime.current?.signal;
    if (busy || disabled || missingAuthorityOrg || !reason.trim() || signal?.aborted) return;
    setBusy(true);
    setError('');
    const payload: ElementDetail['entity']['payload'] =
      entity.payload.authority_spec || entity.payload.managed_skill ? {} : { ...entity.payload };
    const meta: NonNullable<ElementUpdate['meta']> = {};
    for (const field of fields) {
      if (values[field.key] === field.value) continue;
      const value = (values[field.key] || '').trim();
      if (field.key === 'valid_from' || field.key === 'valid_to') meta[field.key] = value;
      else
        payload[field.key] =
          field.key === 'conditions'
            ? value
                .split('\n')
                .map((row) => row.trim())
                .filter(Boolean)
            : value;
    }
    if (entity.kind === 'rule' && authority && subjectsChanged) payload.applicable_subject_ids = subjects;
    else if (entity.kind === 'rule' && !authority && subject !== businessText(entity.payload.applies_to_id))
      payload.applies_to_id = subject;
    try {
      await invokeCapability<ElementDetailResult>(
        'element.update_draft',
        {
          entity_id: entity.id,
          version: entity.version,
          payload,
          meta,
          waiver_applications: Object.fromEntries(
            Object.entries(applications)
              .filter(([, value]) => value?.trim())
              .map(([code, value]) => [code, value?.trim()]),
          ),
          reason: reason.trim(),
        } satisfies ElementUpdate,
        { signal },
      );
      if (!signal?.aborted) onSaved(copy.r4a1_legacy_saved);
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  async function submit() {
    const signal = lifetime.current?.signal;
    if (busy || disabled || changed || !gates?.ready_to_submit || signal?.aborted) return;
    setBusy(true);
    setError('');
    try {
      await invokeCapability<ElementDetailResult>(
        'element.submit_draft',
        {
          entity_id: entity.id,
          version: entity.version,
          reason: reason.trim() || copy.r4a1_legacy_submit,
        } satisfies ElementSubmit,
        { signal },
      );
      if (!signal?.aborted) onSaved(copy.r4a1_legacy_submitted);
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <section
      id="element-repair"
      tabIndex={-1}
      className={`${styles.card} ${styles.stack}`}
      aria-label={copy.r4a1_legacy_repair_title}
    >
      <h2>{copy.r4a1_legacy_repair_title}</h2>
      <p>{copy.r4a1_legacy_repair_note}</p>
      {!!entity.payload.managed_skill && (
        <p>
          {copy.r4a1_legacy_skill_handoff}{' '}
          <Link to={'/skill-development/' + encodeURIComponent(entity.id)}>
            {copy.r4a1_legacy_skill_link}
          </Link>
        </p>
      )}
      {error && <Alert type="error" title={error} role="alert" />}
      {fields.map((field) => (
        <label className={styles.field} key={field.key}>
          {field.label}
          {field.key === 'valid_from' || field.key === 'valid_to' ? (
            <BusinessDatePicker
              aria-label={field.label}
              value={values[field.key] || ''}
              disabled={disabled || busy}
              onChange={(value) => setValues((old) => ({ ...old, [field.key]: value }))}
            />
          ) : (
            <Input.TextArea
              aria-label={field.label}
              value={values[field.key] || ''}
              disabled={disabled || busy}
              rows={2}
              maxLength={8000}
              onChange={(event) => setValues((old) => ({ ...old, [field.key]: event.target.value }))}
            />
          )}
        </label>
      ))}
      {entity.kind === 'rule' && (
        <label className={styles.field}>
          {copy.r4a1_legacy_subject}
          {authority ? (
            <AuthoritySubjectSelect
              value={subjects}
              options={detail.applicability_options || []}
              disabled={disabled || busy}
              onChange={setSubjects}
            />
          ) : (
            <SubjectSelect
              value={subject}
              options={detail.applicability_options || []}
              disabled={disabled || busy}
              onChange={setSubject}
            />
          )}
        </label>
      )}
      {authority && (
        <p>
          {copy.r4a1_authority_subject_hint}{' '}
          <Link to={'/authority/' + encodeURIComponent(entity.id)}>{copy.shell_authority}</Link>
        </p>
      )}
      {missingAuthorityOrg && (
        <Alert type="warning" title={copy.r4a1_authority_subject_required} role="alert" />
      )}
      {(gates?.checks || [])
        .filter(
          (check) =>
            check.status === 'fail' && check.can_waive && (check.code === 'QG1' || check.code === 'QG3'),
        )
        .map((check) => (
          <label key={check.code} className={styles.field} data-waiver-application-code={check.code}>
            {check.label} · {copy.r4a1_legacy_application}
            <Input.TextArea
              aria-label={`${check.label} · ${copy.r4a1_legacy_application}`}
              rows={2}
              maxLength={500}
              disabled={disabled || busy}
              value={applications[check.code as 'QG1' | 'QG3'] || ''}
              placeholder={copy.r4a1_legacy_application_hint}
              onChange={(event) => setApplications((old) => ({ ...old, [check.code]: event.target.value }))}
            />
            <span>{copy.r4a1_legacy_application_pending}</span>
          </label>
        ))}
      <label className={styles.field}>
        {copy.r4a1_legacy_reason}
        <Input
          value={reason}
          aria-label={copy.r4a1_legacy_reason}
          maxLength={500}
          disabled={disabled || busy}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <p>{copy.r4a1_legacy_sod}</p>
      {(gates?.submission_blockers || []).map((blocker, index) => (
        <p key={index}>{blocker}</p>
      ))}
      <div className={styles.actions}>
        <Button
          autoInsertSpace={false}
          disabled={disabled || busy || missingAuthorityOrg || !reason.trim()}
          onClick={() => void save()}
        >
          {copy.r4a1_legacy_save}
        </Button>
        <Button
          type="primary"
          autoInsertSpace={false}
          disabled={disabled || busy || changed || !gates?.ready_to_submit}
          onClick={() => void submit()}
        >
          {copy.r4a1_legacy_submit}
        </Button>
        <Link to={'/assets/' + encodeURIComponent(entity.id)}>{copy.r4a1_legacy_old_link}</Link>
      </div>
    </section>
  );
}
