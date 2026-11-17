import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ElementEntity } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { RuleEditor } from './RuleEditor';
const entity: ElementEntity = {
  id: 'rule-1',
  kind: 'rule',
  title: '付款检查',
  version: 1,
  status: 'draft',
  domain: '',
  activities: [],
  payload: {
    judgement: {
      condition: { oxl: 'amount > 0', sentence: '金额大于零' },
      requirement: { oxl: 'approved == true' },
      test_cases: [],
    },
    scope: '本机构付款',
  },
};
it('saves two actual cases and preserves other payload fields without silently confirming the sentence', async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<RuleEditor entity={entity} disabled={false} onSave={onSave} />);
  const judgement = {
    condition: { oxl: 'amount > 0', sentence: '金额大于零' },
    requirement: { oxl: 'approved == true' },
    test_cases: [
      { name: '适用通过', input: { amount: 1, approved: true }, expected: 'pass' },
      { name: '不适用', input: { amount: 0, approved: false }, expected: 'not_applicable' },
    ],
  };
  fireEvent.change(screen.getByLabelText(terms.labels.r4_rule_judgement), {
    target: { value: JSON.stringify(judgement) },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith({ scope: '本机构付款', judgement }, false));
  await screen.findByText(terms.labels.r4_rule_saved);
  fireEvent.click(screen.getByRole('checkbox', { name: terms.labels.r4_rule_sentence_confirm }));
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  await waitFor(() => expect(onSave).toHaveBeenLastCalledWith({ scope: '本机构付款', judgement }, true));
});
it('counterexample: malformed judgement never calls save and retains the edit', async () => {
  const onSave = vi.fn();
  render(<RuleEditor entity={entity} disabled={false} onSave={onSave} />);
  const field = screen.getByLabelText(terms.labels.r4_rule_judgement);
  fireEvent.change(field, { target: { value: '{broken' } });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  expect(await screen.findByRole('alert')).toHaveTextContent(terms.labels.r4_rule_json_error);
  expect(field).toHaveValue('{broken');
  expect(onSave).not.toHaveBeenCalled();
});
