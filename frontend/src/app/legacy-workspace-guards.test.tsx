import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useSessionValue } from '@legacy/navigation';
import { Modal } from '@legacy/components/ui';
import { UploadDatasetModal } from '@legacy/pages/Datasets';
import { AuthorityDetail } from '@legacy/pages/Authority';
import { blankAuthoritySpec } from '@legacy/pages/authority-types';
import { HarnessConfigurationPanel } from '@legacy/components/HarnessConfiguration';
import { readWorkspaceGuards, setWorkspaceTransition } from '../state/workspace-guard';

const { request, queryData } = vi.hoisted(() => ({
  request: vi.fn(),
  queryData: new Map<string, unknown>(),
}));
vi.mock('@legacy/api', () => ({
  api: request,
  statusLabel: (value: string) => value,
  useApi: (path: string) => ({
    data: queryData.get(path) || { items: [{ id: 'org', title: 'Org' }] },
    reload: vi.fn(),
  }),
}));
vi.mock('@legacy/components/AuthorityFlowEditor', () => ({ default: () => null }));
vi.mock('@legacy/components/LegacyHarnessConversation', () => ({ useHarnessResultRestore: () => {} }));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({
    session: {
      permissions: { can_edit: true, manage_users: true },
      workspace: { tenant_id: 'org', readonly: false },
      user: { home_tenant_id: 'org' },
    },
  }),
}));

beforeEach(() => {
  setWorkspaceTransition('idle');
  sessionStorage.clear();
  request.mockReset();
  queryData.clear();
});
afterEach(() => {
  window.dispatchEvent(new Event('ontology:scope-cleared'));
  sessionStorage.clear();
  setWorkspaceTransition('idle');
});

describe('legacy draft and modal cooperation', () => {
  it('loading saved model settings does not mark an already typed new key as saved', () => {
    const { rerender } = render(<HarnessConfigurationPanel status={null} onSaved={vi.fn()} />);
    fireEvent.change(document.querySelector<HTMLInputElement>('input[type="password"]')!, {
      target: { value: 'unsaved-example' },
    });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    rerender(<HarnessConfigurationPanel status={{ model: 'configured-model' }} onSaved={vi.fn()} />);
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
  });
  it('Authority reverts, keeps failed saves dirty, and suppresses a second leave prompt', async () => {
    const entity = {
      id: 'rule',
      title: 'Saved title',
      summary: '',
      status: 'draft',
      version: 1,
      domain: 'authority',
      source_kind: 'user',
      payload: { authority_spec: blankAuthoritySpec('workflow') },
    };
    queryData.set('/authority/assets/rule', { entity, checks: { errors: [], warnings: [] }, sources: [] });
    queryData.set('/authority/catalog', { organizations: [], people: [], items: [], stats: {} });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <MemoryRouter initialEntries={['/authority/rule']}>
        <Routes>
          <Route path="/authority/:id" element={<AuthorityDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    const title = await screen.findByDisplayValue('Saved title');
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    fireEvent.change(title, { target: { value: 'Changed title' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    fireEvent.change(title, { target: { value: 'Saved title' } });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    fireEvent.change(title, { target: { value: 'Changed title' } });
    request.mockRejectedValueOnce(new Error('save failed'));
    fireEvent.click(document.querySelector<HTMLButtonElement>('.authority-savebar .btn-primary')!);
    await screen.findByText('save failed');
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    request.mockResolvedValueOnce({ entity: { ...entity, title: 'Changed title' } });
    fireEvent.click(document.querySelector<HTMLButtonElement>('.authority-savebar .btn-primary')!);
    await waitFor(() => expect(readWorkspaceGuards().dirtyCount).toBe(0));
    fireEvent.change(title, { target: { value: 'Another edit' } });
    const before = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(true);
    setWorkspaceTransition('leaving');
    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
    fireEvent.click(document.querySelector<HTMLAnchorElement>('a[href="/authority"]')!);
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
  it('restored asset draft is dirty against server baseline and survives closing', () => {
    sessionStorage.setItem('ontology-one-draft-asset-edit-a-1-title', JSON.stringify('local'));
    const editor = renderHook(() => useSessionValue('asset-edit-a-1-title', 'server'));
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    editor.unmount();
    expect(readWorkspaceGuards()).toMatchObject({ dirtyCount: 0, storedDraftCount: 1 });
    sessionStorage.removeItem('ontology-one-draft-asset-edit-a-1-title');
    expect(readWorkspaceGuards().storedDraftCount).toBe(0);
  });
  it('editing and reverting a stored field clears dirty metadata', () => {
    const editor = renderHook(() => useSessionValue('asset-edit-a-1-title', 'server'));
    expect(readWorkspaceGuards().storedDraftCount).toBe(0);
    act(() => editor.result.current[1]('changed'));
    expect(readWorkspaceGuards().storedDraftCount).toBe(1);
    act(() => editor.result.current[1]('server'));
    expect(readWorkspaceGuards()).toMatchObject({ dirtyCount: 0, storedDraftCount: 0 });
  });
  it('old modal does not intercept Escape, Tab or delayed focus while confirmation is open', async () => {
    const close = vi.fn();
    render(
      <>
        <button data-testid="confirmation">Switch</button>
        <Modal title="Edit" onClose={close}>
          <input aria-label="old field" />
        </Modal>
      </>,
    );
    setWorkspaceTransition('confirming');
    screen.getByTestId('confirmation').focus();
    fireEvent.keyDown(document, { key: 'Escape' });
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    document.dispatchEvent(tab);
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(close).not.toHaveBeenCalled();
    expect(tab.defaultPrevented).toBe(false);
    expect(screen.getByTestId('confirmation')).toHaveFocus();
    setWorkspaceTransition('idle');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalledOnce();
  });
});

describe('real dataset upload batch', () => {
  it('keeps a lease between files and clears it after all successes', async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    request
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(() => {
        expect(readWorkspaceGuards().uploadCount).toBe(1);
        return new Promise((resolve) => {
          resolveSecond = resolve;
        });
      });
    render(
      <MemoryRouter>
        <UploadDatasetModal onClose={vi.fn()} onUploaded={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'org' } });
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, { target: { files: [new File(['a'], 'a.csv'), new File(['b'], 'b.csv')] } });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    fireEvent.click(document.querySelector<HTMLButtonElement>('.ds-modal-footer .btn-primary')!);
    expect(readWorkspaceGuards().uploadCount).toBe(1);
    const result = (id: string) => ({ entity: { id, payload: { row_count: 1, sheet_names: ['sheet'] } } });
    await act(async () => resolveFirst(result('a')));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(readWorkspaceGuards().uploadCount).toBe(1);
    await act(async () => resolveSecond(result('b')));
    expect(readWorkspaceGuards()).toMatchObject({ dirtyCount: 0, uploadCount: 0 });
  });
});
