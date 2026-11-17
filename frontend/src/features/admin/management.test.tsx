import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useUiStore } from '../../state/ui';
import { terms } from '../../i18n/zh-CN';
import { brand } from '../../i18n/brand';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import ModelPage from './model/ModelPage';
import BackupPage from './backup/BackupPage';
import SystemPage from './system/SystemPage';

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  invoke: vi.fn(),
  blob: vi.fn(),
  role: 'admin',
  tenant: 'dept-01',
  home: 'dept-01',
  superadmin: false,
}));
vi.mock('../../api/client', () => ({ api: mocks.api, invokeCapability: mocks.invoke, apiBlob: mocks.blob }));
vi.mock('../../state/session', () => ({
  useSessionStore: (select: (state: unknown) => unknown) =>
    select({
      session: {
        user: { id: 'u', role: mocks.role, home_tenant_id: mocks.home, is_superadmin: mocks.superadmin },
        workspace: { tenant_id: mocks.tenant, readonly: false },
        permissions: { manage_users: mocks.role === 'admin' },
      },
    }),
}));
const status = {
  available: true,
  configured: true,
  verified: false,
  model: 'test-model',
  base_url: 'https://model.test/anthropic',
};
const renderPage = (node: React.ReactNode) =>
  render(node, {
    wrapper: ({ children }) => (
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
      >
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  });
beforeEach(() => {
  useUiStore.setState({ viewMode: 'simple' });
  mocks.role = 'admin';
  mocks.tenant = 'dept-01';
  mocks.home = 'dept-01';
  mocks.superadmin = false;
  mocks.api.mockReset().mockImplementation((path: string) =>
    Promise.resolve(
      path.endsWith('/backups')
        ? { items: [] }
        : path.endsWith('/metadata')
          ? {
              product_name: brand.product_name,
              product_name_en: brand.product_name_en,
              version: '1.0.0',
              runtime_mode: 'server',
              control: { current: 1, pending: [] },
              workspace: { current: 1, pending: [] },
            }
          : status,
    ),
  );
  mocks.invoke
    .mockReset()
    .mockImplementation((id: string) =>
      Promise.resolve(
        id === 'agent.readiness' ? { checks: [], message: 'diagnostics', complete: false } : status,
      ),
    );
});
afterEach(cleanup);
it('saves the secret only in the submitted body and clears the field', async () => {
  renderPage(<ModelPage />);
  const input = await screen.findByLabelText(terms.labels.admin_key);
  fireEvent.change(input, { target: { value: 'temporary-secret' } });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.admin_save }));
  await waitFor(() =>
    expect(mocks.invoke).toHaveBeenCalledWith(
      'model.configure',
      expect.objectContaining({ api_key: 'temporary-secret' }),
      expect.anything(),
    ),
  );
  await waitFor(() => expect(input).toHaveValue(''));
  expect(document.body.textContent).not.toContain('temporary-secret');
  expect(localStorage.length).toBe(0);
});
it('clears the saved key explicitly without submitting a typed key', async () => {
  renderPage(<ModelPage />);
  fireEvent.click(await screen.findByLabelText(terms.labels.admin_clear_key));
  fireEvent.click(screen.getByRole('button', { name: terms.labels.admin_save }));
  await waitFor(() =>
    expect(mocks.invoke).toHaveBeenCalledWith(
      'model.configure',
      expect.objectContaining({ clear_key: true }),
      expect.anything(),
    ),
  );
});
it.each(['modeler', 'reviewer'])('retains readonly configuration for %s', async (role) => {
  mocks.role = role;
  renderPage(<ModelPage />);
  expect(await screen.findByLabelText(terms.labels.admin_key)).toBeDisabled();
  expect(screen.queryByRole('button', { name: terms.labels.admin_save })).not.toBeInTheDocument();
});
it('disables verification during cross-institution maintenance', async () => {
  mocks.home = 'hq';
  renderPage(<ModelPage />);
  expect(await screen.findByRole('button', { name: terms.labels.admin_verify })).toBeDisabled();
});
it('clears unsaved key on scope leave', async () => {
  renderPage(<ModelPage />);
  const input = await screen.findByLabelText(terms.labels.admin_key);
  fireEvent.change(input, { target: { value: 'discard-me' } });
  window.dispatchEvent(new Event('ontology:scope-cleared'));
  await waitFor(() => expect(input).toHaveValue(''));
});
it('readonly backup page retains instructions and user-initiated release link', async () => {
  mocks.role = 'reviewer';
  renderPage(<BackupPage />);
  expect(screen.getByRole('button', { name: terms.labels.admin_export })).toBeDisabled();
  expect(screen.getByRole('link', { name: terms.labels.admin_release_link })).toHaveAttribute(
    'rel',
    'noopener noreferrer',
  );
  expect(screen.getByText(terms.labels.admin_restore)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: terms.labels.admin_source_export })).not.toBeInTheDocument();
});
it('system status shows current version without static enabled claims', async () => {
  renderPage(<SystemPage />);
  expect(await screen.findByText(`${brand.product_name} 1.0.0`)).toBeInTheDocument();
  expect(screen.queryByText(String.fromCodePoint(0x5df2, 0x542f, 0x7528))).not.toBeInTheDocument();
});
it('shows unavailable runtime despite a previous successful connection', async () => {
  mocks.api.mockResolvedValue({
    ...status,
    available: false,
    verified: true,
    reason: 'runtime-unavailable',
    version: '0.2.1rc2',
  });
  renderPage(<ModelPage />);
  expect(await screen.findByText(terms.labels.admin_runtime_unavailable)).toBeInTheDocument();
  expect(screen.queryByText('runtime-unavailable')).not.toBeInTheDocument();
  useUiStore.setState({ viewMode: 'professional' });
  fireEvent.click(await screen.findByText(terms.labels.r3_admin_technical));
  expect(await screen.findByText('runtime-unavailable')).toBeInTheDocument();
  expect(screen.getByText(/0.2.1rc2/)).toBeInTheDocument();
});
it('verification success refreshes the global model status', async () => {
  const listener = vi.fn();
  window.addEventListener('ontology:harness-configured', listener);
  renderPage(<ModelPage />);
  await screen.findByRole('button', { name: terms.labels.admin_verify });
  mocks.api.mockResolvedValue({ status: { ...status, verified: true } });
  fireEvent.click(screen.getByRole('button', { name: terms.labels.admin_verify }));
  await waitFor(() => expect(listener).toHaveBeenCalledTimes(1));
  window.removeEventListener('ontology:harness-configured', listener);
});
it('diagnostic failure is explicit and retry actually repeats the request', async () => {
  mocks.invoke.mockRejectedValueOnce(new Error('diagnostic-failure'));
  renderPage(<ModelPage />);
  expect(await screen.findByText('diagnostic-failure')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.admin_retry }));
  await waitFor(() => expect(mocks.invoke).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('diagnostics')).toBeInTheDocument();
});
it('never renders previous institution data under a changed identity', async () => {
  const view = renderPage(<ModelPage />);
  expect(await screen.findByLabelText(terms.labels.admin_model_name)).toHaveValue('test-model');
  mocks.tenant = 'other';
  mocks.api.mockReturnValue(new Promise(() => undefined));
  view.rerender(<ModelPage />);
  expect(screen.queryByDisplayValue('test-model')).not.toBeInTheDocument();
});
