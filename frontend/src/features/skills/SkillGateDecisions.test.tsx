import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import fixture from '../../../test-fixtures/r4-semantic-content.json';
import schemaFixture from '../../../test-fixtures/r4a1-schema-facts.json';
import legacy from '../../../test-fixtures/r4a1-legacy-content.json';
import { terms } from '../../i18n/zh-CN';
import { gates } from '../build/gateFixtures';
import { SkillGateDecisions, SkillGateRecords, skillDecisionBlocked } from './SkillGateDecisions';
import { GovernanceFacts } from '../build/GovernanceFacts';

it('shows every exact skill fact and Chinese source inside the publication dialog, never relying on duplicate background content', () => {
  const result = gates();
  result.fields = ['definition', 'inputs', 'outputs', 'steps', 'usage_conditions'].map((key) => ({
    key,
    label: key,
    value: key === 'inputs' || key === 'outputs' ? JSON.stringify(schemaFixture[key]) : `Actual saved ${key}`,
    source: 'editor' as const,
    source_quote: '',
    confirmed_by: 'Reviewer',
    href: '',
  }));
  const props = {
    action: 'publish',
    waivers: {},
    denials: {},
    disabled: false,
    onWaiver: vi.fn(),
    onDenial: vi.fn(),
  };
  const surface = (dialogGates: typeof result) => (
    <MemoryRouter>
      <GovernanceFacts gates={result} />
      <section role="dialog">
        <SkillGateDecisions {...props} gates={dialogGates} />
      </section>
    </MemoryRouter>
  );
  const { rerender } = render(surface(result));
  const assertDialog = () => {
    const dialog = screen.getByRole('dialog');
    for (const field of result.fields!) {
      const rows = dialog.querySelectorAll(`[data-governance-field="${field.key}"]`);
      expect(rows).toHaveLength(1);
      if (field.key === 'inputs' || field.key === 'outputs') {
        expect(
          within(rows[0] as HTMLElement).getByText(
            field.key === 'inputs' ? schemaFixture.amountLabel : schemaFixture.resultLabel,
          ),
        ).toBeVisible();
        expect(rows[0]).not.toHaveTextContent(field.value);
      } else expect(within(rows[0] as HTMLElement).getByText(field.value)).toBeVisible();
      expect(rows[0].querySelector('[data-fact-origin]')).toHaveTextContent(
        terms.labels.r4a1_legacy_origin_editor,
      );
    }
  };
  assertDialog();
  rerender(surface({ ...result, fields: result.fields!.filter((field) => field.key !== 'inputs') }));
  expect(() => assertDialog()).toThrow();
  // The unchanged valid background card cannot make the missing dialog value pass.
  expect(screen.getByText(schemaFixture.amountLabel)).toBeVisible();
  rerender(
    surface({ ...result, fields: result.fields!.map((field) => ({ ...field, source: 'legacy' as const })) }),
  );
  expect(() => assertDialog()).toThrow();
});

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
