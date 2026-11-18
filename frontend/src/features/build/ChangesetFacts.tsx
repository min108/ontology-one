import { Button, DatePicker } from 'antd';
import dayjs from 'dayjs';
import { useState } from 'react';
import type { Proposal, ConfirmFacts } from '../../api/generated/types';
import { FoldRow } from '../../ui';
import { copy } from './shared';
import { sentence, isSubmitted } from './model';
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
      <span className={styles.muted}>{fact?.quote || copy.r4a1_file_date_missing}</span>
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
    (item) => item.after_data.kind === 'rule' && item.decision !== 'rejected' && !isSubmitted(item),
  );
  const groups = [
    {
      ids: rules.filter((item) => !item.after_data.exceptions?.trim()),
      field: 'confirm_no_exceptions_item_ids' as const,
      label: copy.r4a1_missing_exceptions,
      action: copy.r4a1_confirm_exceptions,
      selector: 'exceptions',
    },
    {
      ids: rules.filter((item) => !item.after_data.conditions?.some((condition) => condition.trim())),
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
          <FoldRow
            key={group.field}
            title={sentence(group.label, { count: group.ids.length })}
            summary={copy.r4a1_omissions_hint}
            defaultOpen
          >
            <ul className={styles.checks}>
              {group.ids.map((item) => (
                <li key={item.id}>{item.after_data.title}</li>
              ))}
            </ul>
            <Button
              data-confirm-no-exceptions={group.selector === 'exceptions' || undefined}
              data-confirm-unconditional={group.selector === 'conditions' || undefined}
              disabled={disabled}
              onClick={() =>
                onConfirm({
                  changeset_id: proposal.id,
                  [group.field]: group.ids.map((item) => item.id),
                  expected_versions: Object.fromEntries(
                    group.ids
                      .filter((item) => item.applied_version)
                      .map((item) => [item.id, item.applied_version!]),
                  ),
                })
              }
            >
              {group.action}
            </Button>
          </FoldRow>
        ))}
    </>
  );
}
