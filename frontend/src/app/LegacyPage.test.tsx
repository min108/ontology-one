import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LegacyPage } from './LegacyPage';
import { WorkspaceBreadcrumbs } from './WorkspaceBreadcrumbs';
import { readWorkspaceGuards, setWorkspaceTransition } from '../state/workspace-guard';
const { api, reload } = vi.hoisted(() => ({ api: vi.fn(), reload: vi.fn() }));
vi.mock('@legacy/navigation', () => ({
  ContextTrail: () => (
    <div className="context-trail">
      <a className="context-back" href="/projects/parent?via=source">
        返回原工作
      </a>
      <details className="context-belonging">
        <summary>所属工作与专栏</summary>
        <a href="/knowledge/collections/parent">业务专栏</a>
      </details>
    </div>
  ),
}));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({ session: { workspace: { tenant_id: 'hq' }, permissions: { can_edit: true } } }),
}));
vi.mock('@legacy/api', () => {
  const data = {
    entity: { visibility: 'institution', tenant_name: '总部', status: 'published' },
    can_set_visibility: true,
    visibility_options: ['institution', 'headquarters'],
  };
  return { api, statusLabel: () => '已发布', useApi: () => ({ data, reload }) };
});
beforeEach(() => {
  api.mockReset().mockResolvedValue({ ok: true });
  reload.mockReset();
  setWorkspaceTransition('idle');
});
afterEach(() => setWorkspaceTransition('idle'));
function show(children: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/materials/material']}>
      <div data-workspace-content>
        <LegacyPage>{children}</LegacyPage>
      </div>
      <WorkspaceBreadcrumbs />
    </MemoryRouter>,
  );
}

it('keeps original source and business-parent links plus editable visibility inside the object title', async () => {
  show(
    <div className="page">
      <header className="page-head">
        <div>
          <h1>材料</h1>
        </div>
      </header>
    </div>,
  );
  const back = await screen.findByRole('link', { name: '返回原工作' });
  await waitFor(() => expect(back.closest('header')).not.toBeNull());
  expect(back).toHaveAttribute('href', '/projects/parent?via=source');
  fireEvent.click(screen.getByText('所属工作与专栏'));
  expect(screen.getByRole('link', { name: '业务专栏' })).toHaveAttribute(
    'href',
    '/knowledge/collections/parent',
  );
  expect(screen.getByLabelText('设置成果可见范围').closest('header')).not.toBeNull();
  expect(screen.queryByText('素材整理')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('设置成果可见范围'), { target: { value: 'headquarters' } });
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
  fireEvent.click(screen.getByRole('button', { name: '保存范围' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(expect.stringContaining('/visibility'), {
      method: 'POST',
      body: JSON.stringify({ visibility: 'headquarters' }),
    }),
  );
  await waitFor(() => expect(readWorkspaceGuards().dirtyCount).toBe(0));
});

it('moves one stable control tree into a late or replaced header without discarding unsaved selection', async () => {
  const view = show(<p>正在读取材料</p>);
  const selection = screen.getByLabelText('设置成果可见范围');
  fireEvent.change(selection, { target: { value: 'headquarters' } });
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
  const rerender = (title: string) =>
    view.rerender(
      <MemoryRouter initialEntries={['/materials/material']}>
        <div data-workspace-content>
          <LegacyPage>
            <div className="page">
              <header className="page-head" key={title}>
                <div>
                  <h1>{title}</h1>
                </div>
              </header>
            </div>
          </LegacyPage>
        </div>
        <WorkspaceBreadcrumbs />
      </MemoryRouter>,
    );
  await act(async () => rerender('材料'));
  await waitFor(() => expect(selection.closest('header')).not.toBeNull());
  await act(async () => rerender('材料新标题'));
  await waitFor(() => expect(selection.closest('header')).toHaveTextContent('材料新标题'));
  expect(screen.getAllByLabelText('设置成果可见范围')).toHaveLength(1);
  expect(screen.getByLabelText('设置成果可见范围')).toBe(selection);
  expect(selection).toHaveValue('headquarters');
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
});
