import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Home from './Home';
import { sentence } from '../build/model';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import type { Proposal } from '../../api/generated/types';
import { workspaceQueryKey } from '../../api/query';
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
function Location() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}
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
  expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '1');
  expect(container.querySelectorAll('[data-home-count]')).toHaveLength(0);
});

it.each([
  ['returned', 2, '/maintenance?tab=review&review_status=returned'],
  ['review', 3, '/maintenance?tab=review&review_status=review'],
] as const)('matches the count, main action and actual %s progress', async (status, step, href) => {
  useUiStore.setState({ viewMode: 'simple' });
  const session = useSessionStore.getState().session!;
  useSessionStore.setState({
    session: { ...session, permissions: { ...session.permissions, can_review: true } },
  });
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/overview')
      return { assets: 4, relations: 0, counts: {}, data_rows: 0, relation_statuses: {} };
    if (path === '/work/catalog') return { tasks: [] };
    if (path.includes('runs')) return { items: [] };
    return { total: path.includes(`status=${status}&`) ? 4 : 0 };
  });
  const { container } = render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByTestId('home-focus')).toHaveTextContent(
      sentence(terms.labels.r3_main_focus, { count: 4 }),
    ),
  );
  expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', String(step));
  const primary = screen.getByRole('button', { name: terms.labels.r31_home_process });
  expect(primary).toHaveClass('ant-btn-primary');
  expect(screen.getByRole('button', { name: terms.labels.home_choose_files })).not.toHaveClass(
    'ant-btn-primary',
  );
  expect(container.querySelectorAll('[data-home-count="4"]').length).toBeGreaterThan(0);
  expect(container.querySelector('[data-home-count="0"]')).toBeNull();
  const help = screen.getByRole('button', { name: terms.labels.help });
  expect(help.querySelector('svg')).not.toBeNull();
  fireEvent.click(primary);
  expect(screen.getByTestId('location')).toHaveTextContent(href);
});

it('does not invent completed mainline steps for a generic active task', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, init) =>
    path.includes('status=returned') ? Promise.resolve({ total: 0 }) : original(path, init),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByTestId('home-focus')).toHaveTextContent(
      sentence(terms.labels.r3_main_focus, { count: 1 }),
    ),
  );
  expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '0');
  const workflow = screen.getByRole('navigation', { name: terms.labels.r3_ui_next });
  expect(workflow.querySelector('[aria-current="step"]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r31_home_process }));
  expect(screen.getByTestId('location')).toHaveTextContent('/work?status=active');
});

