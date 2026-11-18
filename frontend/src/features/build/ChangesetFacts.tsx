import { Button, Checkbox, DatePicker } from 'antd';
import dayjs from 'dayjs';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Proposal, ProposalItem, ConfirmFacts } from '../../api/generated/types';
import { FoldRow, SourceViewer } from '../../ui';
import { copy, sourceHref } from './shared';
import { sentence, isSubmitted, isHandledElsewhere } from './model';
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
  const [date, setDate] = useState(fact?.value || '');
  return (
    <div data-document-facts className={styles['document-facts']}>
      <label htmlFor="file-valid-from">{copy.r4a1_file_date}</label>
      <DatePicker
        id="file-valid-from"
        data-file-valid-from
        aria-label={copy.r4a1_file_date}
        value={validCalendarDate(date) ? dayjs(date) : null}
        disabled={disabled}
        placeholder={copy.r4_rule_date_hint}
        onChange={(value) => setDate(value?.format('YYYY-MM-DD') || '')}
      />
      <Button
        disabled={disabled || (!!date && !validCalendarDate(date)) || date === (fact?.value || '')}
        onClick={() =>
          onConfirm({
            changeset_id: proposal.id,
            valid_from: date,
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
      <span className={styles.muted}>
        {fact?.quote || (fact?.value ? copy.r4a1_origin_manual : copy.r4a1_file_date_missing)}
      </span>
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
        .map((group, index) => (
          <OmissionGroup
            key={proposal.id + group.field}
            proposalId={proposal.id}
            items={group.ids}
            field={group.field}
            label={group.label}
            action={group.action}
            selector={group.selector}
            showHint={index === 0}
            disabled={disabled}
            onConfirm={onConfirm}
          />
        ))}
    </>
  );
}

function OmissionGroup({
  proposalId,
  items,
  field,
  label,
  action,
  selector,
  showHint,
  disabled,
  onConfirm,
}: {
  proposalId: string;
  items: ProposalItem[];
  field: 'confirm_no_exceptions_item_ids' | 'confirm_unconditional_item_ids';
  label: string;
  action: string;
  selector: string;
  showHint: boolean;
  disabled: boolean;
  onConfirm: (body: ConfirmFacts) => void;
}) {
  const [excluded, setExcluded] = useState<string[]>([]);
  const selected = items.filter((item) => !excluded.includes(item.id));
  return (
    <div data-omission-group={selector}>
      <FoldRow
        title={sentence(label, { count: items.length })}
        summary={showHint ? copy.r4a1_omissions_hint : null}
        defaultOpen
      >
        <ul className={styles.checks}>
          {items.map((item) => (
            <li key={item.id} data-omission-item={item.id} className={styles['omission-row']}>
              <Checkbox
                checked={!excluded.includes(item.id)}
                disabled={disabled}
                onChange={(event) =>
                  setExcluded((old) =>
                    event.target.checked ? old.filter((id) => id !== item.id) : [...old, item.id],
                  )
                }
              >
                {item.after_data.title}
              </Checkbox>
              <details className={styles['omission-evidence']}>
                <summary>{copy.r4a1_source_sentence}</summary>
                {item.evidence.some((source) => source.quote.trim()) ? (
                  item.evidence
                    .filter((source) => source.quote.trim())
                    .map((source, index) => (
                      <div key={index}>
                        <SourceViewer text={source.quote} quote={source.quote} />
                        <Link to={sourceHref(source)}>{copy.r3_main_source_page}</Link>
                      </div>
                    ))
                ) : (
                  <p>{copy.r4a1_omission_quote_missing}</p>
                )}
              </details>
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
