import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, invokeCapability } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { sentence } from './model';
import { testQuery, testSession } from './testQuery';
import IntakePage from './IntakePage';
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
  invokeCapability: vi.fn(),
}));
const recipe = {
  id: 'run-1',
  title: 'Payment policy',
  status: 'completed',
  stage_message: 'Completed',
  changeset_id: 'cs-1',
  progress: { done: 4, total: 4 },
  used_model: 0,
};
beforeEach(() => {
  testSession();
  vi.clearAllMocks();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/materials/upload') return { id: 'material-1' } as never;
    if (path.startsWith('/v2/recipes/')) return { data: recipe } as never;
    return { data: { items: [{ tier: 'sure' }, { tier: 'check' }, { tier: 'doubt' }] } } as never;
  });
  vi.mocked(invokeCapability).mockResolvedValue({ data: recipe });
});
function Location() {
  const value = useLocation();
  return <output data-testid="location">{value.pathname + value.search}</output>;
}
it('uploads one chosen file, starts the recipe and concludes with counts and one review action', async () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/build/intake']}>
      <IntakePage />
      <Location />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  fireEvent.change(screen.getByLabelText(terms.labels.r2_choose_file), {
    target: { files: [new File(['Review before payment'], 'policy.txt', { type: 'text/plain' })] },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r2_start }));
  expect(
    await screen.findByText(sentence(terms.labels.r3_main_intake_done, { count: 3, check: 2 })),
  ).toBeVisible();
  expect(invokeCapability).toHaveBeenCalledWith(
    'recipe.start',
    { material_id: 'material-1' },
    expect.anything(),
  );
  const upload = vi.mocked(api).mock.calls.find(([path]) => path === '/materials/upload')?.[1]
    ?.body as FormData;
  expect((upload.get('file') as File).name).toBe('policy.txt');
  expect(container.querySelectorAll('.ant-btn-primary')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_intake_view }));
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/build/changesets/cs-1'));
});

it('offers an explicit fresh file path after a terminal run fails without a proposal', async () => {
  vi.mocked(api).mockResolvedValue({
    data: {
      ...recipe,
      status: 'failed',
      changeset_id: null,
      progress: null,
      stage_message: 'Interrupted; choose a file again.',
    },
  });
  render(
    <MemoryRouter initialEntries={['/build/intake?run=failed-run']}>
      <IntakePage />
      <Location />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  const restart = await screen.findByRole('button', { name: terms.labels.r3_main_intake_new });
  expect(screen.getByText('Interrupted; choose a file again.')).toBeVisible();
  expect(invokeCapability).not.toHaveBeenCalled();
  fireEvent.click(restart);
  expect(screen.getByTestId('location')).toHaveTextContent('/build/intake');
  const picker = screen.getByLabelText(terms.labels.r2_choose_file);
  expect(screen.getByRole('button', { name: terms.actions.r2_start })).toBeDisabled();
  fireEvent.change(picker, {
    target: { files: [new File(['Revised policy text'], 'revised-policy.txt', { type: 'text/plain' })] },
  });
  expect(screen.getByRole('button', { name: terms.actions.r2_start })).toBeEnabled();
  expect(invokeCapability).not.toHaveBeenCalled();
});

it.each([
  ['local', 'partial'],
  ['mock', 'completed'],
])('labels a finished %s-only result as not organized by AI', async (origin, status) => {
  vi.mocked(api).mockImplementation(
    async (path) =>
      ({
        data: path.startsWith('/v2/recipes/')
          ? { ...recipe, status, used_model: 0 }
          : {
              items: [{ tier: 'check', content_origin: origin }],
              extraction_summary: { fully_local: true, retried_batches: 1, discarded_candidates: 2 },
            },
      }) as never,
  );
  const { container } = render(
    <MemoryRouter initialEntries={['/build/intake?run=run-1']}>
      <IntakePage />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  expect(await screen.findByText(terms.labels.r4a1_extraction_local)).toBeVisible();
  await waitFor(() =>
    expect(container.querySelector('[data-extraction-summary]')).toHaveAttribute('data-retries', '1'),
  );
  expect(container.querySelector('[data-extraction-summary]')).toHaveAttribute('data-fully-local', 'true');
});
it('does not label online results or unfinished extraction as fully local', async () => {
  vi.mocked(api).mockImplementation(
    async (path) =>
      ({
        data: path.startsWith('/v2/recipes/')
          ? { ...recipe, used_model: 1 }
          : {
              items: [{ tier: 'sure' }],
              extraction_summary: { fully_local: false, retried_batches: 0, discarded_candidates: 0 },
            },
      }) as never,
  );
  const view = render(
    <MemoryRouter initialEntries={['/build/intake?run=online']}>
      <IntakePage />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  await screen.findByText(sentence(terms.labels.r3_main_intake_done, { count: 1, check: 0 }));
  expect(screen.queryByText(terms.labels.r4a1_extraction_local)).not.toBeInTheDocument();
  view.unmount();
  vi.mocked(api).mockResolvedValue({
    data: { ...recipe, status: 'running', changeset_id: null, used_model: 0 },
  } as never);
  const pending = render(
    <MemoryRouter initialEntries={['/build/intake?run=running']}>
      <IntakePage />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  await screen.findByText(terms.labels.r3_main_intake_running);
  expect(pending.container.querySelector('[data-extraction-summary]')).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r4a1_extraction_local)).not.toBeInTheDocument();
});
