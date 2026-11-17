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

it('edits metric test cases at the payload root while keeping the formula schema separate', async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const metric = {
    ...entity,
    kind: 'metric',
    payload: { schema: 'metric/1', object_type: 'object-1', formula: { oxl: 'amount * 2' }, test_cases: [] },
  };
  render(<RuleEditor entity={metric} disabled={false} onSave={onSave} />);
  const cases = [
    { name: '正额', input: { amount: 2 }, expected: 4 },
    { name: '零额', input: { amount: 0 }, expected: 0 },
  ];
  fireEvent.change(screen.getByRole('textbox', { name: terms.labels.r4_rule_cases }), {
    target: { value: JSON.stringify(cases) },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith({ ...metric.payload, test_cases: cases }, false));
  expect(onSave.mock.calls[0][0].formula).toEqual({ oxl: 'amount * 2' });
});
it('rejects metric cases accidentally nested under formula and invalid root input records', async () => {
  const onSave = vi.fn();
  render(
    <RuleEditor
      entity={{ ...entity, kind: 'metric', payload: { formula: { oxl: 'amount' }, test_cases: [] } }}
      disabled={false}
      onSave={onSave}
    />,
  );
  const formula = screen.getByRole('textbox', { name: terms.labels.r4_rule_formula });
  fireEvent.change(formula, { target: { value: JSON.stringify({ oxl: 'amount', test_cases: [] }) } });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  expect(await screen.findByRole('alert')).toHaveTextContent(terms.labels.r4_metric_cases_separate);
  fireEvent.change(formula, { target: { value: JSON.stringify({ oxl: 'amount' }) } });
  fireEvent.change(screen.getByRole('textbox', { name: terms.labels.r4_rule_cases }), {
    target: { value: JSON.stringify([{ name: '错误输入', input: [], expected: 0 }]) },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_save }));
  expect(await screen.findByRole('alert')).toHaveTextContent(terms.labels.r4_metric_cases_invalid);
  expect(onSave).not.toHaveBeenCalled();
});
