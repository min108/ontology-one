import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiBlob, invalidateClientRequests } from './client';
import { setBusinessBlocked, setWorkspaceId } from '@legacy/api';
beforeEach(() => {
  setBusinessBlocked(false);
  setWorkspaceId('dept-01');
});
afterEach(() => {
  invalidateClientRequests();
  vi.unstubAllGlobals();
});
it('binds binary requests to the current workspace and credentials', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response('ZIP', { headers: { 'Content-Type': 'application/zip' } }));
  vi.stubGlobal('fetch', fetcher);
  expect(await (await apiBlob('/v2/system/backup?workspace_id=dept-01')).text()).toBe('ZIP');
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe('/api/v2/system/backup?workspace_id=dept-01');
  expect(init.headers.get('X-Workspace-Id')).toBe('dept-01');
  expect(init.credentials).toBe('include');
});
it('rejects a binary body that completes after a workspace change', async () => {
  let done!: (body: Blob) => void;
  const body = new Promise<Blob>((resolve) => {
    done = resolve;
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, blob: () => body }));
  const response = apiBlob('/v2/system/backup');
  await Promise.resolve();
  invalidateClientRequests();
  done(new Blob(['old workspace']));
  await expect(response).rejects.toMatchObject({ name: 'AbortError' });
});
