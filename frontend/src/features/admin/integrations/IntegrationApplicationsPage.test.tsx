import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { DesignProvider } from '../../../design/DesignProvider';
import { terms } from '../../../i18n/zh-CN';
import { useUiStore } from '../../../state/ui';
import { readWorkspaceGuards, setWorkspaceTransition } from '../../../state/workspace-guard';
import IntegrationApplicationsPage from './IntegrationApplicationsPage';

const state = vi.hoisted(() => ({
  api: vi.fn(),
  reload: vi.fn(),
  readonly: false,
  manage: true,
  items: [] as unknown[],
}));
const appId = 'a'.repeat(32),
  mappingId = 'b'.repeat(32),
  token = 'synthetic-one-time-credential';
const application = {
  id: appId,
  tenant_id: 'hq',
  name: 'Procurement reader',
  description: 'Read approved policies',
  delegate_user_id: 'delegate',
  delegate_username: 'delegate.reader',
  enabled: true,
  version: 2,
  tool_scopes: ['ontology_discover'],
  mapping_ids: [mappingId],
  created_at: 1735689600,
  updated_at: 1735689600,
  credentials: [
    {
      id: 'c'.repeat(32),
      prefix: 'synthetic-prefix',
      expires_at: 4102444800,
      created_at: 1735689600,
      last_used_at: null,
      revoked_at: null,
      usable: true,
    },
  ],
};
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({
    session: {
      user: { id: 'admin' },
      workspace: { tenant_id: 'hq', name: 'Headquarters', readonly: state.readonly },
      permissions: { manage_users: state.manage, can_edit: true },
    },
  }),
}));
vi.mock('@legacy/api', () => ({
  api: state.api,
  useApi: (path: string) => ({
    reload: state.reload,
    data: path.endsWith('/options')
      ? {
          users: [{ id: 'delegate', username: 'delegate.reader', display_name: 'Reader', role: 'modeler' }],
          mappings: [],
          tools: [
            {
              name: 'ontology_discover',
              title: 'Discover business assets',
              description: 'Read allowed business assets',
            },
          ],
        }
      : {
          items: state.items,
          tools: [{ name: 'ontology_discover', title: 'Discover business assets' }],
          endpoint: '/mcp',
          contract_version: '1',
          limits: { timeout_ms: 30000, max_rows: 200, max_join_keys: 100, max_result_bytes: 1024 },
        },
  }),
}));
const copy = terms.labels;
function show() {
  return render(
    <DesignProvider>
      <MemoryRouter>
        <IntegrationApplicationsPage />
      </MemoryRouter>
    </DesignProvider>,
  );
}
function completeForm() {
  fireEvent.change(screen.getByLabelText(copy.r3_integration_name), {
    target: { value: 'Procurement reader' },
  });
  fireEvent.change(screen.getByLabelText(copy.r3_integration_delegate), { target: { value: 'delegate' } });
  fireEvent.click(screen.getByLabelText(copy.r3_integration_confirm));
}
beforeEach(() => {
  state.api.mockReset();
  state.reload.mockReset();
  state.readonly = false;
  state.manage = true;
  state.items = [application];
  useUiStore.setState({ viewMode: 'simple' });
  setWorkspaceTransition('idle');
});

it('R3 integration applications uses one page header and primary action without identifier leakage', () => {
  const { container } = show();
  expect(container.querySelectorAll('[data-page-header]')).toHaveLength(1);
  expect(container.querySelector('[data-page-template]')).toHaveAttribute('data-page-template', 'list');
  expect(screen.getByRole('heading', { level: 1, name: copy.r3_integration_title })).toBeVisible();
  expect(screen.getAllByRole('button', { name: terms.actions.r3_integration_register })).toHaveLength(1);
  expect(document.body.textContent).not.toContain(appId);
  expect(document.body.textContent).not.toContain(mappingId);
  expect(document.body.textContent).not.toContain('ontology_discover');
  act(() => useUiStore.setState({ viewMode: 'professional' }));
  expect(screen.getByText(appId)).toBeInTheDocument();
  expect(screen.getByText(mappingId)).toBeInTheDocument();
});

it('R3 integration applications retains one-time issuance, clear-on-close, and draft guards', async () => {
  state.items = [];
  state.api.mockResolvedValue({
    application,
    credential: { id: 'credential', token, prefix: 'prefix', expires_at: 4102444800 },
  });
  show();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_integration_register }));
  completeForm();
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
  fireEvent.click(screen.getByRole('button', { name: copy.r3_integration_create }));
  await waitFor(() => expect(screen.getByLabelText(copy.r3_integration_token)).toHaveValue(token));
  expect(state.api).toHaveBeenCalledWith(
    '/integrations/applications',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        name: 'Procurement reader',
        description: '',
        tool_scopes: ['ontology_discover'],
        mapping_ids: [],
        delegate_user_id: 'delegate',
        expires_days: 30,
      }),
    }),
  );
  expect(readWorkspaceGuards().dirtyCount).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: copy.r3_integration_token_close }));
  expect(screen.queryByLabelText(copy.r3_integration_token)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_integration_register }));
  expect(screen.queryByLabelText(copy.r3_integration_token)).not.toBeInTheDocument();
});

it('R3 integration applications clears pending issuance and edited drafts when scope changes', async () => {
  let finish!: (value: unknown) => void;
  state.api.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  show();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_integration_register }));
  completeForm();
  fireEvent.click(screen.getByRole('button', { name: copy.r3_integration_create }));
  act(() => window.dispatchEvent(new Event('ontology:scope-cleared')));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(readWorkspaceGuards().dirtyCount).toBe(0);
  await act(async () => finish({ application, credential: { token } }));
  expect(screen.queryByLabelText(copy.r3_integration_token)).not.toBeInTheDocument();
});

it('R3 integration applications keeps original optimistic version and scope fields in edits', async () => {
  state.items = [{ ...application, mapping_ids: [] }];
  state.api.mockResolvedValue(application);
  show();
  fireEvent.click(screen.getByRole('button', { name: copy.r3_integration_edit }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText(copy.r3_integration_description), {
    target: { value: 'Updated business description' },
  });
  fireEvent.click(within(dialog).getByLabelText(copy.r3_integration_confirm));
  fireEvent.click(within(dialog).getByRole('button', { name: copy.r3_integration_save }));
  await waitFor(() =>
    expect(state.api).toHaveBeenCalledWith(
      '/integrations/applications/' + appId,
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          name: application.name,
          description: 'Updated business description',
          tool_scopes: ['ontology_discover'],
          mapping_ids: [],
          expected_version: 2,
        }),
      }),
    ),
  );
});

it.each([
  ['readonly', true, true],
  ['unauthorized', false, false],
])('R3 integration applications keeps the %s boundary', (_, readonly, manage) => {
  state.readonly = Boolean(readonly);
  state.manage = Boolean(manage);
  show();
  expect(screen.getByRole('heading', { level: 1, name: copy.r3_integration_title })).toBeVisible();
  expect(screen.getByText(copy.r3_integration_forbidden)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: terms.actions.r3_integration_register }),
  ).not.toBeInTheDocument();
  expect(state.api).not.toHaveBeenCalled();
});