function recipeProposal(): Proposal {
  return {
    id: 'CS-ready',
    title: 'Payment policy',
    status: 'open',
    summary: '',
    can_submit: false,
    source_text: '',
    source_material_id: 'material-1',
    items: [1, 2].map((id) => ({
      id: String(id),
      tier: 'check',
      decision: 'pending',
      one_liner: 'Check payment',
      rationale: '',
      content_origin: 'model',
      evidence: [],
      validation: { level: 'ok', issues: [] },
      after_data: { id: 'candidate-' + id, title: 'Payment', kind: 'rule', content: 'Check payment' },
    })),
  };
}
function recipeHome(value: Proposal, status = 'completed', taskCount = 0) {
  useUiStore.setState({ viewMode: 'simple' });
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/overview')
      return { assets: 0, relations: 0, counts: {}, data_rows: 0, relation_statuses: {} };
    if (path === '/work/catalog')
      return { tasks: Array.from({ length: taskCount }, () => ({ status: 'active' })) };
    if (path === '/harness/runs?limit=20')
      return {
        items: [
          {
            id: 'recipe-1',
            kind: 'recipe_run',
            status,
            title: 'Payment policy',
            href: status === 'completed' ? '/build/changesets/CS-ready' : '/build/intake?run=recipe-1',
          },
        ],
      };
    if (path === '/v2/changesets/CS-ready') return { data: value };
    return { items: [], total: 0 };
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Home />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
it.each([0, 5])(
  'returns to a verified ready proposal with its exact remaining count alongside %s other tasks',
  async (tasks) => {
    recipeHome(recipeProposal(), 'completed', tasks);
    await waitFor(() =>
      expect(screen.getByTestId('home-focus')).toHaveTextContent(
        sentence(terms.labels.r31_home_check_count, { count: 2 }),
      ),
    );
    expect(screen.queryByText(terms.labels.r3_main_start)).not.toBeInTheDocument();
    expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '2');
    expect(api).toHaveBeenCalledWith('/v2/changesets/CS-ready', expect.anything());
    expect(screen.getByRole('button', { name: terms.labels.home_choose_files })).not.toHaveClass(
      'ant-btn-primary',
    );
    fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_intake_view }));
    expect(screen.getByTestId('location')).toHaveTextContent('/build/changesets/CS-ready');
  },
);
it.each(['applied', 'submitted'])(
  'excludes a completed recipe whose proposal is already %s',
  async (status) => {
    const proposal = recipeProposal();
    proposal.status = status;
    proposal.items.forEach((item) => {
      item.decision = 'accepted';
      item.applied_entity_id = 'published-' + item.id;
    });
    recipeHome(proposal);
    expect(await screen.findByText(terms.labels.r3_main_start)).toBeVisible();
    expect(api).toHaveBeenCalledWith('/v2/changesets/CS-ready', expect.anything());
    expect(screen.queryByRole('button', { name: terms.labels.r3_main_intake_view })).not.toBeInTheDocument();
    expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '1');
  },
);
it('shows an active recipe at intake and opens its actual progress instead of a finished proposal', async () => {
  recipeHome(recipeProposal(), 'running');
  expect(await screen.findByText(terms.labels.r3_main_intake_running)).toBeVisible();
  expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '1');
  expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/v2/changesets/'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r31_home_progress }));
  expect(screen.getByTestId('location')).toHaveTextContent('/build/intake?run=recipe-1');
});
it.each(['open', 'applied'])(
  'keeps a fully handled %s proposal actionable until the server marks it submitted',
  async (status) => {
    const proposal = recipeProposal();
    proposal.status = status;
    proposal.can_submit = true;
    proposal.items.forEach((item, index) => {
      item.decision = index ? 'edited' : 'accepted';
      if (status === 'applied') item.applied_entity_id = 'draft-' + item.id;
    });
    recipeHome(proposal);
    await waitFor(() =>
      expect(screen.getByTestId('home-focus')).toHaveTextContent(
        sentence(terms.labels.r31_home_submit_count, { count: 2 }),
      ),
    );
    expect(screen.getByTestId('home-workflow')).toHaveAttribute('data-current-step', '2');
    fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_intake_view }));
    expect(screen.getByTestId('location')).toHaveTextContent('/build/changesets/CS-ready');
  },
);
it('does not count rejected or externally handled items as work still waiting for the user', async () => {
  const proposal = recipeProposal();
  proposal.items[0].decision = 'rejected';
  proposal.items[1].processed_elsewhere = true;
  recipeHome(proposal);
  expect(await screen.findByText(terms.labels.r3_main_start)).toBeVisible();
  expect(screen.queryByRole('button', { name: terms.labels.r3_main_intake_view })).not.toBeInTheDocument();
});
it('refreshes a cached empty Home before declaring the returned proposal ready', async () => {
  queryClient.setQueryData(workspaceQueryKey('a', useScopeStore.getState().scopeKey, 'home-runs', true), {
    items: [],
    proposals: [],
    partial: false,
  });
  recipeHome(recipeProposal());
  const home = screen.getByRole('region', { name: terms.labels.home_workbench });
  expect(home).toHaveAttribute('aria-busy', 'true');
  await waitFor(() => expect(home).toHaveAttribute('aria-busy', 'false'));
  expect(screen.getByTestId('home-focus')).toHaveTextContent(
    sentence(terms.labels.r31_home_check_count, { count: 2 }),
  );
});
