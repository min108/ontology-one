import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { invokeCapability } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useSessionStore } from '../../state/session';
import { testSession } from '../build/testQuery';
import { RuleTests } from './RuleTests';
vi.mock('../../api/client', () => ({ invokeCapability: vi.fn() }));
it('runs stored test cases and renders real results before refreshing gates', async () => {
  const onComplete = vi.fn();
  vi.mocked(invokeCapability).mockResolvedValue({
    data: {
      passed: false,
      total: 2,
      passed_count: 1,
      results: [
        { name: '适用通过', expected: 'pass', actual: 'pass', passed: true, error: '' },
        { name: '应拒绝', expected: 'fail', actual: 'needs_review', passed: false, error: '需要补齐输入' },
      ],
      errors: [],
    },
  });
  const { container } = render(<RuleTests id="rule-1" disabled={false} onComplete={onComplete} />);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_run }));
  await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
  expect(invokeCapability).toHaveBeenCalledWith(
    'rule.test_cases',
    { entity_id: 'rule-1' },
    expect.anything(),
  );
  expect(container.querySelector('[data-rule-test-result]')).toHaveAttribute('data-passed', 'false');
  expect(container.querySelectorAll('[data-rule-test-case][data-passed="true"]')).toHaveLength(1);
  expect(screen.getByText('需要补齐输入')).toBeVisible();
  expect(screen.queryByText('needs_review')).not.toBeInTheDocument();
});

it('explains the readonly boundary before running a test capability', () => {
  testSession();
  const session = useSessionStore.getState().session!;
  useSessionStore.setState({ session: { ...session, workspace: { ...session.workspace, readonly: true } } });
  vi.mocked(invokeCapability).mockClear();
  render(<RuleTests id="rule-1" disabled={false} onComplete={vi.fn()} />);
  expect(screen.getByRole('button', { name: terms.labels.r4_rule_run })).toBeDisabled();
  expect(screen.getByText(terms.labels.r3_main_read_only)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4_rule_run }));
  expect(invokeCapability).not.toHaveBeenCalled();
  testSession();
});
