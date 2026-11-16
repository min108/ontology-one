import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  AppShell,
  NavRail,
  TopBar,
  IdentityBar,
  PageHeader,
  StatusTag,
  KindTag,
  StateBlock,
  DataTable,
  FilterBar,
  HelpTip,
  ViewModeSwitch,
  ConfirmCard,
  Timeline,
  Omnibox,
  DropOverlay,
  TaskTray,
  NotificationCenter,
} from './index';
import { terms } from '../i18n/zh-CN';
import { brand } from '../i18n/brand';

const action = { label: terms.actions.retry, onClick: vi.fn() };
const items = [
  { key: 'home', label: terms.labels.home, href: '/' },
  { key: 'admin', label: terms.labels.admin, href: '/admin', permission: 'admin' },
];
const columns = [
  { key: 'name', dataIndex: 'name', title: terms.labels.sample_name },
  { key: 'owner', dataIndex: 'owner', title: terms.labels.sample_owner },
];
const rows = [{ id: '1', name: terms.labels.sample_title, owner: terms.labels.sample_person }];

describe('platform components', () => {
  it('AppShell renders its regions and content', () => {
    render(
      <AppShell
        navigation={<span>{terms.labels.navigation}</span>}
        topBar={<span>{terms.labels.workspace}</span>}
      >
        <p>{terms.labels.sample_title}</p>
      </AppShell>,
    );
    expect(screen.getByRole('main')).toHaveTextContent(terms.labels.sample_title);
  });
  it('NavRail hides denied entries', () => {
    render(<NavRail items={items} permissions={{}} currentKey="home" />);
    expect(screen.getByRole('link', { name: terms.labels.home })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText(terms.labels.admin)).not.toBeInTheDocument();
  });
  it('NavRail displays permitted entries', () => {
    render(<NavRail items={items} permissions={{ admin: true }} currentKey="admin" />);
    expect(screen.getByText(terms.labels.admin)).toBeInTheDocument();
  });
  it('NavRail calls navigation callback', () => {
    const onNavigate = vi.fn();
    render(<NavRail items={items} permissions={{}} currentKey="home" onNavigate={onNavigate} />);
    fireEvent.click(screen.getByText(terms.labels.home));
    expect(onNavigate).toHaveBeenCalledWith('/');
  });
  it('TopBar reads brand configuration and searches', () => {
    const onSearch = vi.fn();
    render(<TopBar workspaceName={terms.labels.sample_organization} onSearch={onSearch} />);
    expect(screen.getByText(brand.product_name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.search }));
    expect(onSearch).toHaveBeenCalled();
  });
  it('IdentityBar displays all three identity parts', () => {
    render(
      <IdentityBar
        organization={terms.labels.sample_organization}
        role={terms.labels.sample_role}
        scope={terms.labels.sample_scope}
      />,
    );
    expect(screen.getByText(terms.labels.sample_organization)).toBeInTheDocument();
    expect(screen.getByText(terms.labels.sample_role)).toBeInTheDocument();
    expect(screen.getByText(terms.labels.sample_scope)).toBeInTheDocument();
  });
  it('PageHeader has one main action', () => {
    render(<PageHeader title={terms.labels.sample_title} primaryAction={action} />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(terms.labels.sample_title);
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });
  it('StatusTag uses domain vocabulary', () => {
    render(<StatusTag domain="element" status="published" />);
    expect(screen.getByText(terms.statuses.element.published)).toBeInTheDocument();
  });
  it('StatusTag falls back safely for unknown values', () => {
    render(<StatusTag status="server_unknown" />);
    expect(screen.getByText(terms.labels.unknown_status)).toBeInTheDocument();
    expect(screen.queryByText('server_unknown')).not.toBeInTheDocument();
  });
  it('KindTag uses kind vocabulary', () => {
    render(<KindTag kind="metric" />);
    expect(screen.getByText(terms.kinds.metric)).toBeInTheDocument();
  });
  it('KindTag falls back safely', () => {
    render(<KindTag kind="server_unknown" />);
    expect(screen.getByText(terms.labels.unknown_kind)).toBeInTheDocument();
  });
  for (const state of ['loading', 'empty', 'forbidden', 'unconfigured', 'error', 'partial'] as const) {
    it(`StateBlock ${state} provides a next action`, () => {
      vi.useFakeTimers();
      render(<StateBlock state={state} action={action} />);
      if (state === 'loading') act(() => vi.advanceTimersByTime(300));
      fireEvent.click(screen.getByRole('button', { name: action.label }));
      expect(action.onClick).toHaveBeenCalled();
    });
  }
  it('StateBlock hides loading before 300ms', () => {
    vi.useFakeTimers();
    render(<StateBlock state="loading" action={action} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(299));
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('StateBlock shows phase after three seconds', () => {
    vi.useFakeTimers();
    render(<StateBlock state="loading" action={action} phase={terms.labels.sample_phase} />);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText(terms.labels.sample_phase)).toBeInTheDocument();
  });
  it('StateBlock collapses request identifier', () => {
    render(<StateBlock state="error" action={action} requestId="request-123" />);
    expect(screen.getByText(terms.labels.request_number).closest('details')).not.toHaveAttribute('open');
  });
  it('DataTable renders records', () => {
    render(<DataTable columns={columns} dataSource={rows} rowKey="id" emptyAction={action} />);
    expect(screen.getByText(terms.labels.sample_title)).toBeInTheDocument();
  });
  it('DataTable has actionable empty state', () => {
    render(<DataTable columns={columns} dataSource={[]} rowKey="id" emptyAction={action} />);
    expect(screen.getByRole('button', { name: action.label })).toBeInTheDocument();
  });
  it('DataTable restores column settings from URL', () => {
    window.history.replaceState(null, '', '/?table.columns=name&keep=yes#section');
    render(<DataTable columns={columns} dataSource={rows} rowKey="id" emptyAction={action} />);
    expect(screen.queryByRole('columnheader', { name: terms.labels.sample_owner })).not.toBeInTheDocument();
    expect(window.location.search).toContain('keep=yes');
  });
  it('DataTable updates columns in URL', async () => {
    render(<DataTable columns={columns} dataSource={rows} rowKey="id" emptyAction={action} />);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.columns }));
    fireEvent.click(await screen.findByRole('checkbox', { name: terms.labels.sample_owner }));
    expect(new URLSearchParams(window.location.search).get('table.columns')).toBe('name');
  });
  it('DataTable never shows compact control in simple view', () => {
    render(
      <DataTable columns={columns} dataSource={rows} rowKey="id" emptyAction={action} viewMode="simple" />,
    );
    expect(screen.queryByText(terms.actions.compact)).not.toBeInTheDocument();
  });
  it('FilterBar calls onChange', () => {
    const onChange = vi.fn();
    render(<FilterBar value="" onChange={onChange} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'abc' } });
    expect(onChange).toHaveBeenCalledWith('abc');
  });
  it('HelpTip has accessible explanation control', () => {
    render(<HelpTip term="caliber" />);
    expect(screen.getByRole('button', { name: terms.labels.help })).toBeInTheDocument();
  });
  it('ViewModeSwitch calls controlled callback', () => {
    const onChange = vi.fn();
    render(<ViewModeSwitch value="simple" onChange={onChange} />);
    fireEvent.click(screen.getByText(terms.actions.professional));
    expect(onChange).toHaveBeenCalledWith('professional');
  });
  it('ConfirmCard explains impact and confirms', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmCard
        level="L2"
        summary={terms.labels.sample_rule}
        impact={terms.labels.sample_impact}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(terms.labels.sample_impact)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.submit }));
    expect(onConfirm).toHaveBeenCalled();
  });
  it('Timeline offers version comparison', () => {
    const compare = vi.fn();
    render(
      <Timeline
        items={[
          { id: '1', title: terms.labels.sample_title, time: terms.labels.sample_time, onCompare: compare },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: terms.actions.compare }));
    expect(compare).toHaveBeenCalled();
  });
  it('Omnibox supports keyboard result selection', () => {
    const select = vi.fn();
    render(
      <Omnibox
        open
        onOpenChange={vi.fn()}
        value=""
        onSearch={vi.fn()}
        items={[
          { id: 'a', label: terms.labels.home },
          { id: 'b', label: terms.labels.build },
        ]}
        onSelect={select}
      />,
    );
    const input = screen.getByRole('combobox');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'b' }));
  });
  it('Omnibox escape closes dialog', () => {
    const close = vi.fn();
    render(<Omnibox open onOpenChange={close} value="" onSearch={vi.fn()} items={[]} onSelect={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    expect(close).toHaveBeenCalledWith(false);
  });
  it('DropOverlay ignores text dragging', () => {
    render(<DropOverlay onFiles={vi.fn()} />);
    fireEvent.dragEnter(document, { dataTransfer: { types: ['text/plain'] } });
    expect(screen.queryByText(terms.labels.drop_title)).not.toBeInTheDocument();
  });
  it('DropOverlay passes dropped files', () => {
    const onFiles = vi.fn();
    render(<DropOverlay onFiles={onFiles} />);
    const file = new File(['abc'], 'memo.txt');
    fireEvent.dragEnter(document, { dataTransfer: { types: ['Files'] } });
    expect(screen.getByText(terms.labels.drop_title)).toBeInTheDocument();
    fireEvent.drop(document, { dataTransfer: { files: [file], types: ['Files'] } });
    expect(onFiles).toHaveBeenCalledWith([file]);
  });
  it('TaskTray renders actual progress', async () => {
    render(
      <TaskTray items={[{ id: '1', title: terms.labels.sample_task, status: 'running', progress: 40 }]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: terms.actions.show_tasks }));
    expect(await screen.findByText(terms.labels.sample_task)).toBeInTheDocument();
  });
  it('NotificationCenter shows unread items', async () => {
    render(
      <NotificationCenter
        items={[{ id: '1', title: terms.labels.sample_notice, read: false }]}
        onRead={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: terms.actions.show_notifications }));
    expect(await screen.findByText(terms.labels.sample_notice)).toBeInTheDocument();
  });
  it('TopBar contains no unfinished tray entry', () => {
    render(<TopBar workspaceName={terms.labels.sample_organization} />);
    expect(screen.queryByRole('button', { name: terms.actions.show_tasks })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: terms.actions.show_notifications })).not.toBeInTheDocument();
  });
  it('AppShell retains identity region', () => {
    render(
      <AppShell
        navigation={null}
        topBar={null}
        identity={
          <IdentityBar
            organization={terms.labels.sample_organization}
            role={terms.labels.sample_role}
            scope={terms.labels.sample_scope}
          />
        }
      >
        <p>{terms.labels.sample_title}</p>
      </AppShell>,
    );
    expect(within(screen.getByRole('main')).getByText(terms.labels.sample_title)).toBeInTheDocument();
  });
});
