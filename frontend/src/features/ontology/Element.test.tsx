import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import ElementPage, { type ElementDetail } from './ElementPage';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));

function detail(): ElementDetail {
  return {
    entity: {
      id: 'rule_private_id',
      kind: 'rule',
      title: '付款管理要求',
      status: 'published',
      version: 3,
      domain: '采购管理',
      payload: {
        scope: ['集团总部', { name: '境内子公司', value: { description: '纳入统一付款管理' } }],
        conditions: [
          {
            title: '付款前',
            content: [{ text: '核验合同' }, { description: '确认验收' }],
            candidate_id: 'nested-secret',
          },
        ],
        constraints: {
          unit: '万元',
          definition: { content: '按含税金额核算', extraction_mode: 'nested-mode' },
          unknown_field: 'unknown-secret',
        },
        obligation: { content: '<script>literal business text</script>' },
        candidate_id: 'candidate-secret',
        source_chunk_ids: ['chunk-secret'],
        extraction_mode: 'mode-secret',
        r2_review: { submitter: 'review-secret' },
        future_internal_field: 'future-secret',
      },
      activities: [
        { id: 'a', action: 'publish', detail: '已完成独立审核', created_at: '2026-11-16T18:05:10Z' },
        { id: 'b', action: '提交审核', detail: '核对原文', created_at: '2026-11-17T02:06:00+08:00' },
        {
          id: 'c',
          action: 'unknown_internal_action',
          detail: 'internal-action-secret',
          created_at: 'not-a-date',
        },
      ],
    },
    card: {
      one_liner: '付款前核验合同与验收记录。',
      points: ['核验合同'],
      relations: [],
      usage: '用于付款审核。',
    },
    versions: [
      { id: 'v3', version: 3, created_at: '2026-11-16T18:05:10Z', reason: '独立审核通过' },
      { id: 'v2', version: 2, created_at: '2026-11-16 18:04:00', change_note: '补齐适用范围' },
      { id: 'v1', version: 1, created_at: 'invalid-secret', reason: '创建草稿' },
    ],
    can_edit: false,
  };
}
async function openElement(value = detail()) {
  vi.mocked(api).mockResolvedValue({ data: value });
  const rendered = render(
    <MemoryRouter initialEntries={['/ontology/elements/rule_private_id']}>
      <Routes>
        <Route path="/ontology/elements/:id" element={<ElementPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole('heading', { name: '付款管理要求' });
  return rendered;
}
beforeEach(() => {
  vi.clearAllMocks();
  useUiStore.setState({ viewMode: 'simple' });
});

it('shows readable nested business information with vocabulary labels and excludes all internal fields', async () => {
  const { container } = await openElement();
  const structure = screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure });
  for (const text of [
    '适用范围',
    '条件',
    '要求',
    '取值口径',
    '集团总部',
    '境内子公司',
    '纳入统一付款管理',
    '付款前',
    '核验合同',
    '确认验收',
    '单位：万元',
    '按含税金额核算',
  ]) {
    expect(structure).toHaveTextContent(text);
  }
  expect(structure).toHaveTextContent('<script>literal business text</script>');
  expect(container.querySelector('script')).toBeNull();
  for (const text of [
    'candidate_id',
    'source_chunk_ids',
    'extraction_mode',
    'r2_review',
    'future_internal_field',
    'secret',
    'nested-mode',
    '{',
    '[',
  ]) {
    expect(structure).not.toHaveTextContent(text);
  }
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
  act(() => useUiStore.getState().setViewMode('professional'));
  expect(screen.getByText(/rule_private_id/)).toBeInTheDocument();
  act(() => useUiStore.getState().setViewMode('simple'));
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
});

it('shows the identifier only in professional view while keeping internal payload fields hidden', async () => {
  useUiStore.setState({ viewMode: 'professional' });
  await openElement();
  expect(screen.getByText(/rule_private_id/)).toBeInTheDocument();
  expect(screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure })).not.toHaveTextContent(
    'secret',
  );
});

it('formats versions and activity in Beijing time and translates action identifiers', async () => {
  await openElement();
  fireEvent.click(screen.getByRole('tab', { name: terms.labels.r2_card_versions }));
  const versions = screen.getByRole('tabpanel', { name: terms.labels.r2_card_versions });
  expect(versions).toHaveTextContent('v3 · 2026-11-17 02:05 · 独立审核通过');
  expect(versions).toHaveTextContent('v2 · 2026-11-17 02:04 · 补齐适用范围');
  expect(versions).not.toHaveTextContent('invalid-secret');
  fireEvent.click(screen.getByRole('tab', { name: terms.labels.r2_card_activity }));
  const activity = screen.getByRole('tabpanel', { name: terms.labels.r2_card_activity });
  expect(activity).toHaveTextContent('发布 · 已完成独立审核 · 2026-11-17 02:05');
  expect(activity).toHaveTextContent('提交审核 · 核对原文 · 2026-11-17 02:06');
  expect(activity).not.toHaveTextContent('publish');
  expect(activity).not.toHaveTextContent('unknown_internal_action');
  expect(activity).not.toHaveTextContent('internal-action-secret');
});

it('shows an empty state when payload contains only metadata or unsupported nested data', async () => {
  const value = detail();
  value.entity.payload = {
    candidate_id: 'secret',
    conditions: { unknown_internal: ['secret'] },
    content: null,
  };
  await openElement(value);
  expect(
    within(screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure })).getByText(
      terms.labels.r2_card_empty,
    ),
  ).toBeInTheDocument();
});
