import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useScopeStore } from '../../state/scope';
import { useSessionStore } from '../../state/session';
import { testQuery, testSession } from '../build/testQuery';
import MaterialReaderPage from './MaterialReaderPage';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));

const original = {
  material_id: 'M',
  title: 'Payment record',
  status: 'published',
  source_version: 'V',
  version: 3,
  locator: 'Article 1',
  offset: 0,
  total: 285000,
};

function open(role: 'viewer' | 'analyst', target = '') {
  testSession();
  useSessionStore.setState(({ session }) => ({
    session: { ...session!, user: { ...session!.user, role } },
    switching: false,
  }));
  return render(
    <MemoryRouter initialEntries={['/materials/M' + target]}>
      <Link to="/materials/N?anchor=A&source_version=V&citing_relation_id=REL">Other source</Link>
      <Routes>
        <Route path="/materials/:id" element={<MaterialReaderPage />} />
      </Routes>
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
}

beforeEach(() => {
  vi.mocked(api).mockReset();
});

it.each([
  ['viewer', '', ''],
  ['analyst', '?tab=source&chunk=first', '&chunk_id=first'],
] as const)(
  'pages the %s original by Unicode characters and withholds a previous page on denial',
  async (role, target, apiSelectors) => {
    const first = '📄' + 'a'.repeat(4095);
    vi.mocked(api).mockResolvedValueOnce({ ...original, text: first });
    let finish!: (value: unknown) => void;
    vi.mocked(api).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.mocked(api).mockRejectedValueOnce(new ApiError('Access revoked', 404));
    const { container } = open(role, target);
    await screen.findByRole('heading', { name: original.title });
    expect(container.querySelector('[data-source-viewer] blockquote')?.textContent).toBe(first);
    expect(screen.getByText(/Article 1/)).toHaveTextContent('3');
    expect(screen.getByText(terms.statuses.element.published)).toBeVisible();
    expect(container.querySelector('[data-source-viewer] mark') !== null).toBe(Boolean(apiSelectors));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_next }));
    await waitFor(() =>
      expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
        '/v2/materials/M/source?offset=0' + apiSelectors,
        '/v2/materials/M/source?offset=4096' + apiSelectors + '&source_version=V',
      ]),
    );
    expect(container.querySelector('[data-source-viewer]')).toBeNull();
    await act(async () => finish({ ...original, offset: 4096, text: 'Second passage' }));
    expect(await screen.findByText('Second passage')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_previous }));
    expect(await screen.findByText('Access revoked')).toBeVisible();
    expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toBe(
      '/v2/materials/M/source?offset=0' + apiSelectors + '&source_version=V',
    );
    expect(container.querySelector('[data-source-viewer]')).toBeNull();
    expect(screen.queryByRole('heading', { name: original.title })).toBeNull();
  },
);

it('does not display a replacement revision between pages', async () => {
  const first = 'a'.repeat(4096);
  vi.mocked(api).mockResolvedValueOnce({ ...original, text: first });
  vi.mocked(api).mockResolvedValueOnce({
    ...original,
    source_version: 'REPLACEMENT',
    text: 'Different version',
  });
  const { container } = open('viewer');
  await screen.findByRole('heading', { name: original.title });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.identity_next }));
  expect(await screen.findByText(terms.labels.client_response_invalid)).toBeVisible();
  expect(vi.mocked(api).mock.calls[1][0]).toBe('/v2/materials/M/source?offset=4096&source_version=V');
  expect(container.querySelector('[data-source-viewer]')).toBeNull();
});

it('checks the new route, citing subject, session and workspace before displaying a passage', async () => {
  vi.mocked(api).mockResolvedValueOnce({
    ...original,
    source_version: 'old',
    text: 'Confirmed quote',
    total: 15,
  });
  let resolveOther!: (value: unknown) => void;
  vi.mocked(api).mockReturnValueOnce(
    new Promise((resolve) => {
      resolveOther = resolve;
    }),
  );
  vi.mocked(api).mockResolvedValueOnce({
    ...original,
    material_id: 'N',
    text: 'Allowed after refresh',
    total: 21,
  });
  vi.mocked(api).mockRejectedValue(new ApiError('No source in this workspace', 404));
  const { container } = open('viewer', '?anchor=one&source_version=old&citing_entity_id=R');
  expect(await screen.findByText('Confirmed quote')).toBeVisible();
  expect(vi.mocked(api).mock.calls[0][0]).toBe(
    '/v2/materials/M/source?offset=0&anchor_id=one&source_version=old&citing_entity_id=R',
  );
  fireEvent.click(screen.getByRole('link', { name: 'Other source' }));
  await waitFor(() =>
    expect(vi.mocked(api).mock.calls[1][0]).toBe(
      '/v2/materials/N/source?offset=0&anchor_id=A&source_version=V&citing_relation_id=REL',
    ),
  );
  expect(container.querySelector('[data-source-viewer]')).toBeNull();
  act(() => useSessionStore.setState((state) => ({ revision: state.revision + 1 })));
  expect(await screen.findByText('Allowed after refresh')).toBeVisible();
  await act(async () => resolveOther({ ...original, material_id: 'N', text: 'Expired response', total: 16 }));
  expect(screen.queryByText('Expired response')).toBeNull();
  act(() => {
    useScopeStore.getState().setScope('other');
    useSessionStore.setState(({ session }) => ({
      session: { ...session!, workspace: { ...session!.workspace, tenant_id: 'other' } },
    }));
  });
  expect(await screen.findByText('No source in this workspace')).toBeVisible();
  expect(container.querySelector('[data-source-viewer]')).toBeNull();
});
