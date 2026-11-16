import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, invalidateClientRequests, invokeCapability } from './client';
import { setBusinessBlocked, setCsrfToken, setWorkspaceId } from '@legacy/api';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const replace = vi.fn();
const originalWindow = window;
beforeEach(() => {
  vi.stubGlobal(
    'window',
    new Proxy(originalWindow, {
      get(target, key) {
        if (key === 'location') return { replace };
        const value = Reflect.get(target, key);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    }),
  );
  setBusinessBlocked(false);
  setCsrfToken('csrf');
  setWorkspaceId('tenant-a');
  replace.mockReset();
});
afterEach(() => {
  invalidateClientRequests();
  vi.unstubAllGlobals();
});

describe('workspace client', () => {
  it('sends credentials, tenant and CSRF to capability gateway', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"ok":true}'));
    vi.stubGlobal('fetch', fetcher);
    expect(await invokeCapability('session.whoami', {})).toEqual({ ok: true });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/api/v2/capabilities/session.whoami:invoke');
    expect(init.credentials).toBe('include');
    expect(init.headers.get('X-Workspace-Id')).toBe('tenant-a');
    expect(init.headers.get('X-CSRF-Token')).toBe('csrf');
  });
  it('rejects a pre-aborted caller without sending anything', async () => {
    const controller = new AbortController();
    controller.abort();
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(api('/v2/test', { signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('discards stale headers before parsing body or dispatching 401', async () => {
    const response = deferred<Response>();
    const json = vi.fn();
    const unauthorized = vi.fn();
    originalWindow.addEventListener('ontology:unauthorized', unauthorized);
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise));
    const request = api('/v2/test');
    invalidateClientRequests();
    response.resolve({ status: 401, ok: false, json } as unknown as Response);
    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    expect(json).not.toHaveBeenCalled();
    expect(unauthorized).not.toHaveBeenCalled();
    originalWindow.removeEventListener('ontology:unauthorized', unauthorized);
  });
  it.each([200, 401, 409])(
    'keeps epoch protection through asynchronous body at status %s',
    async (status) => {
      const body = deferred<unknown>();
      const unauthorized = vi.fn();
      originalWindow.addEventListener('ontology:unauthorized', unauthorized);
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({ status, ok: status === 200, json: () => body.promise }),
      );
      const request = api('/v2/test');
      await Promise.resolve();
      invalidateClientRequests();
      body.resolve({ error: { code: 'WORKSPACE_CHANGED' } });
      await expect(request).rejects.toMatchObject({ name: 'AbortError' });
      expect(replace).not.toHaveBeenCalled();
      expect(unauthorized).not.toHaveBeenCalled();
      originalWindow.removeEventListener('ontology:unauthorized', unauthorized);
    },
  );
  it.each([
    { code: 'workspace_changed' },
    { detail: { code: 'workspace_changed' } },
    { error: { code: 'WORKSPACE_CHANGED' } },
  ])('reloads on old and v2 workspace conflict %o', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 409 })));
    await expect(api('/v2/test')).rejects.toMatchObject({ status: 409 });
    expect(replace).toHaveBeenCalledWith('/');
    await expect(api('/v2/test')).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('never forwards identity headers to an external or ambiguous path', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    for (const path of [
      'https://cross-origin.test/x',
      '//cross-origin.test',
      '/\\cross-origin.test',
      '/api/../outside',
    ]) {
      await expect(api(path)).rejects.toThrow();
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
});

it('turns a stale body parsing failure into cancellation', async () => {
  let reject!: (error: Error) => void;
  const body = new Promise<unknown>((_resolve, fail) => {
    reject = fail;
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true, json: () => body }));
  const request = api('/v2/test');
  await Promise.resolve();
  invalidateClientRequests();
  reject(new Error('bad json'));
  await expect(request).rejects.toMatchObject({ name: 'AbortError' });
});
