import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthorityDetail } from '@legacy/pages/Authority';
import { blankAuthoritySpec } from '@legacy/pages/authority-types';
import { setWorkspaceTransition } from '../state/workspace-guard';
import { requestRouteNavigation } from './guardedNavigation';

const { queries } = vi.hoisted(() => ({ queries: new Map<string, unknown>() }));
vi.mock('@legacy/api', () => ({
  api: vi.fn(),
  statusLabel: (value: string) => value,
  useApi: (path: string) => ({ data: queries.get(path), reload: vi.fn() }),
}));
vi.mock('@legacy/components/AuthorityFlowEditor', () => ({ default: () => null }));
vi.mock('@legacy/components/LegacyHarnessConversation', () => ({ useHarnessResultRestore: () => {} }));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({
    session: {
      permissions: { can_edit: true },
      workspace: { tenant_id: 'org', readonly: false },
      user: { home_tenant_id: 'org' },
    },
  }),
}));

const message = '当前规范有尚未保存的修改，确定离开并放弃这些修改吗？';
beforeEach(() => {
  window.history.replaceState(null, '', '/authority/rule?tab=content#start');
  setWorkspaceTransition('idle');
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
  setWorkspaceTransition('idle');
  vi.restoreAllMocks();
});

function editor(dirty = true) {
  const mounted = render(
    <MemoryRouter initialEntries={['/authority/rule?tab=content#start']}>
      <Routes>
        <Route path="/authority/:id" element={<AuthorityDetail />} />
      </Routes>
    </MemoryRouter>,
  );
  const field = screen.getByLabelText('规范名称');
  if (dirty) fireEvent.change(field, { target: { value: 'Unsaved authority title' } });
  return { ...mounted, field };
}

describe('programmatic navigation cooperates with the actual legacy Authority listener', () => {
  it('navigates exactly once when no legacy editor is mounted', () => {
    const navigate = vi.fn();
    expect(requestRouteNavigation('/materials?from=search#upload', navigate)).toBe(true);
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/materials?from=search#upload');
  });
  it('cancel retains the real dirty editor and does not invoke navigate', () => {
    const { field } = editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const navigate = vi.fn();
    expect(requestRouteNavigation('/materials', navigate)).toBe(false);
    expect(confirm).toHaveBeenCalledExactlyOnceWith(message);
    expect(navigate).not.toHaveBeenCalled();
    expect(field).toHaveValue('Unsaved authority title');
    expect(window.location.pathname).toBe('/authority/rule');
  });
  it('accept invokes navigate once with the original query and hash', () => {
    editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const navigate = vi.fn();
    expect(requestRouteNavigation('/materials?from=search#upload', navigate)).toBe(true);
    expect(confirm).toHaveBeenCalledExactlyOnceWith(message);
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/materials?from=search#upload');
  });
  it('clean and reverted drafts do not prompt', () => {
    const { field } = editor(false);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    expect(requestRouteNavigation('/materials', vi.fn())).toBe(true);
    fireEvent.change(field, { target: { value: 'edit' } });
    fireEvent.change(field, { target: { value: 'Saved title' } });
    expect(requestRouteNavigation('/materials', vi.fn())).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });
  it('same pathname and search preserve the existing hash-only bypass', () => {
    editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const navigate = vi.fn();
    expect(requestRouteNavigation('#next', navigate)).toBe(true);
    expect(navigate).toHaveBeenCalledExactlyOnceWith('#next');
    expect(confirm).not.toHaveBeenCalled();
  });
  it('a changed query on the same pathname still requires confirmation', () => {
    editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    expect(requestRouteNavigation('/authority/rule?tab=other#next', vi.fn())).toBe(false);
    expect(confirm).toHaveBeenCalledExactlyOnceWith(message);
  });
  it('an accepted workspace departure does not ask a second confirmation', () => {
    editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    setWorkspaceTransition('leaving');
    expect(requestRouteNavigation('/', vi.fn())).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });
  it('unmount removes the legacy programmatic listener', () => {
    const mounted = editor();
    mounted.unmount();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    expect(requestRouteNavigation('/', vi.fn())).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });
  it('existing anchor clicks keep their one original confirmation', () => {
    editor();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(document.querySelector<HTMLAnchorElement>('a[href="/authority"]')!);
    expect(confirm).toHaveBeenCalledExactlyOnceWith(message);
    expect(screen.getByLabelText('规范名称')).toHaveValue('Unsaved authority title');
  });
});
