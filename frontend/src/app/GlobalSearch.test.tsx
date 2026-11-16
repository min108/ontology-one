import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GlobalSearch, parseSearchItems } from './GlobalSearch';
import { invokeCapability } from '../api/client';
import { queryClient, workspaceQueryKey } from '../api/query';
import { useScopeStore } from '../state/scope';
import { terms } from '../i18n/zh-CN';

vi.mock('../api/client', () => ({ invokeCapability: vi.fn() }));
const result = { items: [{ id: 'MAT-1', kind: 'material', title: 'Needle result', summary: 'Summary' }] };
function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}
function renderSearch(onClose = vi.fn()) {
  return {
    onClose,
    ...render(
      <MemoryRouter>
        <GlobalSearch open onClose={onClose} />
        <Location />
      </MemoryRouter>,
    ),
  };
}
async function search(value: string) {
  fireEvent.change(screen.getByRole('combobox'), { target: { value } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(251);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  queryClient.clear();
  useScopeStore.getState().setScope('a');
  vi.mocked(invokeCapability).mockReset();
  vi.mocked(invokeCapability).mockResolvedValue(result);
});
afterEach(() => {
  queryClient.clear();
  vi.useRealTimers();
});
it('waits 250ms, trims the query, and uses tenant plus scope cache keys', async () => {
  renderSearch();
  expect(invokeCapability).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '  needle  ' } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(249);
  });
  expect(invokeCapability).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(invokeCapability).toHaveBeenCalledWith(
    'semantic.search',
    { q: 'needle' },
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(
    queryClient.getQueryData(workspaceQueryKey('a', 'local', 'semantic.search', 'needle')),
  ).toBeDefined();
  expect(screen.getByRole('option')).toHaveTextContent('Needle result');
});
it('replaces the debounce timer on edits and never submits blank text', async () => {
  renderSearch();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'first' } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(150);
  });
  await search('second');
  expect(invokeCapability).toHaveBeenCalledTimes(1);
  await search('  ');
  expect(invokeCapability).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
});
it('clears old results immediately when scope changes and ignores its late response', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(invokeCapability).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  renderSearch();
  await search('old');
  act(() => useScopeStore.getState().setScope('b', 'local'));
  expect(screen.getByRole('combobox')).toHaveValue('');
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
  await act(async () => {
    resolve(result);
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
  await search('new');
  expect(screen.getByRole('option')).toHaveTextContent('Needle result');
  act(() => useScopeStore.getState().setScope('b', 'group'));
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
});
it('shows an error without stale rows and supports explicit retry', async () => {
  vi.mocked(invokeCapability).mockRejectedValueOnce(new Error('internal detail'));
  renderSearch();
  await search('needle');
  expect(screen.getByRole('alert')).toHaveTextContent(terms.labels.global_search_failed);
  expect(screen.queryByText('internal detail')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.retry }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByRole('option')).toHaveTextContent('Needle result');
});
it('navigates using the legacy entity route and closes the dialog', async () => {
  const { onClose } = renderSearch();
  await search('needle');
  fireEvent.click(screen.getByRole('option'));
  expect(screen.getByTestId('location')).toHaveTextContent('/materials/MAT-1');
  expect(onClose).toHaveBeenCalledOnce();
});
it('rejects malformed responses and safely handles nullable entity text', () => {
  for (const value of [
    null,
    {},
    { items: [null] },
    { items: [{ id: 1, kind: 'material' }] },
    { items: [{ id: 'x', kind: 'material', title: {} }] },
  ]) {
    expect(() => parseSearchItems(value)).toThrow();
  }
  expect(
    parseSearchItems({ items: [{ id: 'x', kind: 'material', title: null, summary: null }] })[0].label,
  ).toBe('x');
});
it('hides prior rows while a different query is debouncing', async () => {
  renderSearch();
  await search('needle');
  expect(screen.getByRole('option')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'different' } });
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
});

it('keeps the group search entry only for federation.read and closes on navigation', async () => {
  const { useSessionStore } = await import('../state/session');
  useSessionStore.setState({ permission_keys: [] });
  const { onClose } = renderSearch();
  expect(screen.queryByRole('link', { name: terms.labels.global_search_group })).not.toBeInTheDocument();
  act(() => useSessionStore.setState({ permission_keys: ['federation.read'] }));
  fireEvent.click(screen.getByRole('link', { name: terms.labels.global_search_group }));
  expect(screen.getByTestId('location')).toHaveTextContent('/group');
  expect(onClose).toHaveBeenCalledOnce();
  useSessionStore.setState({ permission_keys: [] });
});

it('lists built commands immediately and Enter routes without semantic search', async () => {
  const { onClose } = renderSearch();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '/briefing' } });
  expect(screen.getByRole('option')).toHaveTextContent(terms.labels.home_command_briefing);
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(screen.getByTestId('location')).toHaveTextContent('/briefing');
  expect(onClose).toHaveBeenCalledOnce();
  expect(invokeCapability).not.toHaveBeenCalled();
});
it('groups results using known business kinds', () => {
  const items = parseSearchItems({
    items: [
      { id: 'm', kind: 'material', title: 'Material' },
      { id: 'k', kind: 'knowledge', title: 'Knowledge' },
    ],
  });
  expect(items.map((item) => item.group)).toEqual([terms.kinds.material, terms.kinds.knowledge]);
});

it('keeps search open and the current route when a retained editor refuses navigation', async () => {
  let refuse = true;
  const guard = (event: Event) => {
    if (refuse) event.preventDefault();
  };
  window.addEventListener('ontology:request-route-navigation', guard);
  try {
    const { onClose } = renderSearch();
    await search('needle');
    fireEvent.click(screen.getByRole('option'));
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox')).toHaveValue('needle');
    refuse = false;
    fireEvent.click(screen.getByRole('option'));
    expect(screen.getByTestId('location')).toHaveTextContent('/materials/MAT-1');
    expect(onClose).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener('ontology:request-route-navigation', guard);
  }
});
