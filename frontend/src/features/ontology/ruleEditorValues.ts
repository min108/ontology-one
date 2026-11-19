import type { JsonValue } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';

export function isRuleObject(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/** Keep an explicitly rewritten sentence; let the server regenerate an unchanged stale one. */
export function prepareRuleValue(value: JsonValue, previous: JsonValue | undefined): JsonValue {
  if (!isRuleObject(value)) return value;
  const result = { ...value };
  if (
    isRuleObject(previous) &&
    typeof result.oxl === 'string' &&
    result.oxl !== previous.oxl &&
    result.sentence === previous.sentence
  ) {
    delete result.sentence;
  }
  for (const key of ['condition', 'requirement', 'formula']) {
    if (key in result)
      result[key] = prepareRuleValue(result[key], isRuleObject(previous) ? previous[key] : undefined);
  }
  if (Array.isArray(result.exceptions)) {
    result.exceptions = result.exceptions.map((item, index) =>
      prepareRuleValue(
        item,
        isRuleObject(previous) && Array.isArray(previous.exceptions) ? previous.exceptions[index] : undefined,
      ),
    );
  }
  const outcomes: Record<string, string> = {
    [terms.labels.r4_rule_outcome_pass]: 'pass',
    [terms.labels.r4_rule_outcome_violate]: 'violate',
    [terms.labels.r4_gate_not_applicable]: 'not_applicable',
    [terms.labels.r4_rule_outcome_review]: 'needs_review',
  };
  if (Array.isArray(result.test_cases)) {
    result.test_cases = result.test_cases.map((item) =>
      isRuleObject(item) && typeof item.expected === 'string' && Object.hasOwn(outcomes, item.expected)
        ? { ...item, expected: outcomes[item.expected] }
        : item,
    );
  }
  return result;
}
