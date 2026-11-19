import { Button, Checkbox, DatePicker, Radio } from 'antd';
import dayjs from 'dayjs';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Proposal, ProposalItem, ConfirmFacts } from '../../api/generated/types';
import { FoldRow } from '../../ui';
import { copy, sourceHref } from './shared';
import { sentence, isSubmitted, isHandledElsewhere, isAnchoredEvidence } from './model';
import styles from './Build.module.css';

export const validCalendarDate = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(value) && dayjs(value).isValid() && dayjs(value).format('YYYY-MM-DD') === value;

export function ChangesetFacts({
  proposal,
  disabled,
  onConfirm,
}: {
  proposal: Proposal;
  disabled: boolean;
  onConfirm: (body: ConfirmFacts) => void;
}) {
  const fact = proposal.document_facts?.valid_from;
  const candidates = proposal.document_facts?.valid_from_candidates || [];
  const onlyDateRecognized =
    candidates.length === 1 &&
    fact?.origin === 'system' &&
    !!fact.value &&
    fact.value === candidates[0].value;
  const savedAnchor = fact?.method === 'selection' ? fact.candidate_id || fact.anchor_id || '' : '';
  const [candidateId, setCandidateId] = useState(savedAnchor);
  const [date, setDate] = useState(fact?.value || '');
  const origin =
    fact?.method === 'selection'
      ? copy.r4a2_origin_selection
      : fact?.origin === 'manual'
        ? copy.r4a1_origin_manual
        : fact?.origin === 'source'
          ? copy.r4a1_origin_source
          : copy.r4a2_origin_system;
  const changed = candidateId ? candidateId !== savedAnchor : date !== (fact?.value || '');
  return (
    <div data-document-facts className={styles['file-facts']}>
      <div className={styles['document-facts']}>
        <label htmlFor="file-valid-from">{copy.r4a1_file_date}</label>
        <DatePicker
          id="file-valid-from"
          data-file-valid-from
          aria-label={copy.r4a1_file_date}
          value={validCalendarDate(date) ? dayjs(date) : null}
          disabled={disabled}
          placeholder={copy.r4_rule_date_hint}
          onChange={(value) => {
            setCandidateId('');
            setDate(value?.format('YYYY-MM-DD') || '');
          }}
        />
        <Button
          disabled={disabled || (!!date && !validCalendarDate(date)) || !changed}
          onClick={() =>
            onConfirm({
              changeset_id: proposal.id,
              ...(candidateId ? { valid_from_candidate_id: candidateId } : { valid_from: date }),
              expected_versions: Object.fromEntries(
                proposal.items
                  .filter((item) => item.applied_version)
                  .map((item) => [item.id, item.applied_version!]),
              ),
            })
          }
        >
          {copy.r4a1_file_date_apply}
        </Button>
        {date && !validCalendarDate(date) && <span role="alert">{copy.r4a1_invalid_date}</span>}
        {fact?.value ? (
          <span className={styles.muted} data-document-date-origin>
            {origin}
            {fact.confirmed_by && ` · ${fact.confirmed_by}`}
          </span>
        ) : (
          !candidates.length && <span className={styles.muted}>{copy.r4a1_file_date_missing}</span>
        )}
        {fact?.quote &&
          (isAnchoredEvidence(fact) ? (
            <Link to={sourceHref(fact)}>{fact.quote}</Link>
          ) : (
            <span className={styles.muted}>{fact.quote}</span>
          ))}
      </div>
      {candidates.length > 0 && !onlyDateRecognized && (
        <fieldset className={styles['date-candidates']}>
          <legend>{copy.r4a2_date_candidates}</legend>
          {candidates.map((candidate) => (
            <div className={styles['date-option']} key={candidate.candidate_id || candidate.anchor_id}>
              <Radio
                name="valid-from-candidate"
                checked={candidateId === (candidate.candidate_id || candidate.anchor_id)}
                disabled={disabled}
                onChange={() => {
                  setCandidateId(candidate.candidate_id || candidate.anchor_id || '');
                  setDate(candidate.value || '');
                }}
              >
                {candidate.quote}
              </Radio>
              {isAnchoredEvidence(candidate) && (
                <Link to={sourceHref(candidate)}>{copy.r3_main_source_page}</Link>
              )}
            </div>
          ))}
        </fieldset>
      )}
    </div>
  );
}

