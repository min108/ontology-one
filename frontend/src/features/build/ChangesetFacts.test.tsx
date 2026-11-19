import { fireEvent, render, screen, within } from '@testing-library/react';
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
function openOmissionLists() {
  for (const summary of document.querySelectorAll('[data-omission-group] details:not([open]) > summary')) {
    fireEvent.click(summary);
  }
}
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
  value.document_facts!.valid_from = { value: '2026-02-01', origin: 'manual', actor_id: 'builder' };
  rerender(<ChangesetFacts key="manual" proposal={value} disabled={false} onConfirm={confirm} />);
  expect(screen.getByText(terms.labels.r4a1_origin_manual)).toBeVisible();
  expect(screen.queryByText(terms.labels.r4a1_file_date_missing)).not.toBeInTheDocument();
});
it('keeps overlapping date suggestions unselected and sends the chosen source anchor with version guards', () => {
  const value = proposal();
  const candidates = [
    { value: '2026-01-01', quote: 'The original policy started on 1 January.', anchor_id: 'old' },
    { value: '2026-01-01', quote: 'This amended policy starts on 1 January.', anchor_id: 'current' },
  ].map((fact) => ({ ...fact, material_id: 'M-1', source_version: 'V-1', origin: 'system' }));
  value.document_facts!.valid_from_candidates = candidates;
  value.items[1].applied_version = 3;
  const confirm = vi.fn();
  const { rerender } = render(<ChangesetFacts proposal={value} disabled={false} onConfirm={confirm} />, {
    wrapper: wrap,
  });
  const date = screen.getByLabelText(terms.labels.r4a1_file_date);
  const apply = screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply });
  expect(date).toHaveValue('');
  for (const candidate of candidates) {
    expect(screen.getByRole('radio', { name: candidate.quote })).not.toBeChecked();
    expect(screen.getByText(candidate.quote)).toBeVisible();
  }
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('radio', { name: candidates[1].quote }));
  expect(date).toHaveValue(candidates[1].value);
  fireEvent.click(apply);
  expect(confirm).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'CS-1',
    valid_from_candidate_id: 'current',
    expected_versions: { B: 3 },
  });
  value.document_facts!.valid_from = {
    ...candidates[1],
    origin: 'manual',
    method: 'selection',
    confirmed_by: 'Builder Li',
  };
  rerender(<ChangesetFacts key="saved" proposal={value} disabled={false} onConfirm={confirm} />);
  expect(screen.getByRole('radio', { name: candidates[1].quote })).toBeChecked();
  expect(screen.getByText(/核对人选择.*Builder Li/)).toBeVisible();
  expect(screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply })).toBeDisabled();
  fireEvent.click(screen.getByRole('radio', { name: candidates[0].quote }));
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply }));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    valid_from_candidate_id: 'old',
    expected_versions: { B: 3 },
  });
});
it('shows a single confirmed current-file date and its sentence once without asking the builder to choose it again', () => {
  const value = proposal();
  const only = {
    origin: 'system',
    value: '2026-01-01',
    anchor_id: 'effective',
    material_id: 'M-1',
    source_version: 'V-1',
    quote: 'Only the current edition takes effect on this date.',
  };
  value.document_facts!.valid_from = only;
  value.document_facts!.valid_from_candidates = [only];
  render(<ChangesetFacts proposal={value} disabled={false} onConfirm={vi.fn()} />, { wrapper: wrap });
  expect(screen.getByLabelText(terms.labels.r4a1_file_date)).toHaveValue(only.value);
  expect(screen.getByText(terms.labels.r4a2_origin_system)).toBeVisible();
  expect(screen.getByRole('link', { name: only.quote })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=effective&source_version=V-1',
  );
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
});
it('allows a historical proposal to select a stored source position before it has an anchor', () => {
  const value = proposal();
  const candidate = {
    origin: 'system',
    candidate_id: 'position-27',
    quote: 'The amended edition starts on 1 May.',
    value: '2027-05-01',
    source_version: 'V-1',
    material_id: 'M-1',
  };
  value.document_facts!.valid_from_candidates = [candidate];
  const confirm = vi.fn();
  render(<ChangesetFacts proposal={value} disabled={false} onConfirm={confirm} />, { wrapper: wrap });
  const option = screen.getByRole('radio', { name: candidate.quote });
  expect(option).not.toBeChecked();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  fireEvent.click(option);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_file_date_apply }));
  expect(confirm).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'CS-1',
    valid_from_candidate_id: candidate.candidate_id,
    expected_versions: {},
  });
});
it('explicit batch confirmation sends only missing active rules and their version guards', () => {
  const value = proposal();
  value.items[0].applied_entity_id = 'R-1';
  value.items[0].applied_version = 4;
  value.items[1].after_data.exceptions = 'A specific exception';
  value.items.push(
    { ...item('rejected'), decision: 'rejected' },
    { ...item('processed'), processed_elsewhere: true },
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
  openOmissionLists();
  const exceptions = screen.getByRole('button', { name: terms.labels.r4a1_confirm_exceptions });
  expect(exceptions).toBeDisabled();
  for (const box of within(exceptions.closest('details')!).getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(exceptions);
  expect(confirm).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'CS-1',
    confirm_no_exceptions_item_ids: ['A'],
    expected_versions: { A: 4 },
  });
  const conditions = screen.getByRole('button', { name: terms.labels.r4a1_confirm_conditions });
  expect(conditions).toBeDisabled();
  for (const box of within(conditions.closest('details')!).getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(conditions);
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_unconditional_item_ids: ['A', 'B'],
    expected_versions: { A: 4 },
  });
});
it('lets each omission group independently select exact IDs and versions after reviewing its own quote', () => {
  const value = proposal();
  for (const [index, entry] of value.items.entries()) {
    entry.applied_version = index + 3;
    entry.evidence = [
      {
        material_id: 'M-1',
        anchor_id: 'anchor-' + entry.id,
        source_version: 'version-1',
        quote: 'Exact original sentence for ' + entry.id + '.',
      },
    ];
  }
  const confirm = vi.fn();
  const { container, rerender } = render(
    <ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />,
    { wrapper: wrap },
  );
  openOmissionLists();
  const exceptions = container.querySelector('[data-omission-group="exceptions"]') as HTMLElement;
  const conditions = container.querySelector('[data-omission-group="conditions"]') as HTMLElement;
  const row = exceptions.querySelector('[data-omission-item="A"]') as HTMLElement;
  const quote = within(row).getByText(value.items[0].evidence[0].quote);
  expect(exceptions.querySelector('summary')).toHaveTextContent(/请核对.*例外/);
  expect(conditions.querySelector('summary')).toHaveTextContent(/请核对.*条件/);
  expect(exceptions.querySelector('summary')).not.toHaveTextContent('原文未写');
  expect(quote).toBeVisible();
  expect(within(row).queryByText(value.items[1].evidence[0].quote)).not.toBeInTheDocument();
  expect(within(row).getByRole('link')).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=anchor-A&source_version=version-1',
  );
  for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).not.toBeChecked();
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'A' }));
  expect(within(conditions).getByRole('checkbox', { name: 'A' })).not.toBeChecked();
  fireEvent.click(within(exceptions).getByRole('button', { name: terms.labels.r4a1_confirm_exceptions }));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_no_exceptions_item_ids: ['A'],
    expected_versions: { A: 3 },
  });
  fireEvent.click(within(conditions).getByRole('checkbox', { name: 'B' }));
  fireEvent.click(within(conditions).getByRole('button', { name: terms.labels.r4a1_confirm_conditions }));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_unconditional_item_ids: ['B'],
    expected_versions: { B: 4 },
  });
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'A' }));
  const action = within(exceptions).getByRole('button', { name: terms.labels.r4a1_confirm_exceptions });
  expect(action).toBeDisabled();
  fireEvent.click(action);
  expect(confirm).toHaveBeenCalledTimes(2);
  rerender(<ChangesetOmissions proposal={value} disabled onConfirm={confirm} />);
  for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).toBeDisabled();
  const conditionAction = within(conditions).getByRole('button', {
    name: terms.labels.r4a1_confirm_conditions,
  });
  expect(conditionAction).toBeDisabled();
  fireEvent.click(conditionAction);
  expect(confirm).toHaveBeenCalledTimes(2);
});
it('never submits a deselected or newly ineligible row when the proposal refreshes', () => {
  const value = proposal();
  value.items[0].applied_version = 3;
  value.items[1].applied_version = 4;
  const confirm = vi.fn();
  const { container, rerender } = render(
    <ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />,
    { wrapper: wrap },
  );
  openOmissionLists();
  const exceptions = container.querySelector('[data-omission-group="exceptions"]') as HTMLElement;
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'A' }));
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'A' }));
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'B' }));
  value.items[1].after_data.bulk_confirmation_blocked = true;
  value.items.push({ ...item('C'), applied_version: 5 });
  rerender(<ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />);
  expect(within(exceptions).queryByRole('checkbox', { name: 'B' })).not.toBeInTheDocument();
  expect(within(exceptions).getByRole('checkbox', { name: 'A' })).not.toBeChecked();
  expect(within(exceptions).getByRole('checkbox', { name: 'C' })).not.toBeChecked();
  expect(within(exceptions).getByRole('button')).toBeDisabled();
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'C' }));
  fireEvent.click(within(exceptions).getByRole('button', { name: terms.labels.r4a1_confirm_exceptions }));
  expect(confirm).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'CS-1',
    confirm_no_exceptions_item_ids: ['C'],
    expected_versions: { C: 5 },
  });
});
it('requires a fresh selection when a checked row changes version or gains a different source quote', () => {
  const value = proposal();
  value.items[0].applied_version = 3;
  value.items[1].evidence = [
    { material_id: 'M', source_version: 'V', anchor_id: 'A', quote: 'First wording.' },
  ];
  const confirm = vi.fn();
  const { container, rerender } = render(
    <ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />,
    { wrapper: wrap },
  );
  openOmissionLists();
  const list = container.querySelector('[data-omission-group="exceptions"]') as HTMLElement;
  for (const box of within(list).getAllByRole('checkbox')) fireEvent.click(box);
  expect(within(list).getByRole('button')).toBeEnabled();
  const refreshed = structuredClone(value);
  refreshed.items[0].applied_version = 4;
  refreshed.items[1].evidence[0].quote = 'Amended wording.';
  rerender(<ChangesetOmissions proposal={refreshed} disabled={false} onConfirm={confirm} />);
  for (const box of within(list).getAllByRole('checkbox')) expect(box).not.toBeChecked();
  expect(within(list).getByRole('button')).toBeDisabled();
  expect(confirm).not.toHaveBeenCalled();
});
it('does not relabel a deleted source declaration as absent, independently for each field', () => {
  const value = proposal();
  value.items[0].after_data.source_declared_fields = ['exceptions'];
  value.items[1].after_data.source_declared_fields = ['conditions'];
  value.items.push({
    ...item('both'),
    after_data: { ...item('both').after_data, source_declared_fields: ['conditions', 'exceptions'] },
  });
  const confirm = vi.fn();
  const { container } = render(<ChangesetOmissions proposal={value} disabled={false} onConfirm={confirm} />, {
    wrapper: wrap,
  });
  openOmissionLists();
  const exceptions = container.querySelector('[data-omission-group="exceptions"]') as HTMLElement;
  const conditions = container.querySelector('[data-omission-group="conditions"]') as HTMLElement;
  expect(within(exceptions).getAllByRole('checkbox')).toHaveLength(1);
  expect(within(exceptions).getByRole('checkbox', { name: 'B' })).not.toBeChecked();
  fireEvent.click(within(exceptions).getByRole('checkbox', { name: 'B' }));
  expect(within(conditions).getAllByRole('checkbox')).toHaveLength(1);
  expect(within(conditions).getByRole('checkbox', { name: 'A' })).not.toBeChecked();
  fireEvent.click(within(conditions).getByRole('checkbox', { name: 'A' }));
  expect(screen.queryByRole('checkbox', { name: 'both' })).not.toBeInTheDocument();
  fireEvent.click(within(exceptions).getByRole('button'));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_no_exceptions_item_ids: ['B'],
    expected_versions: {},
  });
  fireEvent.click(within(conditions).getByRole('button'));
  expect(confirm).toHaveBeenLastCalledWith({
    changeset_id: 'CS-1',
    confirm_unconditional_item_ids: ['A'],
    expected_versions: {},
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
