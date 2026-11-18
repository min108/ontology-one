import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { Proposal, ProposalItem } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { ChangesetFacts, ChangesetOmissions, validCalendarDate } from './ChangesetFacts';
import { GateApplications } from './GateApplications';
import { gates } from './gateFixtures';
const item = (id: string): ProposalItem => ({
  id,
  tier: 'sure',
  decision: 'pending',
  one_liner: id,
  rationale: '',
  content_origin: 'model',
  validation: { level: 'ok', issues: [] },
  evidence: [],
  after_data: { id, title: id, kind: 'rule', content: 'A rule', source_quotes: [] },
  gates: gates(['QG3']),
});
const proposal = (): Proposal => ({
  id: 'CS-1',
  title: 'Policy',
  status: 'open',
  summary: '',
  can_submit: false,
  source_text: 'Original policy',
  source_material_id: 'M-1',
  document_facts: { valid_from: { value: '', origin: 'source' }, scope: { value: '', origin: 'source' } },
  items: [item('A'), item('B')],
});
const wrap = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider theme={{ token: { motion: false } }}>
    <MemoryRouter>{children}</MemoryRouter>
  </ConfigProvider>
);
it('does not invent a file date and displays the source quote for an extracted date', () => {
  const value = proposal();
  const confirm = vi.fn();
  const { rerender } = render(<ChangesetFacts proposal={value} disabled={false} onConfirm={confirm} />, {
    wrapper: wrap,
  });
  expect(screen.getByLabelText(terms.labels.r4a1_file_date)).toHaveValue('');
  expect(screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply })).toBeDisabled();
  expect(confirm).not.toHaveBeenCalled();
  value.document_facts!.valid_from = {
    value: '2026-01-01',
    origin: 'source',
    quote: 'This policy takes effect 2026-01-01.',
  };
  rerender(<ChangesetFacts key="extracted" proposal={value} disabled={false} onConfirm={confirm} />);
  expect(screen.getByLabelText(terms.labels.r4a1_file_date)).toHaveValue('2026-01-01');
  expect(screen.getByText(value.document_facts!.valid_from.quote!)).toBeVisible();
});
it('explicit batch confirmation sends only missing active rules and their version guards', () => {
  const value = proposal();
  value.items[0].applied_entity_id = 'R-1';
  value.items[0].applied_version = 4;
  value.items[1].after_data.exceptions = 'A specific exception';
  value.items.push(
    { ...item('rejected'), decision: 'rejected' },
    {
      ...item('ambiguous'),
      after_data: { ...item('ambiguous').after_data, bulk_confirmation_blocked: true },
    },
    { ...item('submitted'), applied_entity_id: 'R-2', applied_status: 'review' },
    { ...item('data'), after_data: { ...item('data').after_data, kind: 'data' } },
  );
  const confirm = vi.fn();
  render(<ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />, { wrapper: wrap });
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_confirm_exceptions }));
  expect(confirm).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'CS-1',
    confirm_no_exceptions_item_ids: ['A'],
    expected_versions: { A: 4 },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_confirm_conditions }));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_unconditional_item_ids: ['A', 'B'],
    expected_versions: { A: 4 },
  });
});
it('applications preserve the edited snapshot, never waive a check, and exclude hard gates', () => {
  const value = item('A');
  value.applied_version = 2;
  value.gates = gates(['QG1', 'QG2', 'QG3', 'QG4', 'QG5']);
  const save = vi.fn();
  const { rerender } = render(<GateApplications item={value} disabled={false} onSave={save} />, {
    wrapper: wrap,
  });
  fireEvent.click(screen.getByRole('button', { name: new RegExp(terms.labels.r4a1_gate_application) }));
  const inputs = screen.getAllByRole('textbox');
  expect(inputs).toHaveLength(2);
  fireEvent.change(inputs[0], { target: { value: 'Explain source limitation' } });
  fireEvent.change(inputs[1], { target: { value: 'Explain applicability limitation' } });
  rerender(<GateApplications item={{ ...value, applied_version: 3 }} disabled={false} onSave={save} />);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_gate_application_save }));
  expect(save).toHaveBeenCalledWith(value, {
    QG1: 'Explain source limitation',
    QG3: 'Explain applicability limitation',
  });
  expect(value.gates.checks.every((check) => check.status === 'fail')).toBe(true);
});

it('rejects invalid calendar dates without normalizing them to a different effective date', () => {
  expect(validCalendarDate('2026-02-28')).toBe(true);
  for (const value of ['nonsense', '2026-02-31', '2026-13-01', '2026-1-1'])
    expect(validCalendarDate(value)).toBe(false);
  const value = proposal();
  value.document_facts!.valid_from.value = '2026-02-31';
  render(<ChangesetFacts proposal={value} disabled={false} onConfirm={vi.fn()} />, { wrapper: wrap });
  expect(screen.getByLabelText(terms.labels.r4a1_file_date)).toHaveValue('');
  expect(screen.getByRole('alert')).toHaveTextContent(terms.labels.r4a1_invalid_date);
  expect(screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply })).toBeDisabled();
});
