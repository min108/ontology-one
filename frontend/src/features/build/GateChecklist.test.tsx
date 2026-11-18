import fixture from '../../../test-fixtures/r4-semantic-content.json';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { gates } from './gateFixtures';
import { terms } from '../../i18n/zh-CN';
import { GateChecklist, gateBlockers } from './GateChecklist';

it('opens real failed checks in simple view and keeps passed checks collapsed', () => {
  const { container, rerender } = render(<GateChecklist gates={gates(['QG3'])} />);
  expect(container.querySelector('details')).toHaveAttribute('open');
  expect(container.querySelector('[data-gate-code="QG3"]')).toHaveAttribute('data-gate-status', 'fail');
  expect(screen.getByText(fixture.missingEvidence)).toBeVisible();
  expect(screen.queryByText(terms.labels.r3_main_check_unknown)).not.toBeInTheDocument();
  rerender(<GateChecklist gates={gates()} />);
  expect(container.querySelector('details')).not.toHaveAttribute('open');
});
it('offers waiver reasons only for the two permitted checks', () => {
  const onWaiver = vi.fn();
  const { container } = render(
    <GateChecklist gates={gates(['QG1', 'QG2', 'QG3', 'QG4', 'QG5'])} onWaiver={onWaiver} />,
  );
  expect(screen.getAllByRole('textbox')).toHaveLength(2);
  fireEvent.change(screen.getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: fixture.paperVerified },
  });
  expect(onWaiver).toHaveBeenCalledWith('QG1', fixture.paperVerified);
  expect(container.querySelector('[data-gate-code="QG4"] textarea')).toBeNull();
});
it('counterexample: an empty or whitespace waiver never removes a blocker', () => {
  expect(
    gateBlockers(gates(['QG1', 'QG3']), { QG1: fixture.paperVerified, QG3: fixture.scenarioAccepted }),
  ).toHaveLength(0);
  expect(gateBlockers(gates(['QG1', 'QG3']), { QG1: ' ', QG3: '' })).toHaveLength(2);
  expect(gateBlockers(gates(['QG4']), { QG1: fixture.invalidRelationWaiver })).toHaveLength(1);
});

it('keeps both waiver markers and the hard-constraint basis warning visible when checks are collapsed', () => {
  const result = gates();
  result.hard_constraint = true;
  result.checks = result.checks.map((check) =>
    ['QG1', 'QG3'].includes(check.code)
      ? { ...check, status: 'waived', waiver_reason: fixture.waiverReason }
      : check,
  );
  const { container, rerender } = render(<GateChecklist gates={result} />);
  expect(container.querySelector('details')).not.toHaveAttribute('open');
  expect(
    screen.getByText(terms.labels.r4_source_waived + ' · ' + terms.labels.r4_conditions_waived),
  ).toBeVisible();
  expect(screen.getByText(terms.labels.r4_waiver_basis_warning)).toBeVisible();
  expect(screen.queryByText(terms.labels.r4_gates_pass)).not.toBeInTheDocument();
  rerender(<GateChecklist gates={{ ...result, hard_constraint: false }} />);
  expect(screen.queryByText(terms.labels.r4_waiver_basis_warning)).not.toBeInTheDocument();
  expect(container.querySelector('[data-gate-waiver-marker]')).toBeVisible();
});
it('warns the reviewer while proposing an evidence waiver for a hard constraint', () => {
  const result = { ...gates(['QG1']), hard_constraint: true };
  const { rerender } = render(
    <GateChecklist gates={result} waivers={{ QG1: fixture.interviewVerified }} onWaiver={() => undefined} />,
  );
  expect(screen.getByText(terms.labels.r4_waiver_basis_warning)).toBeVisible();
  rerender(<GateChecklist gates={result} waivers={{ QG1: ' ' }} onWaiver={() => undefined} />);
  expect(screen.queryByText(terms.labels.r4_waiver_basis_warning)).not.toBeInTheDocument();
});

it('offers a repair action for each failed gate but never a waiver for definition', () => {
  const onRepair = vi.fn(),
    result = gates(['QG1', 'QG2']);
  result.checks[0].application_reason = '原件待归档，请独立核实';
  const { container } = render(<GateChecklist gates={result} onRepair={onRepair} />);
  const repairs = screen.getAllByRole('button', { name: terms.labels.r4a1_legacy_repair });
  expect(repairs).toHaveLength(2);
  fireEvent.click(repairs[1]);
  expect(onRepair).toHaveBeenCalledWith('QG2');
  expect(screen.getByText(/原件待归档，请独立核实/)).toBeVisible();
  expect(container.querySelector('[data-gate-code="QG1"]')).toHaveAttribute('data-gate-status', 'fail');
  expect(container.querySelector('[data-gate-code="QG2"] textarea')).toBeNull();
});
