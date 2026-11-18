import { Alert, Button, Checkbox, Input } from 'antd';
import { useState } from 'react';
import type { ElementEntity, JsonValue } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { prepareRuleValue } from './ruleEditorValues';
import styles from '../build/Build.module.css';

const copy = terms.labels;
export function RuleEditor({
  entity,
  disabled,
  onSave,
}: {
  entity: ElementEntity;
  disabled: boolean;
  onSave: (payload: ElementEntity['payload'], sentenceConfirmed: boolean) => Promise<void>;
}) {
  const field = entity.kind === 'metric' ? 'formula' : 'judgement';
  const [value, setValue] = useState(() => JSON.stringify(entity.payload[field] || {}, null, 2));
  const [casesText, setCasesText] = useState(() => JSON.stringify(entity.payload.test_cases || [], null, 2));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [sentenceConfirmed, setSentenceConfirmed] = useState(false);
  async function save() {
    if (disabled || busy) return;
    let parsed: JsonValue;
    let cases: JsonValue;
    try {
      parsed = JSON.parse(value);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      if (field === 'formula' && 'test_cases' in parsed) {
        setError(copy.r4_metric_cases_separate);
        return;
      }
    } catch {
      setError(copy.r4_rule_json_error);
      return;
    }
    if (field === 'formula') {
      try {
        cases = JSON.parse(casesText);
        if (
          !Array.isArray(cases) ||
          cases.some(
            (entry) =>
              !entry ||
              typeof entry !== 'object' ||
              Array.isArray(entry) ||
              typeof entry.name !== 'string' ||
              !entry.name.trim() ||
              !entry.input ||
              typeof entry.input !== 'object' ||
              Array.isArray(entry.input) ||
              !Object.hasOwn(entry, 'expected'),
          )
        )
          throw new Error();
      } catch {
        setError(copy.r4_metric_cases_invalid);
        return;
      }
    }
    setBusy(true);
    setError('');
    try {
      await onSave(
        {
          ...entity.payload,
          [field]: prepareRuleValue(parsed, entity.payload[field]),
          ...(field === 'formula' ? { test_cases: cases } : {}),
        },
        sentenceConfirmed,
      );
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.stack} data-rule-editor>
      <p>{copy.r4_rule_edit_hint}</p>
      {field === 'judgement' && <p>{copy.r4a1_legacy_rule_cases_hint}</p>}
      {error && <Alert role="alert" type="error" title={error} />}
      {saved && <Alert role="status" type="success" title={copy.r4_rule_saved} />}
      <label className={styles.field}>
        {field === 'formula' ? copy.r4_rule_formula : copy.r4_rule_judgement}
        <Input.TextArea
          aria-label={field === 'formula' ? copy.r4_rule_formula : copy.r4_rule_judgement}
          value={value}
          disabled={disabled || busy}
          rows={12}
          maxLength={64000}
          onChange={(event) => {
            setValue(event.target.value);
            setSaved(false);
            setSentenceConfirmed(false);
          }}
        />
      </label>
      {field === 'formula' && (
        <label className={styles.field}>
          {copy.r4_rule_cases}
          <p>{copy.r4_metric_cases_hint}</p>
          <Input.TextArea
            aria-label={copy.r4_rule_cases}
            value={casesText}
            rows={8}
            maxLength={64000}
            disabled={disabled || busy}
            onChange={(event) => {
              setCasesText(event.target.value);
              setSaved(false);
            }}
          />
        </label>
      )}
      <Checkbox
        checked={sentenceConfirmed}
        disabled={disabled || busy}
        onChange={(event) => setSentenceConfirmed(event.target.checked)}
      >
        {copy.r4_rule_sentence_confirm}
      </Checkbox>
      <Button
        autoInsertSpace={false}
        aria-label={copy.r4_rule_save}
        disabled={disabled || !value.trim()}
        loading={busy}
        onClick={() => void save()}
      >
        {copy.r4_rule_save}
      </Button>
    </section>
  );
}