export function ChangesetOmissions({
  proposal,
  disabled,
  onConfirm,
}: {
  proposal: Proposal;
  disabled: boolean;
  onConfirm: (body: ConfirmFacts) => void;
}) {
  const rules = proposal.items.filter(
    (item) =>
      item.after_data.kind === 'rule' &&
      !item.after_data.bulk_confirmation_blocked &&
      item.decision !== 'rejected' &&
      !isHandledElsewhere(item) &&
      !isSubmitted(item),
  );
  const sourceDeclares = (item: ProposalItem, field: 'conditions' | 'exceptions') =>
    Array.isArray(item.after_data.source_declared_fields) &&
    item.after_data.source_declared_fields.includes(field);
  const groups = [
    {
      ids: rules.filter((item) => !sourceDeclares(item, 'exceptions') && !item.after_data.exceptions?.trim()),
      field: 'confirm_no_exceptions_item_ids' as const,
      label: copy.r4a1_missing_exceptions,
      action: copy.r4a1_confirm_exceptions,
      selector: 'exceptions',
    },
    {
      ids: rules.filter(
        (item) =>
          !sourceDeclares(item, 'conditions') &&
          !item.after_data.conditions?.some((condition) => condition.trim()),
      ),
      field: 'confirm_unconditional_item_ids' as const,
      label: copy.r4a1_missing_conditions,
      action: copy.r4a1_confirm_conditions,
      selector: 'conditions',
    },
  ];
  return (
    <>
      {groups
        .filter((group) => group.ids.length)
        .map((group) => (
          <OmissionGroup
            key={proposal.id + group.field}
            proposalId={proposal.id}
            items={group.ids}
            field={group.field}
            label={group.label}
            action={group.action}
            selector={group.selector}
            disabled={disabled}
            onConfirm={onConfirm}
          />
        ))}
    </>
  );
}

function omissionSnapshot(item: ProposalItem) {
  return JSON.stringify([
    item.id,
    item.applied_version,
    item.after_data.title,
    item.after_data.content,
    item.evidence.map(({ material_id, source_version, anchor_id, quote }) => [
      material_id,
      source_version,
      anchor_id,
      quote,
    ]),
  ]);
}

function OmissionGroup({
  proposalId,
  items,
  field,
  label,
  action,
  selector,
  disabled,
  onConfirm,
}: {
  proposalId: string;
  items: ProposalItem[];
  field: 'confirm_no_exceptions_item_ids' | 'confirm_unconditional_item_ids';
  label: string;
  action: string;
  selector: string;
  disabled: boolean;
  onConfirm: (body: ConfirmFacts) => void;
}) {
  const [checks, setChecks] = useState<Record<string, string>>({});
  const selected = items.filter((item) => checks[item.id] === omissionSnapshot(item));
  return (
    <div data-omission-group={selector}>
      <FoldRow title={sentence(label, { count: items.length })} summary={null}>
        <p className={styles.muted}>{copy.r4a1_omissions_hint}</p>
        <ul className={styles.checks}>
          {items.map((item) => (
            <li key={item.id} data-omission-item={item.id} className={styles['omission-row']}>
              <Checkbox
                checked={selected.includes(item)}
                disabled={disabled}
                onChange={(event) =>
                  setChecks((old) => ({
                    ...old,
                    [item.id]: event.target.checked ? omissionSnapshot(item) : '',
                  }))
                }
              >
                {item.after_data.title}
              </Checkbox>
              <div className={styles['omission-evidence']}>
                {item.evidence.some((source) => source.quote.trim()) ? (
                  item.evidence
                    .filter((source) => source.quote.trim())
                    .map((source, index) => (
                      <div key={index}>
                        <blockquote className={styles.quote}>{source.quote}</blockquote>
                        <Link to={sourceHref(source)}>{copy.r3_main_source_page}</Link>
                      </div>
                    ))
                ) : (
                  <p>{copy.r4a1_omission_quote_missing}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
        <span className={styles.muted}>
          {sentence(copy.r4a1_omission_selected, { count: selected.length, total: items.length })}
        </span>
        <Button
          data-confirm-no-exceptions={selector === 'exceptions' || undefined}
          data-confirm-unconditional={selector === 'conditions' || undefined}
          disabled={disabled || !selected.length}
          onClick={() => {
            if (disabled || !selected.length) return;
            onConfirm({
              changeset_id: proposalId,
              [field]: selected.map((item) => item.id),
              expected_versions: Object.fromEntries(
                selected
                  .filter((item) => item.applied_version)
                  .map((item) => [item.id, item.applied_version!]),
              ),
            });
          }}
        >
          {action}
        </Button>
      </FoldRow>
    </div>
  );
}
