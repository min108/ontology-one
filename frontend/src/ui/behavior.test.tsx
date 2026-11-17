import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DataTable, DropOverlay, Omnibox } from './index';
import { terms } from '../i18n/zh-CN';

const action = { label: terms.actions.create, onClick: vi.fn() };
const columns = ['name', 'owner', 'updated', 'type', 'status', 'version', 'department'].map((key) => ({
  key,
  dataIndex: key,
  title: terms.labels[`sample_${key}` as keyof typeof terms.labels],
}));

describe('navigation and table boundaries', () => {
  it('DataTable shows at most six columns by default', () => {
    render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
      />,
    );
    expect(screen.getAllByRole('columnheader')).toHaveLength(6);
  });
  it('DataTable restores browser navigation changes', () => {
    render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
      />,
    );
    act(() => {
      window.history.replaceState(null, '', '/?table.columns=name');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getAllByRole('columnheader')).toHaveLength(1);
  });
  it('DataTable permits compact density in professional view', () => {
    render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
        viewMode="professional"
      />,
    );
    fireEvent.click(screen.getByText(terms.actions.compact));
    expect(new URLSearchParams(window.location.search).get('table.density')).toBe('compact');
  });
  it('DataTable cannot restore unknown columns from URL', () => {
    window.history.replaceState(null, '', '/?table.columns=unknown');
    render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
      />,
    );
    expect(screen.getAllByRole('columnheader')).toHaveLength(6);
  });
  it('restores readable business columns when the saved selection is professional-only', () => {
    window.history.replaceState(null, '', '/?table.columns=department');
    const props = {
      columns,
      dataSource: [{ id: 'record', name: 'Saved business name' }],
      rowKey: 'id',
      emptyAction: action,
    };
    const { rerender } = render(<DataTable {...props} viewMode="professional" />);
    expect(screen.getAllByRole('columnheader')).toHaveLength(1);
    rerender(<DataTable {...props} viewMode="simple" />);
    expect(screen.getAllByRole('columnheader')).toHaveLength(6);
    expect(screen.getByText('Saved business name')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: terms.actions.columns })).not.toBeInTheDocument();
  });
  it('Omnibox ArrowUp wraps to the last result', () => {
    const onSelect = vi.fn();
    render(
      <Omnibox
        open
        onOpenChange={vi.fn()}
        value=""
        onSearch={vi.fn()}
        items={[
          { id: 'first', label: terms.labels.home },
          { id: 'last', label: terms.labels.build },
        ]}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowUp' });
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'last' }));
  });
  it('Omnibox does not select from an empty result', () => {
    const onSelect = vi.fn();
    render(
      <Omnibox open onOpenChange={vi.fn()} value="" onSearch={vi.fn()} items={[]} onSelect={onSelect} />,
    );
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
  });
  it('DropOverlay stays visible across nested drag entries', () => {
    render(<DropOverlay onFiles={vi.fn()} />);
    const data = { dataTransfer: { types: ['Files'] } };
    fireEvent.dragEnter(document, data);
    fireEvent.dragEnter(document, data);
    fireEvent.dragLeave(document, data);
    expect(screen.getByText(terms.labels.drop_title)).toBeInTheDocument();
    fireEvent.dragLeave(document, data);
    expect(screen.queryByText(terms.labels.drop_title)).not.toBeInTheDocument();
  });
  it('DropOverlay respects disabled state', () => {
    render(<DropOverlay enabled={false} onFiles={vi.fn()} />);
    fireEvent.dragEnter(document, { dataTransfer: { types: ['Files'] } });
    expect(screen.queryByText(terms.labels.drop_title)).not.toBeInTheDocument();
  });
  it('DataTable resets compact density when switching to simple view', () => {
    window.history.replaceState(null, '', '/?table.density=compact');
    const { rerender, container } = render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
        viewMode="professional"
      />,
    );
    expect(container.querySelector('.ant-table-small')).not.toBeNull();
    rerender(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
        viewMode="simple"
      />,
    );
    expect(container.querySelector('.ant-table-small')).toBeNull();
  });
  it('DataTable replaces stale selected columns after the column contract changes', () => {
    const { rerender } = render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
      />,
    );
    rerender(
      <DataTable
        columns={[{ key: 'new', title: terms.labels.sample_name }]}
        dataSource={[{ id: 'record', name: terms.labels.sample_name }]}
        rowKey="id"
        emptyAction={action}
      />,
    );
    expect(screen.getByRole('columnheader', { name: terms.labels.sample_name })).toBeInTheDocument();
  });
  it('Omnibox lets the input method confirm its candidate', () => {
    const onSelect = vi.fn();
    render(
      <Omnibox
        open
        onOpenChange={vi.fn()}
        value=""
        onSearch={vi.fn()}
        items={[{ id: 'first', label: terms.labels.home }]}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter', isComposing: true });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter', keyCode: 229 });
    expect(onSelect).not.toHaveBeenCalled();
  });
});
