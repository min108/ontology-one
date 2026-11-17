import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Home from './Home';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
let queryClient: QueryClient;
import { api } from '../../api/client';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
vi.mock('../../api/client', () => ({ api: vi.fn(), invokeCapability: vi.fn() }));
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  useUiStore.setState({ viewMode: 'professional' });
  vi.mocked(api).mockReset();
  useScopeStore.getState().setScope('a');
  useSessionStore.setState({
    session: {
      authenticated: true,
      user: {
        id: 'u',
        username: 'one',
        display_name: 'One',
        home_tenant_id: 'a',
        role: 'modeler',
        is_superadmin: false,
        must_change_password: false,
      },
      workspace: { tenant_id: 'a', name: 'Tenant A', category: 'local', readonly: false },
      workspaces: [],
      roles: [],
      permissions: { manage_users: false, manage_grants: false, can_edit: true, can_review: false },
      csrf_token: 'c',
      must_change_password: false,
    },
  });
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/overview')
      return {
        assets: 12,
        relations: 7,
        counts: { organization: 4, data: 12 },
        data_rows: 99,
        relation_statuses: { confirmed: 7 },
      };
    if (path === '/work/catalog') return { tasks: [{ status: 'active' }] };
    if (path.includes('runs')) return { items: [] };
    return { items: [], total: 2 };
  });
});
afterEach(() => queryClient.clear());
it('renders actual institution counts and no more than four metric cards', async () => {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole('heading', { name: terms.labels.home_workbench })).toBeInTheDocument();
  expect(screen.queryByText('Tenant A')).not.toBeInTheDocument();
  expect(await screen.findAllByTestId('home-metric')).toHaveLength(4);
  expect(screen.getByText('99')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: new RegExp(terms.labels.home_pending) })).toBeInTheDocument();
});
it('does not turn a failed overview request into zero metrics', async () => {
  vi.mocked(api).mockRejectedValue(new Error('offline'));
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect((await screen.findAllByRole('alert')).length).toBeGreaterThan(0);
  expect(screen.queryAllByTestId('home-metric')).toHaveLength(0);
});

it('opens the exact overview measure in an addressable detail drawer', async () => {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const metrics = await screen.findAllByTestId('home-metric');
  fireEvent.click(metrics[2]);
  const detail = await screen.findByRole('dialog');
  expect(within(detail).getByText('4')).toBeInTheDocument();
  expect(within(detail).getByText(terms.labels.home_organizations_scope)).toBeInTheDocument();
  expect(within(detail).getByRole('link')).toHaveAttribute('href', '/graph?view=organization');
});
it('only requests review lists for reviewers and keeps task links institution scoped', async () => {
  const session = useSessionStore.getState().session!;
  useSessionStore.setState({
    session: { ...session, permissions: { ...session.permissions, can_edit: false, can_review: true } },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findAllByTestId('home-metric');
  await waitFor(() =>
    expect(screen.getByRole('link', { name: new RegExp(terms.labels.home_review) })).toHaveAttribute(
      'href',
      '/maintenance?tab=review&review_status=review',
    ),
  );
  expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('status=returned'))).toBe(false);
  expect(screen.getByRole('link', { name: new RegExp(terms.labels.home_tasks) })).toHaveAttribute(
    'href',
    '/work?status=active',
  );
});
it('foreign readonly institutions do not display unqueried running counts or allow file intake', async () => {
  const session = useSessionStore.getState().session!;
  useSessionStore.setState({
    session: {
      ...session,
      user: { ...session.user, home_tenant_id: 'other' },
      workspace: { ...session.workspace, readonly: true },
    },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findAllByTestId('home-metric');
  expect(screen.getByText(terms.labels.home_foreign_runs)).toBeInTheDocument();
  expect(screen.queryByText(terms.labels.home_none_running)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: terms.labels.home_choose_files })).toBeDisabled();
  expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('runs'))).toBe(false);
});

it('shows one starting action and hides every zero summary in an empty simple workbench', async () => {
  useUiStore.setState({ viewMode: 'simple' });
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/overview')
      return { assets: 0, relations: 0, counts: { organization: 1 }, data_rows: 0, relation_statuses: {} };
    if (path === '/work/catalog') return { tasks: [] };
    return { items: [], total: 0 };
  });
  const { container } = render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText(terms.labels.r3_main_start)).toBeVisible();
  await waitFor(() =>
    expect(
      screen.queryByRole('heading', { name: new RegExp(terms.labels.home_pending) }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.queryAllByTestId('home-metric')).toHaveLength(0);
  expect(screen.queryByText('0')).not.toBeInTheDocument();
  expect(container.querySelectorAll('.ant-btn-primary')).toHaveLength(1);
  expect(screen.getByRole('button', { name: terms.labels.home_choose_files })).toBeEnabled();
});
