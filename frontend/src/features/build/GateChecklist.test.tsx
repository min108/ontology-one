import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { gates } from './gateFixtures';
import { terms } from '../../i18n/zh-CN';
import { GateChecklist, gateBlockers } from './GateChecklist';

it('opens real failed checks in simple view and keeps passed checks collapsed', () => {
  const { container, rerender } = render(<GateChecklist gates={gates(['QG3'])} />);
  expect(container.querySelector('details')).toHaveAttribute('open');
  expect(container.querySelector('[data-gate-code="QG3"]')).toHaveAttribute('data-gate-status', 'fail');
  expect(screen.getByText('需要补齐依据')).toBeVisible();
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
  fireEvent.change(screen.getByLabelText('出处可定位 · ' + terms.labels.r4_gate_waiver), {
    target: { value: '已核对纸质制度' },
  });
  expect(onWaiver).toHaveBeenCalledWith('QG1', '已核对纸质制度');
  expect(container.querySelector('[data-gate-code="QG4"] textarea')).toBeNull();
});
it('counterexample: an empty or whitespace waiver never removes a blocker', () => {
  expect(
    gateBlockers(gates(['QG1', 'QG3']), { QG1: '已核对纸质制度', QG3: '依照既定场景执行' }),
  ).toHaveLength(0);
  expect(gateBlockers(gates(['QG1', 'QG3']), { QG1: ' ', QG3: '' })).toHaveLength(2);
  expect(gateBlockers(gates(['QG4']), { QG1: '不能代替必备关系' })).toHaveLength(1);
});

it('keeps both waiver markers and the hard-constraint basis warning visible when checks are collapsed', () => {
  const result = gates();
  result.hard_constraint = true;
  result.checks = result.checks.map((check) =>
    ['QG1', 'QG3'].includes(check.code)
      ? { ...check, status: 'waived', waiver_reason: '接受该依据的具体理由' }
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
    <GateChecklist gates={result} waivers={{ QG1: '已核对访谈原始记录' }} onWaiver={() => undefined} />,
  );
  expect(screen.getByText(terms.labels.r4_waiver_basis_warning)).toBeVisible();
  rerender(<GateChecklist gates={result} waivers={{ QG1: ' ' }} onWaiver={() => undefined} />);
  expect(screen.queryByText(terms.labels.r4_waiver_basis_warning)).not.toBeInTheDocument();
});
