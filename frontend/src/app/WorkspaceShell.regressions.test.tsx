import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthorityDetail } from '@legacy/pages/Authority';
import { blankAuthoritySpec } from '@legacy/pages/authority-types';
import { useUiStore } from '../state/ui';
import { setWorkspaceTransition } from '../state/workspace-guard';
import { terms } from '../i18n/zh-CN';
import { WorkspaceShell } from './WorkspaceShell';

const { navigate, queries } = vi.hoisted(() => ({ navigate: vi.fn(), queries: new Map<string, unknown>() }));
vi.mock('react-router-dom', async (original) => ({
  ...(await original<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}));
vi.mock('@legacy/api', () => ({
  api: vi.fn(),
  statusLabel: (value: string) => value,
  useApi: (path: string) => ({ data: queries.get(path), reload: vi.fn() }),
}));
vi.mock('@legacy/components/AuthorityFlowEditor', () => ({ default: () => null }));
vi.mock('@legacy/components/LegacyHarnessConversation', () => ({
  default: () => null,
  useHarnessResultRestore: () => {},
}));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({
    session: { permissions: { can_edit: true }, workspace: { tenant_id: 'org', readonly: false } },
  }),
}));
vi.mock('../state/session', () => ({
  setWorkspaceConfirmation: vi.fn(),
  useSessionStore: () => ({
    session: { user: { id: 'one', role: 'modeler' }, workspace: { tenant_id: 'org' } },
    permission_keys: [],
    setNotice: vi.fn(),
  }),
}));
vi.mock('../features/home/HomeIntake', () => ({ HomeIntake: () => null }));
vi.mock('../features/home/RecentRecorder', () => ({ RecentRecorder: () => null }));
vi.mock('./GlobalSearch', () => ({ GlobalSearch: () => null }));
vi.mock('./WorkspaceBreadcrumbs', () => ({ WorkspaceBreadcrumbs: () => null }));
vi.mock('./WorkspaceConfirmation', () => ({ WorkspaceConfirmation: () => null }));
vi.mock('./useDownloadWorkspace', () => ({ useDownloadWorkspace: () => {} }));
vi.mock('./WorkspaceHeader', () => ({
  WorkspaceHeader: ({ onAssistant }: { onAssistant: () => void }) => (
    <button onClick={onAssistant}>Open assistant</button>
  ),
}));
vi.mock('./AppRoutes', () => ({
  completedAdministration: new Set(),
  AppRoutes: () => (
    <Routes>
      <Route path="/authority/:id" element={<AuthorityDetail />} />
      <Route path="*" element={<div>Other page</div>} />
    </Routes>
  ),
}));

beforeEach(() => {
  navigate.mockReset();
  setWorkspaceTransition('idle');
  useUiStore.setState({ collapsed: false });
  queries.clear();
  queries.set('/authority/assets/rule', {
    entity: {
      id: 'rule',
      title: 'Saved title',
      summary: '',
      status: 'draft',
      version: 1,
      domain: 'authority',
      source_kind: 'user',
      payload: { authority_spec: blankAuthoritySpec('workflow') },
    },
    checks: { errors: [], warnings: [] },
    sources: [],
  });
  queries.set('/authority/catalog', { organizations: [], people: [], items: [], stats: {} });
});
afterEach(() => {
  cleanup();
  document.querySelectorAll('[data-test-modal]').forEach((node) => node.remove());
  setWorkspaceTransition('idle');
  vi.restoreAllMocks();
});
function mount(path = '/authority/rule') {
  window.history.replaceState(null, '', path);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <WorkspaceShell />
    </MemoryRouter>,
  );
}

it.each([false, true])('R3.1 workspace navigation confirms once; accepted=%s', (accepted) => {
  mount();
  const field = screen.getByLabelText(terms.labels.home_authority_name);
  fireEvent.change(field, { target: { value: 'Unsaved authority title' } });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(accepted);
  fireEvent.click(screen.getByRole('link', { name: terms.labels.ontology }));
  expect(confirm).toHaveBeenCalledExactlyOnceWith(terms.labels.home_authority_discard);
  if (accepted) expect(navigate).toHaveBeenCalledExactlyOnceWith('/ontology');
  else {
    expect(navigate).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/authority/rule');
    expect(field).toHaveValue('Unsaved authority title');
  }
});

it('R3.1 inner dialog owns Escape and a later Escape closes the assistant', () => {
  mount('/ontology');
  fireEvent.click(screen.getByRole('button', { name: 'Open assistant' }));
  const panel = document.querySelector('[data-assistant-panel]');
  expect(panel).toBeVisible();
  const dialog = document.createElement('section');
  dialog.dataset.testModal = '';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  const field = document.createElement('input');
  dialog.append(field);
  document.body.append(dialog);
  dialog.addEventListener('keydown', () => dialog.remove());
  fireEvent.keyDown(field, { key: 'Escape' });
  expect(dialog).not.toBeInTheDocument();
  expect(panel).toBeVisible();
  // Antd may retain a closed modal in the DOM for its transition.
  dialog.style.display = 'none';
  document.body.append(dialog);
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(panel).not.toBeVisible();
});

it('R3.1 narrow split pages do not expose an ineffective expand-navigation action', () => {
  vi.spyOn(window, 'matchMedia').mockImplementation((media) => ({
    matches: media.startsWith('(max-width:'),
    media,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: () => true,
  }));
  mount('/build/changesets/one');
  expect(document.querySelector('[data-app-shell]')).toHaveAttribute('data-nav-collapsed', 'true');
  expect(screen.queryByRole('button', { name: terms.labels.r3_ui_nav_expand })).not.toBeInTheDocument();
});
