import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
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
