import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ProposalItem } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { GateApplications } from './GateApplications';
import { gates } from './gateFixtures';

it('withdraws just the cleared application and retains the other explanation', () => {
  const item: ProposalItem = {
    id: 'candidate',
    tier: 'check',
    decision: 'edited',
    one_liner: '',
    rationale: '',
    content_origin: 'model',
    validation: { level: 'ok', issues: [] },
    evidence: [],
    after_data: {
      id: 'candidate',
      title: 'Policy',
      kind: 'rule',
      content: 'Rule',
      waiver_applications: { QG1: 'No electronic original', QG3: 'Pending exception' },
    },
    gates: gates(['QG1', 'QG3']),
  };
  const save = vi.fn();
  const { container } = render(<GateApplications item={item} disabled={false} onSave={save} />);
  fireEvent.click(container.querySelector('summary')!);
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByDisplayValue('Pending exception'), { target: { value: '  ' } });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4a1_gate_application_save }));
  expect(save).toHaveBeenCalledWith(item, { QG1: 'No electronic original' });
});
