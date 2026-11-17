import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { ReportButton } from './ReportButton';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}
beforeEach(() => vi.resetAllMocks());

it('shows numeric correction details as text, stays in the answer and only retries explicitly', async () => {
  const injected = '<img src=x onerror=alert(1)>';
  vi.mocked(api)
    .mockRejectedValueOnce(
      new ApiError('Verify quantities', 422, {
        detail: { code: 'NUMERIC_UNVERIFIED', issues: ['Paragraph 1: 987', injected, null] },
      }),
    )
    .mockResolvedValueOnce({ data: { id: 'report-1' } });
  const { container } = render(
    <MemoryRouter initialEntries={['/applications']}>
      <ReportButton sessionId="session-1" runId="run-1" />
      <Location />
    </MemoryRouter>,
  );
  const create = screen.getByRole('button', { name: terms.actions.r2_report_create });
  fireEvent.click(create);
  expect(await screen.findByText('Paragraph 1: 987')).toBeInTheDocument();
  expect(screen.getByText(injected)).toBeInTheDocument();
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByTestId('location')).toHaveTextContent('/applications');
  expect(api).toHaveBeenCalledTimes(1);
  fireEvent.click(create);
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/apply/artifacts/report-1'));
  expect(screen.queryByText('Paragraph 1: 987')).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(2);
});

it('sends correction details in one follow-up and generates once from the completed new run', async () => {
  const revise = vi.fn().mockResolvedValue('run-2');
  vi.mocked(api)
    .mockRejectedValueOnce(
      new ApiError('Check the number', 422, {
        error: { details: { code: 'NUMERIC_UNVERIFIED', issues: ['2 items'] } },
      }),
    )
    .mockResolvedValueOnce({ status: 'completed' })
    .mockResolvedValueOnce({ data: { id: 'repaired' } });
  render(
    <MemoryRouter>
      <ReportButton sessionId="session-1" runId="run-1" onRevise={revise} />
      <Location />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r2_report_create }));
  const repair = await screen.findByRole('button', { name: terms.actions.r21_report_revise });
  fireEvent.click(repair);
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/apply/artifacts/repaired'));
  expect(revise).toHaveBeenCalledTimes(1);
  expect(revise.mock.calls[0][1]).toBe('run-1');
  expect(revise.mock.calls[0][0]).toContain('2 items');
  expect(api).toHaveBeenLastCalledWith(
    '/agent/sessions/session-1/runs/run-2/report',
    expect.objectContaining({ method: 'POST' }),
  );
  expect(api).toHaveBeenCalledTimes(3);
});

it('keeps correction available if sending fails and retries report generation using the revised run', async () => {
  const revise = vi.fn().mockRejectedValueOnce(new Error('Send failed')).mockResolvedValueOnce('run-2');
  vi.mocked(api)
    .mockRejectedValueOnce(
      new ApiError('Check', 422, { error: { details: { code: 'NUMERIC_UNVERIFIED', issues: ['2 items'] } } }),
    )
    .mockResolvedValueOnce({ status: 'completed' })
    .mockRejectedValueOnce(new Error('Temporary report failure'))
    .mockResolvedValueOnce({ data: { id: 'retried' } });
  render(
    <MemoryRouter>
      <ReportButton sessionId="session-1" runId="run-1" onRevise={revise} />
      <Location />
    </MemoryRouter>,
  );
  const create = screen.getByRole('button', { name: terms.actions.r2_report_create });
  fireEvent.click(create);
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.r21_report_revise }));
  expect(await screen.findByText('Send failed')).toBeInTheDocument();
  expect(screen.getByText('2 items')).toBeInTheDocument();
  const repair = screen.getByRole('button', { name: terms.actions.r21_report_revise });
  expect(repair).toBeEnabled();
  fireEvent.click(repair);
  expect(await screen.findByText('Temporary report failure')).toBeInTheDocument();
  fireEvent.click(create);
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/apply/artifacts/retried'));
  expect(api).toHaveBeenLastCalledWith(
    '/agent/sessions/session-1/runs/run-2/report',
    expect.objectContaining({ method: 'POST' }),
  );
  expect(revise).toHaveBeenCalledTimes(2);
});

it('does not generate or navigate when the user leaves while the correction is being submitted', async () => {
  let complete!: (value: string) => void;
  const revise = vi.fn(
    () =>
      new Promise<string>((resolve) => {
        complete = resolve;
      }),
  );
  vi.mocked(api).mockRejectedValueOnce(
    new ApiError('Check', 422, { detail: { code: 'NUMERIC_UNVERIFIED', issues: ['2 items'] } }),
  );
  const original = window.location.href;
  try {
    render(
      <MemoryRouter>
        <ReportButton sessionId="session-1" runId="run-1" onRevise={revise} />
        <Location />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: terms.actions.r2_report_create }));
    fireEvent.click(await screen.findByRole('button', { name: terms.actions.r21_report_revise }));
    expect(revise).toHaveBeenCalledOnce();
    window.history.replaceState({}, '', '/another-workspace');
    complete('run-2');
    await waitFor(() =>
      expect(screen.getByText(terms.actions.r2_report_create).closest('button')).not.toHaveClass(
        'ant-btn-loading',
      ),
    );
    expect(api).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('location')).not.toHaveTextContent('/apply/artifacts/');
    expect(window.location.pathname).toBe('/another-workspace');
  } finally {
    window.history.replaceState({}, '', original);
  }
});
