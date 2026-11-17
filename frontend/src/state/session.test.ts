import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AuthSession } from '../api/session.types';

const originalWindow = window;
const replace = vi.fn();
const auth: AuthSession = {
  authenticated: true,
  user: {
    id: 'u1',
    username: 'one',
    display_name: 'One',
    home_tenant_id: 'a',
    role: 'admin',
    is_superadmin: false,
    must_change_password: false,
  },
  workspace: { tenant_id: 'a', name: 'A', category: 'local', readonly: false },
  workspaces: [],
  roles: [],
  permissions: { manage_users: true, manage_grants: false, can_edit: true, can_review: false },
  csrf_token: 'csrf-a',
  must_change_password: false,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function setup() {
  const session = await import('./session');
  const guard = await import('./workspace-guard');
  const { queryClient, workspaceQueryKey } = await import('../api/query');
  const { useScopeStore } = await import('./scope');
  const { useUiStore } = await import('./ui');
  const legacy = await import('@legacy/api');
  return { ...session, ...guard, queryClient, workspaceQueryKey, useScopeStore, useUiStore, legacy };
}
beforeEach(() => {
  vi.resetModules();
  replace.mockReset();
  sessionStorage.clear();
  localStorage.clear();
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
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementation(
        async (path: string) =>
          new Response(
            JSON.stringify(
              path.includes('whoami')
                ? { user_id: 'u1', tenant_id: 'a', permissions: ['federation.read'] }
                : auth,
            ),
          ),
      ),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

it('maps whoami permissions without changing legacy boolean permissions', async () => {
  const { useSessionStore, useScopeStore } = await setup();
  await useSessionStore.getState().reload();
  expect(useSessionStore.getState().permission_keys).toEqual(['federation.read']);
  expect(useSessionStore.getState().session?.permissions).toEqual(auth.permissions);
  expect(useScopeStore.getState()).toMatchObject({ tenantId: 'a', scopeKey: 'local' });
});
it('retains a saved professional preference when a source-only identity resolves later', async () => {
  const identity = deferred<Response>();
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (String(path).includes('whoami')) return identity.promise;
    if (String(path).endsWith('/v2/prefs'))
      return new Response(JSON.stringify({ preferences: { view_mode: 'pro' } }));
    return new Response(JSON.stringify(auth));
  });
  const state = await setup();
  const loading = state.useSessionStore.getState().reload();
  await vi.waitFor(() => expect(state.useSessionStore.getState().session).not.toBeNull());
  const { loadPreferences } = await import('./preferences');
  await loadPreferences('u1', 'a');
  expect(state.useUiStore.getState().viewMode).toBe('professional');
  identity.resolve(
    new Response(JSON.stringify({ user_id: 'u1', tenant_id: 'a', permissions: ['source.submit'] })),
  );
  await loading;
  expect(state.useSessionStore.getState().permission_keys).toEqual(['source.submit']);
  expect(state.useUiStore.getState().viewMode).toBe('professional');
});
it('rejects permissions for a mismatched identity and defaults denied', async () => {
  const { useSessionStore } = await setup();
  vi.mocked(fetch).mockImplementation(
    async (path) =>
      new Response(
        JSON.stringify(
          String(path).includes('whoami')
            ? { user_id: 'other', tenant_id: 'a', permissions: ['federation.read'] }
            : auth,
        ),
      ),
  );
  await useSessionStore.getState().reload();
  expect(useSessionStore.getState().permission_keys).toEqual([]);
  expect(useSessionStore.getState().permissionError).toBeTruthy();
});
it('cancel leaves caches, drafts, upload, tokens, scope and session untouched', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  s.queryClient.setQueryData(s.workspaceQueryKey('a', 'local', 'items'), ['old']);
  sessionStorage.setItem('ontology-one-draft-x', 'draft');
  s.beginWorkspaceUpload();
  const confirmation = deferred<boolean>();
  const confirm = vi.fn(() => confirmation.promise);
  s.setWorkspaceConfirmation(confirm);
  const switching = s.useSessionStore.getState().switchWorkspace('b');
  expect(s.useSessionStore.getState().switching).toBe(false);
  await s.useSessionStore.getState().switchWorkspace('c');
  expect(confirm).toHaveBeenCalledTimes(1);
  confirmation.resolve(false);
  await switching;
  expect(s.queryClient.getQueryData(s.workspaceQueryKey('a', 'local', 'items'))).toEqual(['old']);
  expect(sessionStorage.getItem('ontology-one-draft-x')).toBe('draft');
  expect(s.readWorkspaceGuards().uploadCount).toBe(1);
  expect(s.legacy.csrfHeaders()).toEqual({ 'X-CSRF-Token': 'csrf-a' });
  expect(s.legacy.getWorkspaceId()).toBe('a');
  expect(s.useScopeStore.getState().tenantId).toBe('a');
  expect(replace).not.toHaveBeenCalled();
  expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/auth/workspace'))).toBe(false);
});
it('same id and unregistered guarded switch have no effects', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  s.registerWorkspaceGuard(() => true);
  await s.useSessionStore.getState().switchWorkspace('a');
  await s.useSessionStore.getState().switchWorkspace('b');
  expect(replace).not.toHaveBeenCalled();
  expect(s.useScopeStore.getState().tenantId).toBe('a');
  expect(s.isWorkspaceConfirmationOpen()).toBe(false);
});
it.each([true, false])(
  'confirmed switch clears state and keeps business blocked until unload, success=%s',
  async (success) => {
    const s = await setup();
    await s.useSessionStore.getState().reload();
    s.queryClient.setQueryData(s.workspaceQueryKey('a', 'local', 'items'), ['old']);
    s.useUiStore.getState().setCollapsed(true);
    sessionStorage.setItem('ontology-one-draft-x', 'old');
    s.setWorkspaceConfirmation(async () => true);
    vi.mocked(fetch).mockImplementation(async () => {
      if (!success) throw new Error('offline');
      return new Response(JSON.stringify(auth));
    });
    await s.useSessionStore.getState().switchWorkspace('b');
    expect(s.queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(s.useScopeStore.getState().tenantId).toBe('');
    expect(s.useUiStore.getState().collapsed).toBe(false);
    expect(sessionStorage.getItem('ontology-one-draft-x')).toBeNull();
    expect(replace).toHaveBeenCalledExactlyOnceWith('/');
    expect(s.isWorkspaceLeaving()).toBe(true);
    s.useSessionStore.getState().acceptSession(auth);
    await s.useSessionStore.getState().reload();
    expect(s.useScopeStore.getState().tenantId).toBe('');
    expect(() => s.legacy.assertBusinessReady()).toThrow();
  },
);
it('late initial reload cannot revive session after confirmed switch', async () => {
  const s = await setup();
  const response = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(response.promise);
  const reload = s.useSessionStore.getState().reload();
  await s.useSessionStore.getState().switchWorkspace('b');
  response.resolve(new Response(JSON.stringify(auth)));
  await reload;
  expect(s.useScopeStore.getState().tenantId).toBe('');
  expect(s.useSessionStore.getState().session).toBeNull();
  expect(() => s.legacy.assertBusinessReady()).toThrow();
});
it('a newer reload wins over delayed older initialization', async () => {
  const s = await setup();
  const response = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(response.promise);
  const older = s.useSessionStore.getState().reload();
  await s.useSessionStore.getState().reload();
  response.resolve(
    new Response(JSON.stringify({ ...auth, workspace: { ...auth.workspace, tenant_id: 'old' } })),
  );
  await older;
  expect(s.useScopeStore.getState().tenantId).toBe('a');
});
it('tenant and scope cache key parts are mandatory and distinct', async () => {
  const s = await setup();
  expect(s.workspaceQueryKey('a', 'local', 'items')).not.toEqual(s.workspaceQueryKey('b', 'local', 'items'));
  expect(s.workspaceQueryKey('a', 'local', 'items')).not.toEqual(s.workspaceQueryKey('a', 'group', 'items'));
  expect(() => s.workspaceQueryKey('', 'local', 'items')).toThrow();
});

it('late login cannot reopen scope or navigate after leaving', async () => {
  const s = await setup();
  const response = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(response.promise);
  const login = s.useSessionStore.getState().login('one', 'password');
  const failure = expect(login).rejects.toMatchObject({ name: 'AbortError' });
  await s.useSessionStore.getState().switchWorkspace('b');
  response.resolve(new Response(JSON.stringify(auth)));
  await failure;
  expect(s.useScopeStore.getState().tenantId).toBe('');
  expect(() => s.legacy.assertBusinessReady()).toThrow();
  expect(replace).toHaveBeenCalledExactlyOnceWith('/');
});
it('storage cleanup failure still reloads, stays blocked and does not submit switch', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  sessionStorage.setItem('ontology-one-test', 'old');
  const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
    throw new DOMException('Denied', 'SecurityError');
  });
  try {
    await s.useSessionStore.getState().switchWorkspace('b');
  } finally {
    remove.mockRestore();
  }
  expect(replace).toHaveBeenCalledExactlyOnceWith('/');
  expect(() => s.legacy.assertBusinessReady()).toThrow();
  expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/auth/workspace'))).toBe(false);
});
it('StrictMode setup-cleanup-setup discards the first delayed initialization and removes listeners', async () => {
  const s = await setup();
  const first = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(first.promise);
  const disposeFirst = s.initializeSession();
  disposeFirst();
  const disposeSecond = s.initializeSession();
  await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
  first.resolve(
    new Response(JSON.stringify({ ...auth, workspace: { ...auth.workspace, tenant_id: 'stale' } })),
  );
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  expect(s.useScopeStore.getState().tenantId).toBe('a');
  const before = s.useSessionStore.getState().revision;
  disposeSecond();
  window.dispatchEvent(new Event('ontology:unauthorized'));
  expect(s.useSessionStore.getState().revision).toBe(before);
});
it('current unauthorized response clears identity, caches and permissions', async () => {
  const s = await setup();
  const dispose = s.initializeSession();
  await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
  vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 401 }));
  await s.useSessionStore.getState().reload();
  dispose();
  expect(s.useSessionStore.getState().session).toBeNull();
  expect(s.useSessionStore.getState().permission_keys).toEqual([]);
  expect(s.useScopeStore.getState().tenantId).toBe('');
  expect(s.legacy.csrfHeaders()).toEqual({});
});
it('converts preference modes without inventing an endpoint and defaults source submitters to simple', async () => {
  const { defaultViewMode, fromPreferenceViewMode, toPreferenceViewMode } = await import('./ui');
  for (const role of ['admin', 'modeler', 'reviewer', 'viewer', 'analyst'])
    expect(defaultViewMode(role)).toBe('simple');
  expect(defaultViewMode('viewer')).toBe('simple');
  expect(defaultViewMode('modeler', ['source.submit'])).toBe('simple');
  expect(fromPreferenceViewMode('pro')).toBe('professional');
  expect(toPreferenceViewMode('professional')).toBe('pro');
});

