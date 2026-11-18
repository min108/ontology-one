import { expect, it } from 'vitest';
import { prepareRuleValue } from './ruleEditorValues';
import { terms } from '../../i18n/zh-CN';

it('removes a stale sentence after an expression change without mutating the input', () => {
  const previous = { requirement: { oxl: 'amount > 0', sentence: 'Amount must be positive' } };
  const edited = { requirement: { oxl: 'amount >= 0', sentence: 'Amount must be positive' } };
  expect(prepareRuleValue(edited, previous)).toEqual({ requirement: { oxl: 'amount >= 0' } });
  expect(edited.requirement.sentence).toBe('Amount must be positive');
  // Counterexample: an explicitly rewritten sentence must survive for server confirmation.
  expect(
    prepareRuleValue({ requirement: { oxl: 'amount >= 0', sentence: 'Amount can be zero' } }, previous),
  ).toEqual({ requirement: { oxl: 'amount >= 0', sentence: 'Amount can be zero' } });
  expect(prepareRuleValue(previous, previous)).toEqual(previous);
});
it('accepts displayed Chinese outcomes while leaving invalid values for strict server rejection', () => {
  const cases = [
    terms.labels.r4_rule_outcome_pass,
    terms.labels.r4_rule_outcome_violate,
    terms.labels.r4_gate_not_applicable,
    terms.labels.r4_rule_outcome_review,
    'invalid',
  ].map((expected) => ({ name: 'case', input: {}, expected }));
  expect(prepareRuleValue({ test_cases: cases }, {})).toEqual({
    test_cases: cases.map((item, index) => ({
      ...item,
      expected: ['pass', 'violate', 'not_applicable', 'needs_review', 'invalid'][index],
    })),
  });
  expect(prepareRuleValue({ requirement: null }, {})).toEqual({ requirement: null });
});
