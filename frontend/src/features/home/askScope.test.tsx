import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import Applications from '@legacy/pages/Applications';
import { api, useApi } from '@legacy/api';
import { terms } from '../../i18n/zh-CN';
import { testSession } from '../build/testQuery';
vi.mock('@legacy/api', async (original) => ({
  ...(await original<typeof import('@legacy/api')>()),
  api: vi.fn(),
  request: vi.fn().mockResolvedValue(new Response()),
  useApi: vi.fn(),
}));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({ session: { workspace: { tenant_id: 'tenant-test' } } }),
}));
const options = {
  assets: [{ id: 'rule-1', title: 'Payment control', kind: 'rule' }],
  mappings: [],
  skills: [],
};
beforeEach(() => {
  testSession();
  vi.clearAllMocks();
  HTMLElement.prototype.scrollTo = vi.fn();
  vi.mocked(useApi).mockImplementation(
    (path: string) =>
      ({
        data:
          path === '/agent/options'
            ? options
            : path === '/agent/capabilities'
              ? { configured: true, available: true }
              : { items: [] },
        loading: false,
        error: null,
        reload: vi.fn(),
      }) as never,
  );
  vi.mocked(api).mockImplementation(async (path: string) =>
    path.endsWith('/turns')
      ? { run_id: 'run-1', status: 'completed' }
      : ({
          id: 'session-1',
          title: 'Question',
          messages: [],
          turns: [{ id: 'run-1', status: 'completed' }],
          last_scope: { entity_ids: ['rule-1'] },
          scope_mode: 'selected',
        } as never),
  );
});
it('preselects only an authorized published option and submits that exact reference scope', async () => {
  render(
    <MemoryRouter initialEntries={['/applications?element=rule-1']}>
      <Applications />
    </MemoryRouter>,
  );
  expect(await screen.findByRole('checkbox', { name: /Payment control/ })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: /自动按需选用/ })).not.toBeChecked();
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'What should be checked before payment?' },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.catalog_6_0 }));
  await waitFor(() => expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith('/turns'))).toBe(true));
  const payload = JSON.parse(
    String(vi.mocked(api).mock.calls.find(([path]) => path.endsWith('/turns'))?.[1]?.body),
  );
  expect(payload.entity_ids).toEqual(['rule-1']);
  expect(payload.scope_mode).toBe('selected');
});
it('does not preselect an unknown or unauthorized address parameter', async () => {
  render(
    <MemoryRouter initialEntries={['/applications?element=private-unknown']}>
      <Applications />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: /^参考范围/ }));
  expect(screen.getByRole('checkbox', { name: /Payment control/ })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: /自动按需选用/ })).toBeChecked();
});