it('cancel does not invalidate an in-flight client request or emit scope-cleared', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  const { api } = await import('../api/client');
  const response = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(response.promise);
  const pending = api('/v2/items');
  s.registerWorkspaceGuard(() => true);
  s.setWorkspaceConfirmation(async () => false);
  const cleared = vi.fn();
  window.addEventListener('ontology:scope-cleared', cleared);
  try {
    await s.useSessionStore.getState().switchWorkspace('b');
    response.resolve(new Response('{"items":[1]}'));
    await expect(pending).resolves.toEqual({ items: [1] });
    expect(cleared).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener('ontology:scope-cleared', cleared);
  }
});
it('public acceptSession supersedes initialization without leaving loading stuck', async () => {
  const s = await setup();
  const response = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(response.promise);
  const reload = s.useSessionStore.getState().reload();
  s.useSessionStore.getState().acceptSession(auth);
  await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
  response.resolve(
    new Response(JSON.stringify({ ...auth, workspace: { ...auth.workspace, tenant_id: 'old' } })),
  );
  await reload;
  expect(s.useScopeStore.getState().tenantId).toBe('a');
});
it('confirmed workspace POST retains old CSRF and auth-route header behavior', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  await s.useSessionStore.getState().switchWorkspace('b');
  const call = vi.mocked(fetch).mock.calls.find(([path]) => String(path).includes('/auth/workspace'));
  expect(call).toBeDefined();
  const headers = new Headers(call?.[1]?.headers);
  expect(headers.get('X-CSRF-Token')).toBe('csrf-a');
  expect(headers.has('X-Workspace-Id')).toBe(false);
  expect(call?.[1]?.body).toBe(JSON.stringify({ tenant_id: 'b' }));
});

