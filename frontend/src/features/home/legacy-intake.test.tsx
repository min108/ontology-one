import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Materials } from '@legacy/pages/Materials';
import { UploadDatasetModal } from '@legacy/pages/Datasets';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { setWorkspaceTransition } from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
import { clearTransfers, readTransfer, stageFiles, type IntakeTarget } from './intake';
const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@legacy/api', () => ({
  api: request,
  useApi: (path: string) => ({
    data: {
      items: path.startsWith('/entities?kind=organization') ? [{ id: 'org', title: 'Org' }] : [],
      total: 0,
      facets: {},
    },
    reload: vi.fn(),
  }),
  statusLabel: (value: string) => value,
  formatDate: (value: string) => value,
  csrfHeaders: () => ({}),
  workspaceHeaders: () => ({}),
  registerPendingUpload: () => () => {},
  assertBusinessReady: () => {},
  notifyUnauthorized: () => {},
}));
vi.mock('@legacy/auth', () => ({ useAuth: () => ({ session: useSessionStore.getState().session }) }));
const scope = { userId: 'u', tenantId: 'a', scopeKey: 'local' };
let posted: FormData[];
const cleanupObservers: (() => void)[] = [];
beforeEach(() => {
  clearTransfers();
  setWorkspaceTransition('idle');
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
      workspace: { tenant_id: 'a', name: 'A', category: 'local', readonly: false },
      workspaces: [],
      roles: [],
      permissions: { can_edit: true, can_review: false, manage_users: false, manage_grants: false },
      csrf_token: 'c',
      must_change_password: false,
    },
  });
  posted = [];
  request.mockReset();
  request.mockImplementation(async (_path, init) => {
    posted.push(init.body);
    return { entity: { id: 'saved', payload: { row_count: 1, sheet_names: ['sheet'] } } };
  });
  vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(function (this: XMLHttpRequest, body) {
    posted.push(body as FormData);
    Object.defineProperty(this, 'status', { value: 200 });
    Object.defineProperty(this, 'responseText', { value: JSON.stringify({ id: 'saved' }) });
    this.onload?.(new ProgressEvent('load'));
    this.onloadend?.(new ProgressEvent('loadend'));
  });
});
afterEach(() => {
  for (const remove of cleanupObservers.splice(0)) remove();
  clearTransfers();
  vi.restoreAllMocks();
});
function AddTransfer({ target, files }: { target: IntakeTarget; files: File[] }) {
  const navigate = useNavigate();
  return (
    <button
      data-testid="transfer"
      onClick={() => {
        const id = stageFiles(scope, target, files);
        const path = '/' + target + '?upload=1&intake=' + id;
        window.history.replaceState(null, '', path);
        void navigate(path);
      }}
    >
      transfer
    </button>
  );
}
function mount(target: IntakeTarget, id = '', incoming: File[] = []) {
  const path = '/' + target + '?upload=1' + (id ? '&intake=' + id : '');
  window.history.replaceState(null, '', path);
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[path]}>
        {target === 'materials' ? (
          <Materials />
        ) : (
          <UploadDatasetModal onClose={vi.fn()} onUploaded={vi.fn()} />
        )}
        <AddTransfer target={target} files={incoming} />
      </MemoryRouter>
    </StrictMode>,
  );
}
const duplicates = (target: IntakeTarget) =>
  ['AA', 'BB'].map(
    (content) => new File([content], target === 'materials' ? 'same.md' : 'same.csv', { lastModified: 7 }),
  );
async function upload(target: IntakeTarget) {
  if (target === 'datasets')
    for (const select of screen.getAllByRole('combobox'))
      fireEvent.change(select, { target: { value: 'org' } });
  const button = document.querySelector(
    target === 'materials' ? '.materials-modal-actions .btn-primary' : '.ds-modal-footer .btn-primary',
  );
  expect(button).not.toBeNull();
  fireEvent.click(button!);
  await waitFor(() => expect(posted).toHaveLength(2));
}
for (const target of ['materials', 'datasets'] as const) {
  it(target + ' preserves same-metadata distinct File objects in a StrictMode handoff', async () => {
    const files = duplicates(target),
      id = stageFiles(scope, target, files);
    const acceptedCounts: number[] = [];
    const observe = () => {
      if (!readTransfer(id, scope, target))
        acceptedCounts.push(
          document.querySelectorAll(target === 'materials' ? '.materials-upload-item' : '.ds-import-item')
            .length,
        );
    };
    window.addEventListener('ontology:home-transfers', observe);
    cleanupObservers.push(() => window.removeEventListener('ontology:home-transfers', observe));
    mount(target, id);
    await waitFor(() => expect(screen.getAllByText(files[0].name, { exact: true })).toHaveLength(2));
    expect(readTransfer(id, scope, target)).toBeNull();
    expect(acceptedCounts).toEqual([2]);
    expect(posted).toHaveLength(0);
    await upload(target);
    expect(posted[0].get('file')).toBe(files[0]);
    expect(posted[1].get('file')).toBe(files[1]);
  });
  it(target + ' retains a handoff that collides with an existing picker queue entry', async () => {
    const files = duplicates(target);
    mount(target, '', [files[1]]);
    fireEvent.change(document.querySelector('input[type=file]')!, { target: { files: [files[0]] } });
    await screen.findByText(files[0].name, { exact: true });
    fireEvent.click(screen.getByTestId('transfer'));
    await waitFor(() => expect(screen.getAllByText(files[0].name, { exact: true })).toHaveLength(2));
    expect(posted).toHaveLength(0);
    await upload(target);
    expect(posted[0].get('file')).toBe(files[0]);
    expect(posted[1].get('file')).toBe(files[1]);
  });
  it(target + ' does not acknowledge a batch the full destination cannot receive', async () => {
    const count = target === 'materials' ? 40 : 20;
    const old = Array.from(
      { length: count },
      (_, i) => new File(['x'], i + (target === 'materials' ? '.md' : '.csv')),
    );
    const incoming = new File(['new'], target === 'materials' ? 'new.md' : 'new.csv');
    mount(target, '', [incoming]);
    fireEvent.change(document.querySelector('input[type=file]')!, { target: { files: old } });
    await screen.findByText(old.at(-1)!.name, { exact: true });
    fireEvent.click(screen.getByTestId('transfer'));
    const id = new URLSearchParams(window.location.search).get('intake')!;
    await screen.findByText(
      target === 'materials' ? terms.labels.home_material_limit : terms.labels.home_dataset_limit,
      { exact: true },
    );
    expect(readTransfer(id, scope, target)?.files[0]).toBe(incoming);
    expect(screen.queryByText(incoming.name, { exact: true })).not.toBeInTheDocument();
    expect(posted).toHaveLength(0);
    act(() => clearTransfers());
  });
}
