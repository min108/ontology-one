import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, apiBlob } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import ReportsPage from './ReportsPage';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
  apiBlob: vi.fn(),
}));
const report = {
  id: 'ART-private',
  title: 'Payment report',
  source_kind: 'mock',
  current_version: 1,
  created_at: '2026-11-17T00:30:00Z',
  versions: [{ version: 1, created_at: '2026-11-17T00:30:00Z', change_note: 'Generated' }],
  spec: {
    source_label: 'Simulated model result',
    blocks: [
      {
        type: 'paragraph',
        text: '[Payment rule](/assets/rule_internal) requires payment within 30 days.',
        citations: ['c1'],
      },
      { type: 'table', columns: ['Item', 'Deadline'], rows: [['Payment', '30 days']], citations: ['c1'] },
    ],
    citations: {
      c1: {
        title: 'Payment rule',
        source_title: 'Payment regulation',
        entity_id: 'rule_internal',
        quote: 'Payment is due within 30 days.',
        version: 1,
        path: '/materials/original',
        locator: 'Article 1',
        anchor_id: 'anchor_hidden',
      },
    },
    revision: {
      count: 1,
      before: 'rule_internal requires 99 days.',
      after: 'rule_internal requires 30 days.',
    },
  },
};
function open(path = '/apply/artifacts/ART-private') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/apply/artifacts" element={<ReportsPage />} />
          <Route path="/apply/artifacts/:id" element={<ReportsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockReset();
  vi.mocked(apiBlob).mockReset();
});

it('opens source superscripts in a drawer, closes it and preserves the report', async () => {
  vi.mocked(api).mockResolvedValue({ data: report });
  const view = open();
  expect(await screen.findByRole('heading', { name: report.title })).toBeInTheDocument();
  const citation = screen.getAllByRole('button', { name: terms.actions.r3_report_source + ' 1' })[0];
  expect(citation.closest('sup')).not.toBeNull();
  fireEvent.click(citation);
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByText('Payment regulation')).toBeInTheDocument();
  expect(within(drawer).getByText('Payment is due within 30 days.').closest('mark')).not.toBeNull();
  expect(within(drawer).getByRole('link', { name: terms.actions.r3_report_open_material })).toHaveAttribute(
    'href',
    '/materials/original',
  );
  fireEvent.click(within(drawer).getByRole('button', { name: terms.actions.r3_ui_close }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('heading', { name: report.title })).toBeInTheDocument();
  expect(view.container.textContent).not.toContain('anchor_hidden');
  expect(view.container.textContent).not.toContain('rule_internal');
});

it('keeps server-owned before and after answers visibly distinguished', async () => {
  vi.mocked(api).mockResolvedValue({ data: report });
  open();
  const compare = await screen.findByText(terms.actions.r3_report_compare);
  fireEvent.click(compare);
  expect(screen.getByRole('heading', { name: terms.labels.r3_report_before })).toBeInTheDocument();
  expect(screen.getByText('Payment rule requires 99 days.')).toBeInTheDocument();
  expect(screen.getByText('Payment rule requires 30 days.')).toBeInTheDocument();
  expect(screen.getAllByText(terms.labels.r3_report_revised).length).toBeGreaterThan(0);
});

it('downloads the chosen saved version and regenerates with a strict version request', async () => {
  const regenerated = {
    ...report,
    current_version: 2,
    versions: [{ ...report.versions[0], version: 2 }, ...report.versions],
  };
  vi.mocked(api)
    .mockResolvedValueOnce({ data: report })
    .mockResolvedValueOnce({ data: regenerated })
    .mockResolvedValueOnce({ data: regenerated });
  vi.mocked(apiBlob).mockResolvedValue(new Blob(['document'], { type: 'application/octet-stream' }));
  const create = vi.fn().mockReturnValue('blob:report'),
    revoke = vi.fn();
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    open();
    const heading = await screen.findByRole('heading', { name: report.title });
    const header = heading.closest('header')!;
    fireEvent.click(within(header).getByRole('button', { name: terms.actions.r2_report_download_word }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(apiBlob).toHaveBeenCalledWith(
      '/v2/artifacts/ART-private/versions/1/files/docx',
      expect.anything(),
    );
    fireEvent.click(screen.getByText(terms.labels.r2_report_versions));
    fireEvent.click(screen.getByRole('button', { name: terms.actions.r2_report_again }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/v2/artifacts/ART-private:render',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ artifact_id: 'ART-private', expected_version: 1 }),
        }),
      ),
    );
    expect(await screen.findByText(/^版本 2/)).toBeInTheDocument();
  } finally {
    click.mockRestore();
    vi.unstubAllGlobals();
  }
});

it('lists report titles and localized dates without private identifiers', async () => {
  vi.mocked(api).mockResolvedValue({ data: { items: [report] } });
  const { container } = open('/apply/artifacts');
  expect(await screen.findByRole('link', { name: report.title })).toHaveAttribute(
    'href',
    '/apply/artifacts/ART-private',
  );
  expect(screen.getByText('2026-11-17 08:30')).toBeInTheDocument();
  expect(container.textContent).not.toContain('ART-private');
  expect(container.querySelectorAll('th')).toHaveLength(3);
});