it('late sessions cannot undo a block set by legacy workspace conflict handling', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  const before = s.useSessionStore.getState().revision;
  s.legacy.setBusinessBlocked(true);
  s.useSessionStore.getState().acceptSession({ ...auth, csrf_token: 'late' });
  await Promise.resolve();
  expect(() => s.legacy.assertBusinessReady()).toThrow();
  expect(s.useSessionStore.getState().revision).toBe(before);
  expect(s.legacy.csrfHeaders()).toEqual({ 'X-CSRF-Token': 'csrf-a' });
});

it('an externally changed identity forces a hard reload instead of mixing mounted legacy state', async () => {
  const s = await setup();
  await s.useSessionStore.getState().reload();
  s.queryClient.setQueryData(s.workspaceQueryKey('a', 'local', 'items'), ['old']);
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify({ ...auth, workspace: { ...auth.workspace, tenant_id: 'b' } })),
  );
  await s.useSessionStore.getState().reload();
  expect(replace).toHaveBeenCalledExactlyOnceWith('/');
  expect(s.useScopeStore.getState().tenantId).toBe('');
  expect(s.queryClient.getQueryCache().getAll()).toHaveLength(0);
  expect(() => s.legacy.assertBusinessReady()).toThrow();
});

it.each([401, 409])('discards superseded session status %s before global status effects', async (status) => {
  const s = await setup();
  const dispose = s.initializeSession();
  try {
    await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
    const response = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(response.promise);
    const older = s.useSessionStore.getState().reload();
    await s.useSessionStore.getState().reload();
    response.resolve(new Response(JSON.stringify({ error: { code: 'WORKSPACE_CHANGED' } }), { status }));
    await older;
    expect(s.useSessionStore.getState().session).toEqual(auth);
    expect(s.useScopeStore.getState().tenantId).toBe('a');
    expect(s.useSessionStore.getState().permission_keys).toEqual(['federation.read']);
    expect(replace).not.toHaveBeenCalled();
    expect(() => s.legacy.assertBusinessReady()).not.toThrow();
  } finally {
    dispose();
  }
});

