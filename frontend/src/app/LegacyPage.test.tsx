import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LegacyPage } from './LegacyPage';
import { WorkspaceBreadcrumbs } from './WorkspaceBreadcrumbs';
import { terms } from '../i18n/zh-CN';
import { readWorkspaceGuards, setWorkspaceTransition } from '../state/workspace-guard';
const { api, reload } = vi.hoisted(() => ({ api: vi.fn(), reload: vi.fn() }));
vi.mock('@legacy/navigation', () => ({
  ContextTrail: () => (
    <div className="context-trail">
      <a className="context-back" href="/projects/parent?via=source">
        Return to source work
      </a>
      <details className="context-belonging">
        <summary>Business parents</summary>
        <a href="/knowledge/collections/parent">Business collection</a>
      </details>
    </div>
  ),
}));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({ session: { workspace: { tenant_id: 'hq' }, permissions: { can_edit: true } } }),
}));
vi.mock('@legacy/api', () => {
  const data = {
    entity: { visibility: 'institution', tenant_name: 'Headquarters', status: 'published' },
    can_set_visibility: true,
    visibility_options: ['institution', 'headquarters'],
  };
  return { api, statusLabel: () => 'Published', useApi: () => ({ data, reload }) };
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
          <h1>Material</h1>
        </div>
      </header>
    </div>,
  );
  const back = await screen.findByRole('link', { name: 'Return to source work' });
  await waitFor(() => expect(back.closest('header')).not.toBeNull());
  expect(back).toHaveAttribute('href', '/projects/parent?via=source');
  fireEvent.click(screen.getByText('Business parents'));
  expect(screen.getByRole('link', { name: 'Business collection' })).toHaveAttribute(
    'href',
    '/knowledge/collections/parent',
  );
  expect(screen.getByRole('combobox').closest('header')).not.toBeNull();
  expect(screen.queryByText(terms.labels.shell_materials)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'headquarters' } });
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
  const scope = screen.getByRole('combobox').closest('aside')!;
  expect(screen.getByRole('combobox')).toHaveAccessibleName();
  const save = within(scope).getByRole('button');
  expect(save).toHaveAccessibleName();
  fireEvent.click(save);
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(expect.stringContaining('/visibility'), {
      method: 'POST',
      body: JSON.stringify({ visibility: 'headquarters' }),
    }),
  );
  await waitFor(() => expect(readWorkspaceGuards().dirtyCount).toBe(0));
});

it('moves one stable control tree into a late or replaced header without discarding unsaved selection', async () => {
  const view = show(<p>Loading material</p>);
  const selection = screen.getByRole('combobox');
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
  await act(async () => rerender('Material'));
  await waitFor(() => expect(selection.closest('header')).not.toBeNull());
  await act(async () => rerender('Updated material'));
  await waitFor(() => expect(selection.closest('header')).toHaveTextContent('Updated material'));
  expect(screen.getAllByRole('combobox')).toHaveLength(1);
  expect(screen.getByRole('combobox')).toBe(selection);
  expect(selection).toHaveValue('headquarters');
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
});
