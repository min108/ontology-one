import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DesignProvider } from '../../design/DesignProvider';
import { useUiStore } from '../../state/ui';
import { useSessionStore } from '../../state/session';
import { readWorkspaceGuards } from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
import UsersPage from './UsersPage';
import OrganizationsPage from './OrganizationsPage';
import AuditPage, { auditBoundary } from './AuditPage';
import type { AuthSession } from '../../api/session.types';
const { request, invoke } = vi.hoisted(() => ({ request: vi.fn(), invoke: vi.fn() }));
vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: request,
  invokeCapability: invoke,
}));
const reload = vi.fn();
const user = {
  id: 'self',
  username: 'admin',
  display_name: 'Admin',
  home_tenant_id: 'hq',
  role: 'admin',
  is_superadmin: true,
  is_active: true,
  must_change_password: false,
  tenant_name: 'HQ',
  auth_version: 1,
};
const other = {
  ...user,
  id: 'other',
  username: 'other',
  display_name: 'Other',
  role: 'modeler',
  is_superadmin: false,
};
const orgs = [
  { id: 'hq', org_id: 'O-HQ', name: 'HQ', category: 'headquarters', status: 'active', parent_id: null },
  {
    id: 'dept',
    org_id: 'O-D',
    name: 'Dept',
    category: 'headquarters_department',
    status: 'active',
    parent_id: 'hq',
  },
  { id: 'sub', org_id: 'O-S', name: 'Sub', category: 'subsidiary', status: 'active', parent_id: 'hq' },
];
const session: AuthSession = {
  authenticated: true,
  user,
  workspace: { tenant_id: 'hq', name: 'HQ', category: 'headquarters', readonly: false },
  workspaces: [],
  roles: [
    { id: 'admin', name: terms.roles.admin },
    { id: 'modeler', name: terms.roles.modeler },
    { id: 'reviewer', name: terms.roles.reviewer },
    { id: 'viewer', name: terms.roles.viewer },
  ],
  permissions: { can_edit: true, can_review: true, manage_users: true, manage_grants: true },
  csrf_token: 'csrf',
  must_change_password: false,
};
function wrapper(node: React.ReactNode, path = '/admin/users') {
  return render(
    <DesignProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
      </QueryClientProvider>
    </DesignProvider>,
  );
}
beforeEach(() => {
  useUiStore.setState({ viewMode: 'simple' });
  request.mockReset();
  invoke.mockReset();
  reload.mockReset();
  useSessionStore.setState({
    session,
    loading: false,
    switching: false,
    permission_keys: ['admin.users', 'admin.org', 'audit.read'],
    reload,
  });
  invoke.mockResolvedValue({ id: 'saved', ok: true });
  request.mockImplementation(async (path: string) =>
    path.includes('/orgs')
      ? { items: orgs, total: 3 }
      : path.includes('/users')
        ? { items: [user, other], total: 2 }
        : path.includes('/grants')
          ? { items: [], total: 0 }
          : {
              items: [
                {
                  id: 'audit1',
                  created_at: '2026-01-01T00:00:00Z',
                  actor_username: 'Admin',
                  action: 'login',
                  resource: 'user',
                  outcome: 'success',
                  request_id: 'REQ-1',
                  details: { sessions_revoked: true },
                },
              ],
              total: 31,
              page: 1,
              page_size: 30,
            },
  );
});
describe('identity administration pages', () => {
  it.each([UsersPage, OrganizationsPage, AuditPage])(
    'denies direct access before issuing privileged reads',
    (Page) => {
      useSessionStore.setState({
        session: { ...session, user: { ...user, role: 'modeler', is_superadmin: false } },
        permission_keys: [],
      });
      wrapper(<Page />);
      expect(screen.getByRole('status')).toHaveTextContent(terms.states.forbidden.title);
      expect(request).not.toHaveBeenCalled();
    },
  );
  it('keeps filters clean and validates a guarded account form before confirmation', async () => {
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    fireEvent.change(screen.getByLabelText(terms.labels.filter), { target: { value: 'other' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_create_user }));
    expect(screen.queryByRole('option', { name: terms.roles.viewer })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_username), { target: { value: 'ab' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    expect(screen.getByRole('button', { name: terms.actions.identity_review })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_username), {
      target: { value: 'new.user' },
    });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_name), { target: { value: 'New' } });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_role), { target: { value: 'modeler' } });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_initial_password), {
      target: { value: 'InitialPassword123' },
    });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    expect(invoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith(
        'user.manage',
        expect.objectContaining({
          action: 'create',
          create: expect.objectContaining({ username: 'new.user', role: 'modeler' }),
        }),
      ),
    );
    await waitFor(() => expect(readWorkspaceGuards().dirtyCount).toBe(0));
  });
  it('protects own role and reloads after editing the current account', async () => {
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    const row = screen.getByRole('row', { name: /Admin.*admin/ });
    expect(within(row).getAllByRole('button')).toHaveLength(2);
    fireEvent.click(within(row).getByRole('button', { name: terms.actions.r3_admin_more }));
    expect(await screen.findByRole('menuitem', { name: terms.actions.identity_disable })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(within(row).getByRole('button', { name: terms.actions.identity_edit_user }));
    expect(screen.getByLabelText(terms.labels.identity_role)).toBeDisabled();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_name), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    expect(screen.getByText(terms.labels.identity_self_update_impact)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(invoke.mock.calls[0][1].changes).not.toHaveProperty('role');
  });
  it('does not invoke management writes when a reviewed form is cancelled', async () => {
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    fireEvent.click(
      within(screen.getByRole('row', { name: /Other/ })).getByRole('button', {
        name: terms.actions.r3_admin_more,
      }),
    );
    fireEvent.click(await screen.findByRole('menuitem', { name: terms.actions.identity_disable }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_cancel }));
    expect(invoke).not.toHaveBeenCalled();
  });
  it('resets a password through More and sends only the intended account change', async () => {
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    const row = screen.getByRole('row', { name: /Other/ });
    expect(within(row).getAllByRole('button')).toHaveLength(2);
    fireEvent.click(within(row).getByRole('button', { name: terms.actions.r3_admin_more }));
    fireEvent.click(await screen.findByRole('menuitem', { name: terms.actions.identity_reset_password }));
    fireEvent.change(screen.getByLabelText(terms.labels.identity_temporary_password), {
      target: { value: 'TemporaryPassword123' },
    });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('user.manage', {
        action: 'update',
        user_id: 'other',
        changes: { password: 'TemporaryPassword123' },
      }),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText(terms.labels.identity_temporary_password)).not.toBeInTheDocument(),
    );
  });
  it('disables an account through More after confirming the impact', async () => {
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    fireEvent.click(
      within(screen.getByRole('row', { name: /Other/ })).getByRole('button', {
        name: terms.actions.r3_admin_more,
      }),
    );
    fireEvent.click(await screen.findByRole('menuitem', { name: terms.actions.identity_disable }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    expect(invoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('user.manage', {
        action: 'update',
        user_id: 'other',
        changes: { is_active: false },
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it('preserves edited values and dirty state after a rejected management write', async () => {
    invoke.mockRejectedValueOnce(new Error('offline'));
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    fireEvent.click(
      within(screen.getByRole('row', { name: /Other/ })).getByRole('button', {
        name: terms.actions.identity_edit_user,
      }),
    );
    fireEvent.change(screen.getByLabelText(terms.labels.identity_name), {
      target: { value: 'Keep this change' },
    });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await screen.findByText(terms.labels.identity_error_preserved);
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_back_edit }));
    expect(screen.getByLabelText(terms.labels.identity_name)).toHaveValue('Keep this change');
    fireEvent.change(screen.getByLabelText(terms.labels.identity_name), { target: { value: 'Other' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it('requires an explanation when editing another headquarters administrator', async () => {
    request.mockImplementation(async (path: string) =>
      path.includes('/orgs')
        ? { items: orgs }
        : { items: [user, { ...other, role: 'admin', is_superadmin: true }] },
    );
    wrapper(<UsersPage />);
    await screen.findByText('Other');
    fireEvent.click(
      within(screen.getByRole('row', { name: /Other/ })).getByRole('button', {
        name: terms.actions.identity_edit_user,
      }),
    );
    const reason = screen.getByLabelText(terms.labels.identity_reason_required);
    expect(screen.getByRole('button', { name: terms.actions.identity_review })).toBeDisabled();
    fireEvent.change(reason, { target: { value: 'x' } });
    expect(screen.getByRole('button', { name: terms.actions.identity_review })).toBeDisabled();
    fireEvent.change(reason, { target: { value: 'Role adjustment' } });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith(
        'user.manage',
        expect.objectContaining({ changes: expect.objectContaining({ reason: 'Role adjustment' }) }),
      ),
    );
  });
  it('keeps grant filters clean and confirms a constrained grant with its reason', async () => {
    wrapper(<UsersPage />, '/admin/users?tab=grants');
    expect(screen.getByRole('radio', { name: terms.labels.identity_grants_tab })).toBeChecked();
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: terms.actions.identity_create_grant })[0]).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText(terms.labels.filter), { target: { value: 'find' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    fireEvent.click(screen.getAllByRole('button', { name: terms.actions.identity_create_grant })[0]);
    fireEvent.change(screen.getByLabelText(terms.labels.identity_grant_user), { target: { value: 'other' } });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_grant_org), { target: { value: 'dept' } });
    expect(screen.getByRole('option', { name: terms.labels.identity_grant_write })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_grant_reason), {
      target: { value: 'Work requirement' },
    });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_review }));
    expect(invoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('grant.manage', {
        action: 'set',
        grant: { user_id: 'other', tenant_id: 'dept', access: 'read_published', reason: 'Work requirement' },
      }),
    );
  });
  it('shows the grant category only to headquarters administrators', async () => {
    useSessionStore.setState({
      session: {
        ...session,
        user: { ...user, is_superadmin: false, home_tenant_id: 'dept' },
        permissions: { ...session.permissions, manage_grants: false },
      },
      permission_keys: ['admin.users'],
    });
    wrapper(<UsersPage />);
    expect(screen.queryByRole('radio', { name: terms.labels.identity_grants_tab })).not.toBeInTheDocument();
  });
  it('renders a governed organization tree with no delete action and protects headquarters', async () => {
    wrapper(<OrganizationsPage />, '/admin/orgs');
    await screen.findByText('Dept');
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('tree')).getByText('HQ'));
    expect(screen.getByRole('button', { name: terms.actions.identity_disable })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_create_org }));
    fireEvent.change(screen.getByLabelText(terms.labels.identity_org_name), { target: { value: 'New org' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
  });
  it('gives organization selectors and ordering controls exact business labels', async () => {
    wrapper(<OrganizationsPage />, '/admin/orgs');
    await screen.findByText('Dept');
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_create_org }));
    expect(screen.getByLabelText(terms.labels.identity_org_category, { exact: true })).toHaveValue(
      'subsidiary',
    );
    expect(screen.getByLabelText(terms.labels.identity_org_parent, { exact: true })).toHaveValue('hq');
    expect(screen.getByRole('spinbutton', { name: terms.labels.identity_org_sort })).toHaveValue('0');
  });
  it('requires enabling a disabled organization before editing it', async () => {
    request.mockResolvedValue({ items: [{ ...orgs[1], status: 'disabled' }], total: 1 });
    wrapper(<OrganizationsPage />, '/admin/orgs');
    await screen.findByRole('button', { name: terms.actions.identity_enable });
    expect(screen.getByRole('button', { name: terms.actions.identity_edit_org })).toBeDisabled();
    expect(screen.getByRole('button', { name: terms.actions.identity_enable })).toBeEnabled();
  });
  it('does not create institutions before the directory has loaded successfully', async () => {
    request.mockRejectedValue(new Error('offline'));
    wrapper(<OrganizationsPage />, '/admin/orgs');
    await screen.findByText(terms.labels.identity_error_preserved);
    expect(screen.getByRole('button', { name: terms.actions.identity_create_org })).toBeDisabled();
  });
  it('gives an offline next step for a failed institution without offering online activation', async () => {
    request.mockResolvedValue({ items: [{ ...orgs[1], status: 'provision_failed' }], total: 1 });
    wrapper(<OrganizationsPage />, '/admin/orgs');
    await screen.findByText(terms.labels.identity_org_offline_check);
    expect(screen.getByRole('button', { name: terms.actions.identity_enable })).toBeDisabled();
    expect(screen.getByRole('button', { name: terms.actions.identity_edit_org })).toBeDisabled();
  });
  it('filters audit requests and retains details and the thirty-record page size', async () => {
    wrapper(<AuditPage />, '/admin/audit');
    await screen.findByRole('button', { name: terms.identity_audit_actions.login });
    expect(screen.getAllByRole('columnheader')).toHaveLength(4);
    expect(screen.queryByText('REQ-1')).not.toBeInTheDocument();
    useUiStore.setState({ viewMode: 'professional' });
    fireEvent.click(screen.getByRole('button', { name: terms.identity_audit_actions.login }));
    fireEvent.click(await screen.findByText(terms.labels.r3_admin_technical));
    expect(await screen.findByText('REQ-1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /关闭|Close/ }));
    fireEvent.change(screen.getByLabelText(terms.labels.r3_admin_actor), {
      target: { value: 'other' },
    });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_filter }));
    await waitFor(() =>
      expect(
        request.mock.calls.some(
          ([path]) => String(path).includes('actor_id=other') && String(path).includes('page_size=30'),
        ),
      ).toBe(true),
    );
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it.each(['demo_loaded', 'source_mapping_create'] as const)(
    'keeps the historical %s action selectable by its business name',
    async (action) => {
      wrapper(<AuditPage />, '/admin/audit');
      const select = await screen.findByRole('combobox', { name: terms.labels.r3_admin_action });
      expect(within(select).getByRole('option', { name: terms.identity_audit_actions[action] })).toHaveValue(
        action,
      );
      fireEvent.change(select, { target: { value: action } });
      fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_filter }));
      await waitFor(() =>
        expect(
          request.mock.calls.some(
            ([path]) => String(path).includes('/v2/audit?') && String(path).includes('action=' + action),
          ),
        ).toBe(true),
      );
    },
  );
});

describe('audit Beijing date boundaries', () => {
  it('includes both complete selected calendar days', () => {
    expect(auditBoundary('2026-01-02')).toBe('2026-01-01T16:00:00.000Z');
    expect(auditBoundary('2026-01-02', true)).toBe('2026-01-02T15:59:59.999Z');
    expect(auditBoundary('2026-01-02T10:30')).toBe('2026-01-02T02:30:00.000Z');
  });
  it.each(['invalid', '2026-02-31', '2026-13-01', '2026-01-02T29:00'])('rejects invalid date %s', (value) => {
    expect(auditBoundary(value)).toBeNull();
  });
});