const sessionOperations = ['reload', 'acceptSession', 'login', 'logout'] as const;
type SessionOperation = (typeof sessionOperations)[number];
async function runSessionOperation(
  state: import('./session').SessionState,
  operation: SessionOperation,
): Promise<void> {
  if (operation === 'acceptSession') state.acceptSession(auth);
  else if (operation === 'login') await state.login('one', 'password');
  else await state[operation]();
}
const competitions = sessionOperations.flatMap((older) =>
  sessionOperations.flatMap((newer) => [401, 409].map((status) => ({ older, newer, status }))),
);
it.each(competitions)(
  'supersedes $older with $newer before old status $status effects',
  async ({ older, newer, status }) => {
    const s = await setup();
    const dispose = s.initializeSession();
    const unauthorized = vi.fn();
    window.addEventListener('ontology:unauthorized', unauthorized);
    try {
      await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
      const response = deferred<Response>();
      vi.mocked(fetch).mockReturnValueOnce(response.promise);
      const prior = runSessionOperation(s.useSessionStore.getState(), older).catch((error: unknown) => error);
      const oldSignal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal;
      const latest = runSessionOperation(s.useSessionStore.getState(), newer);
      expect(oldSignal?.aborted).toBe(true);
      await latest;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      response.resolve(new Response(JSON.stringify({ error: { code: 'WORKSPACE_CHANGED' } }), { status }));
      await prior;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      expect(unauthorized).not.toHaveBeenCalled();
      expect(replace).not.toHaveBeenCalled();
      expect(s.useSessionStore.getState().session).toEqual(newer === 'logout' ? null : auth);
      expect(s.useScopeStore.getState().tenantId).toBe(newer === 'logout' ? '' : 'a');
      expect(() => s.legacy.assertBusinessReady()).not.toThrow();
    } finally {
      window.removeEventListener('ontology:unauthorized', unauthorized);
      dispose();
    }
  },
);
it.each(sessionOperations.flatMap((operation) => [401, 409].map((status) => ({ operation, status }))))(
  'preserves current $operation status $status handling',
  async ({ operation, status }) => {
    const s = await setup();
    const dispose = s.initializeSession();
    try {
      await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: 'WORKSPACE_CHANGED' } }), { status }),
      );
      await runSessionOperation(s.useSessionStore.getState(), operation).catch(() => undefined);
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      if (status === 401) {
        expect(s.useSessionStore.getState().session).toBeNull();
        expect(replace).not.toHaveBeenCalled();
      } else {
        expect(replace).toHaveBeenCalledExactlyOnceWith('/');
        expect(() => s.legacy.assertBusinessReady()).toThrow();
      }
    } finally {
      dispose();
    }
  },
);
it.each(['reload', 'acceptSession'] as const)(
  '%s supersedes only session work, keeping unrelated business requests alive',
  async (operation) => {
    const s = await setup();
    await s.useSessionStore.getState().reload();
    const { api } = await import('../api/client');
    const response = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(response.promise);
    const business = api('/v2/business');
    const signal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal;
    await runSessionOperation(s.useSessionStore.getState(), operation);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(signal?.aborted).toBe(false);
    response.resolve(new Response('{"ok":true}'));
    await expect(business).resolves.toEqual({ ok: true });
  },
);
it.each([401, 409])('cancels old whoami while status %s JSON is still being parsed', async (status) => {
  const s = await setup();
  const dispose = s.initializeSession();
  try {
    await vi.waitFor(() => expect(s.useSessionStore.getState().loading).toBe(false));
    const body = deferred<unknown>();
    vi.mocked(fetch).mockResolvedValueOnce({ status, ok: false, json: () => body.promise } as Response);
    s.useSessionStore.getState().acceptSession(auth);
    await Promise.resolve();
    await s.useSessionStore.getState().reload();
    body.resolve({ error: { code: 'WORKSPACE_CHANGED' } });
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(s.useSessionStore.getState().session).toEqual(auth);
    expect(replace).not.toHaveBeenCalled();
  } finally {
    dispose();
  }
});
