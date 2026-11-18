import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import fixture from '../../../test-fixtures/r4-semantic-content.json';
import legacy from '../../../test-fixtures/r4a1-legacy-content.json';
import { terms } from '../../i18n/zh-CN';
import { gates } from '../build/gateFixtures';
import { SkillGateDecisions, SkillGateRecords, skillDecisionBlocked } from './SkillGateDecisions';

it('requires independent permission, current package proof and every non-waivable check for publication', () => {
  const waiver = { QG1: fixture.paperVerified };
  expect(skillDecisionBlocked('publish', true, true, gates(['QG1']), waiver, {})).toBe(false);
  expect(skillDecisionBlocked('publish', false, true, gates(['QG1']), waiver, {})).toBe(true);
  expect(skillDecisionBlocked('publish', true, false, gates(['QG1']), waiver, {})).toBe(true);
  expect(skillDecisionBlocked('publish', true, true, undefined, waiver, {})).toBe(true);
  expect(skillDecisionBlocked('publish', true, true, gates(['QG1']), { QG1: ' ' }, {})).toBe(true);
  for (const code of ['QG2', 'QG4', 'QG5'] as const)
    expect(skillDecisionBlocked('publish', true, true, gates([code]), waiver, {})).toBe(true);
});

it('supports independent return and disjoint per-check decisions without turning a denial into publication', () => {
  expect(
    skillDecisionBlocked(
      'return',
      true,
      false,
      gates(['QG1', 'QG3']),
      { QG1: 'accepted' },
      { QG3: 'denied' },
    ),
  ).toBe(false);
  expect(
    skillDecisionBlocked('return', true, true, gates(['QG1']), { QG1: 'accepted' }, { QG1: 'denied' }),
  ).toBe(true);
  expect(skillDecisionBlocked('publish', true, true, gates(), {}, { QG1: 'denied' })).toBe(true);
});

it('shows the actual application, collects a per-check denial and disables unauthorized edits', () => {
  const result = gates(['QG1']);
  result.checks[0].application_reason = legacy.application;
  const onWaiver = vi.fn(),
    onDenial = vi.fn();
  const props = { action: 'return', gates: result, waivers: {}, denials: {}, onWaiver, onDenial };
  const { rerender } = render(<SkillGateDecisions {...props} disabled={false} />);
  expect(screen.getByText(`${terms.labels.r4a1_legacy_application}：${legacy.application}`)).toBeVisible();
  const label = result.checks[0].label + ' · ' + terms.labels.r4a1_legacy_denial;
  fireEvent.change(screen.getByLabelText(label), { target: { value: legacy.manualApplication } });
  expect(onDenial).toHaveBeenCalledWith('QG1', legacy.manualApplication);
  rerender(<SkillGateDecisions {...props} disabled />);
  expect(screen.getByLabelText(label)).toBeDisabled();
  expect(screen.getByLabelText(result.checks[0].label + ' · ' + terms.labels.r4_gate_waiver)).toBeDisabled();
});

it('retains the published waiver and the application and denial history as readable records', () => {
  const result = gates();
  result.checks[0].status = 'waived';
  result.checks[0].waiver_reason = legacy.reviewWaiver;
  const { rerender } = render(
    <SkillGateRecords
      gates={result}
      activities={[
        { action: terms.labels.r4a1_legacy_application, detail: legacy.application },
        { action: terms.labels.r4a1_legacy_denial, detail: legacy.manualApplication },
      ]}
    />,
  );
  expect(screen.getByText(`${terms.labels.r4_gate_waiver}：${legacy.reviewWaiver}`)).toBeVisible();
  expect(screen.getByText(legacy.application)).toBeVisible();
  expect(screen.getByText(legacy.manualApplication)).toBeVisible();
  rerender(<SkillGateRecords gates={gates()} />);
  expect(screen.queryByText(legacy.application)).not.toBeInTheDocument();
  expect(
    screen.queryByText(`${terms.labels.r4_gate_waiver}：${legacy.reviewWaiver}`),
  ).not.toBeInTheDocument();
});
