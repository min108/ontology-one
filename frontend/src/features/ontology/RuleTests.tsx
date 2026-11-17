import { Alert, Button } from 'antd';
import { useState } from 'react';
import { invokeCapability } from '../../api/client';
import type { ElementGet, RuleCasesResult } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { useSessionStore } from '../../state/session';
import { FoldRow } from '../../ui';
import { sentence } from '../build/model';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
const outcomeLabels: Record<string, string> = {
  pass: copy.r4_rule_test_pass,
  fail: copy.r4_rule_test_fail,
  not_applicable: copy.r4_gate_not_applicable,
  needs_review: copy.r3_main_needs_confirm,
};
function outcome(value: unknown) {
  if (typeof value === 'string') return outcomeLabels[value] || value;
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? copy.r4_rule_test_pass : copy.r4_rule_test_fail;
  return copy.r3_main_needs_confirm;
}
export function RuleTests({
  id,
  disabled,
  onComplete,
}: {
  id: string;
  disabled: boolean;
  onComplete: () => void;
}) {
  const readonly = useSessionStore((state) => !!state.session?.workspace.readonly);
  const [result, setResult] = useState<RuleCasesResult['data']>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lifetime = useLifetime();
  async function run() {
    if (disabled || readonly || busy || lifetime.current?.signal.aborted) return;
    const signal = lifetime.current?.signal;
    setBusy(true);
    setError('');
    try {
      const response = await invokeCapability<RuleCasesResult>(
        'rule.test_cases',
        { entity_id: id } satisfies ElementGet,
        { signal },
      );
      if (signal?.aborted) return;
      setResult(response.data);
      onComplete();
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <FoldRow
      title={copy.r4_rule_cases}
      summary={
        result
          ? sentence(copy.r4_rule_test_summary, { passed: result.passed_count, total: result.total })
          : copy.r4_rule_run
      }
      defaultOpen
    >
      <div className={styles.stack}>
        {error && <Alert role="alert" type="error" title={error} />}
        <Button
          autoInsertSpace={false}
          aria-label={copy.r4_rule_run}
          disabled={disabled || readonly}
          loading={busy}
          onClick={() => void run()}
        >
          {copy.r4_rule_run}
        </Button>
        {readonly && <p>{copy.r3_main_read_only}</p>}
        {result && (
          <div data-rule-test-result data-passed={result.passed} role="status">
            <p>{sentence(copy.r4_rule_test_summary, { passed: result.passed_count, total: result.total })}</p>
            <ul>
              {result.results.map((row, i) => (
                <li key={i} data-rule-test-case data-passed={row.passed}>
                  <strong>
                    {row.name} · {row.passed ? copy.r4_rule_test_pass : copy.r4_rule_test_fail}
                  </strong>
                  <p>
                    {copy.r4_rule_expected}：{outcome(row.expected)}；{copy.r4_rule_actual}：
                    {outcome(row.actual)}
                  </p>
                  {row.error && <p>{row.error}</p>}
                </li>
              ))}
            </ul>
            {result.errors.map((message, i) => (
              <p key={i}>{message}</p>
            ))}
          </div>
        )}
      </div>
    </FoldRow>
  );
}
