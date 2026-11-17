import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DesignProvider } from '../../design/DesignProvider';
import { useUiStore } from '../../state/ui';
import { useSessionStore } from '../../state/session';
import { readWorkspaceGuards } from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
import { LoginPage } from '../auth/LoginPage';
import { AuthBoundary } from '../auth/AuthBoundary';
import AccountSecurity from './AccountSecurity';
import { Timestamp } from '../identity/common';
import type { AuthSession } from '../../api/session.types';
const request = vi.hoisted(() => vi.fn());
vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: request,
}));
const login = vi.fn(),
  reload = vi.fn(),
  acceptSession = vi.fn();
const session: AuthSession = {
  authenticated: true,
  user: {
    id: 'self',
    username: 'admin',
    display_name: 'Admin',
    home_tenant_id: 'hq',
    role: 'admin',
    is_superadmin: true,
    must_change_password: false,
  },
  workspace: { tenant_id: 'hq', name: 'HQ', category: 'headquarters', readonly: false },
  workspaces: [],
  roles: [{ id: 'admin', name: 'Admin' }],
  permissions: { can_edit: true, can_review: true, manage_users: true, manage_grants: true },
  csrf_token: 'csrf',
  must_change_password: false,
  password_change_recommended: true,
};
function wrapper(children: React.ReactNode) {
  return render(
    <DesignProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    </DesignProvider>,
  );
}
beforeEach(() => {
  useUiStore.setState({ viewMode: 'simple' });
  request.mockReset();
  login.mockReset();
  reload.mockReset();
  acceptSession.mockReset();
  useSessionStore.setState({
    session,
    loading: false,
    switching: false,
    error: '',
    login,
    reload,
    acceptSession,
    permission_keys: ['admin.users', 'admin.org', 'audit.read'],
  });
  request.mockResolvedValue({ items: [] });
});
describe('new identity pages', () => {
  it('renders fixed-width Shanghai timestamps across single and double digit dates', () => {
    const { container } = render(
      <>
        <Timestamp value="2026-01-02T01:04:05Z" />
        <Timestamp value="2026-11-16T15:14:25Z" />
      </>,
    );
    const values = [...container.querySelectorAll('time')];
    expect(values.map((node) => node.textContent)).toEqual(['2026-01-02 09:04', '2026-11-16 23:14']);
    expect(values.every((node) => node.dataset.visualDynamic === 'timestamp')).toBe(true);
    expect(values[0]).toHaveAttribute('datetime', '2026-01-02T01:04:05Z');
  });
  it('uses the shared role label while preserving live identity facts', async () => {
    wrapper(<AccountSecurity />);
    await screen.findByText(terms.labels.identity_no_sessions);
    expect(screen.getByText(terms.roles.admin, { exact: true })).toBeInTheDocument();
    expect(screen.getByText('admin', { exact: true })).toBeInTheDocument();
    expect(
      screen.getByText(terms.labels.identity_superadmin).closest('.ant-tag')?.querySelector('svg'),
    ).not.toBeNull();
    expect(screen.getByText('HQ', { exact: true })).toBeInTheDocument();
  });
  it('labels the active workspace separately from the account home organization', async () => {
    useSessionStore.setState({
      session: {
        ...session,
        workspace: {
          tenant_id: 'dept-01',
          name: 'Office',
          category: 'headquarters_department',
          readonly: false,
        },
      },
    });
    wrapper(<AccountSecurity />);
    await screen.findByText(terms.labels.identity_no_sessions);
    expect(
      screen.getByText(terms.labels.identity_current_organization, { exact: true }).nextElementSibling,
    ).toHaveTextContent('Office');
    expect(screen.queryByText(terms.labels.identity_organization, { exact: true })).not.toBeInTheDocument();
  });
  it('provides the design theme before the authenticated shell exists', () => {
    useSessionStore.setState({ session: null });
    render(<AuthBoundary>{null}</AuthBoundary>);
    expect(
      screen.getByRole('heading', { name: terms.labels.identity_login }).closest('.zy-root'),
    ).not.toBeNull();
  });
  it('clears login passwords after failure and retains the account name', async () => {
    useSessionStore.setState({ session: null });
    login.mockRejectedValue({ status: 401 });
    wrapper(<LoginPage />);
    fireEvent.change(screen.getByLabelText(terms.labels.identity_login_account), {
      target: { value: ' admin ' },
    });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_password), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_login }));
    await screen.findByText(terms.labels.identity_login_failed);
    expect(login).toHaveBeenCalledWith('admin', 'secret');
    expect(screen.getByLabelText(terms.labels.identity_password)).toHaveValue('');
    expect(screen.getByLabelText(terms.labels.identity_login_account)).toHaveValue(' admin ');
  });
  it('offers reconnect and a distinct rate-limit message', async () => {
    useSessionStore.setState({ session: null, error: 'offline' });
    login.mockRejectedValue({ status: 429 });
    wrapper(<LoginPage />);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_reconnect }));
    expect(reload).toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_login_account), {
      target: { value: 'admin' },
    });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_password), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_login }));
    await screen.findByText(terms.labels.identity_login_limited);
  });
  it('does not gate business content even for a legacy forced-password payload', () => {
    useSessionStore.setState({
      session: {
        ...session,
        must_change_password: true,
        user: { ...session.user, must_change_password: true },
      },
    });
    wrapper(
      <AuthBoundary>
        <p>Business page</p>
      </AuthBoundary>,
    );
    expect(screen.getByText('Business page')).toBeInTheDocument();
  });
  it('guards password edits, validates confirmation and clears only on success', async () => {
    wrapper(<AccountSecurity />);
    expect(screen.getByText(terms.labels.identity_password_recommended)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_current_password), {
      target: { value: 'old-secret' },
    });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    fireEvent.change(screen.getByLabelText(terms.labels.identity_new_password), {
      target: { value: 'NewPassword123' },
    });
    fireEvent.change(screen.getByLabelText(terms.labels.identity_confirm_password), {
      target: { value: 'mismatch' },
    });
    expect(screen.getByRole('button', { name: terms.actions.identity_password_change })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(terms.labels.identity_confirm_password), {
      target: { value: 'NewPassword123' },
    });
    request.mockRejectedValueOnce(new Error('failed'));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_password_change }));
    await screen.findByText(terms.labels.identity_error_preserved);
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    request.mockResolvedValueOnce({ ...session, password_change_recommended: false });
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_password_change }));
    await waitFor(() => expect(acceptSession).toHaveBeenCalled());
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    expect(screen.getByLabelText(terms.labels.identity_new_password)).toHaveValue('');
  });
  it('summarizes devices in simple view and preserves raw details only for professional view', async () => {
    const agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0 Safari/537.36';
    request.mockResolvedValue({
      items: [
        {
          id: '0123456789abcdef0123456789abcdef',
          current: false,
          created_at: '2026-01-01T00:00:00Z',
          last_seen_at: '2026-01-02T00:00:00Z',
          expires_at: '2026-02-01T00:00:00Z',
          user_agent: agent,
          ip_hint: '127.*',
          tenant_id: 'hq',
        },
      ],
    });
    wrapper(<AccountSecurity />);
    expect(await screen.findByText(/Chrome · Windows/)).toHaveTextContent('2026-01-02 08:00');
    expect(screen.queryByText(agent, { exact: false })).not.toBeInTheDocument();
    useUiStore.setState({ viewMode: 'professional' });
    fireEvent.click(await screen.findByText(terms.labels.r3_admin_technical));
    expect(await screen.findByText(agent, { exact: false })).toBeInTheDocument();
    expect(screen.getByText('127.*')).toBeInTheDocument();
  });
  it('cancels session revocation without a request and reloads after current-session revocation', async () => {
    request.mockResolvedValue({
      items: [
        {
          id: 'current',
          current: true,
          created_at: '2026-01-01',
          last_seen_at: '2026-01-02',
          expires_at: '2026-02-01',
          user_agent: 'Device',
          ip_hint: '127.*',
          tenant_id: 'hq',
        },
      ],
    });
    wrapper(<AccountSecurity />);
    fireEvent.click(await screen.findByRole('button', { name: terms.actions.identity_revoke_session }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_cancel }));
    expect(request.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_revoke_session }));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_confirm_revoke }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });
});
