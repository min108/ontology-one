import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { DesignProvider } from '../design/DesignProvider';
import { theme } from '../design/theme';
import { terms } from '../i18n/zh-CN';
import { UserMenu } from './UserMenu';
import { WorkspaceHeader } from './WorkspaceHeader';
import { WorkspaceBreadcrumbs } from './WorkspaceBreadcrumbs';

const identity = vi.hoisted(() => ({
  session: {
    user: {
      id: 'account-1',
      username: 'test.admin',
      display_name: 'Test Administrator',
      role: 'admin',
      is_superadmin: true,
      home_tenant_id: 'hq',
    },
    workspace: { name: 'Headquarters', tenant_id: 'hq' },
    permissions: { manage_users: true, can_edit: true },
    workspaces: [{ name: 'Headquarters', tenant_id: 'hq' }],
    password_change_recommended: false,
  },
  logout: vi.fn(),
  permission_keys: ['admin.users'],
}));
vi.mock('../state/session', () => ({
  useSessionStore: (select?: (value: typeof identity) => unknown) => (select ? select(identity) : identity),
}));

vi.mock('@legacy/components/HarnessActivity', () => ({ default: () => null }));
vi.mock('../api/shell', () => ({ readAssistantStatus: async () => ({ configured: true }) }));

it('names the account trigger with its visible person and role and still opens account actions', async () => {
  render(
    <DesignProvider>
      <MemoryRouter>
        <UserMenu />
      </MemoryRouter>
    </DesignProvider>,
  );
  const button = screen.getByRole('button', {
    name: `${terms.labels.shell_account}：${identity.session.user.display_name} ${terms.roles.admin}`,
  });
  expect(button).toHaveTextContent(identity.session.user.display_name);
  expect(button).toHaveTextContent(terms.roles.admin);
  await userEvent.click(button);
  expect(await screen.findByRole('link', { name: terms.labels.shell_security })).toHaveAttribute(
    'href',
    '/account/security',
  );
});

it('renders an object return link inside its title with at least 4.5:1 contrast', async () => {
  render(
    <DesignProvider>
      <MemoryRouter initialEntries={['/materials/example']}>
        <div data-workspace-content>
          <div data-legacy-page>
            <header>
              <h1>Example</h1>
            </header>
          </div>
        </div>
        <WorkspaceBreadcrumbs />
      </MemoryRouter>
    </DesignProvider>,
  );
  const link = await screen.findByRole('link', { name: terms.labels.shell_materials });
  expect(link.closest('header')).not.toBeNull();
  const foreground = getComputedStyle(link)
    .color.match(/[\d.]+/g)
    ?.map(Number);
  expect(foreground).toBeDefined();
  const background = String(theme.token?.colorBgLayout)
    .match(/[a-f\d]{2}/gi)!
    .map((hex) => parseInt(hex, 16));
  const alpha = foreground![3] ?? 1;
  const composite = foreground!
    .slice(0, 3)
    .map((value, index) => value * alpha + background[index] * (1 - alpha));
  const luminance = (rgb: number[]) =>
    rgb
      .map((channel) => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      })
      .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const values = [luminance(composite), luminance(background)].sort((a, b) => b - a);
  expect((values[0] + 0.05) / (values[1] + 0.05)).toBeGreaterThanOrEqual(4.5);
});

it('list and workbench routes have no extra breadcrumb row', () => {
  const view = render(
    <MemoryRouter initialEntries={['/']}>
      <WorkspaceBreadcrumbs />
    </MemoryRouter>,
  );
  expect(view.container).toBeEmptyDOMElement();
});

it('includes the visible institution and role in the identity trigger and keeps its chooser operable', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <DesignProvider>
        <MemoryRouter>
          <WorkspaceHeader onSearch={() => {}} />
        </MemoryRouter>
      </DesignProvider>
    </QueryClientProvider>,
  );
  const trigger = screen.getByRole('button', {
    name: `${terms.labels.r3_ui_org_switch}：${identity.session.workspace.name} ${terms.roles.admin}`,
  });
  expect(trigger).toHaveTextContent(identity.session.workspace.name);
  expect(trigger).toHaveTextContent(terms.roles.admin);
  await userEvent.click(trigger);
  expect(await screen.findByRole('combobox', { name: terms.labels.organization })).toBeInTheDocument();
});
