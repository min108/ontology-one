import { beforeEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import {
  clearPreferences,
  loadPreferences,
  savePreferredViewMode,
  retryPreferenceSave,
  usePreferenceStore,
} from './preferences';
import { useUiStore } from './ui';
const request = vi.hoisted(() => vi.fn());
vi.mock('../api/client', () => ({ api: request }));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  clearPreferences();
  request.mockReset();
  useUiStore.getState().resetScopeUi('professional');
});
describe('server view preferences', () => {
  it('loads pro/simple and does not invent missing preferences', async () => {
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    await loadPreferences('u', 'a');
    expect(useUiStore.getState().viewMode).toBe('simple');
    clearPreferences();
    request.mockResolvedValue({ preferences: {} });
    useUiStore.getState().setViewMode('professional');
    await loadPreferences('u', 'a');
    expect(useUiStore.getState().viewMode).toBe('professional');
  });
  it('does not let a late GET overwrite a newer choice', async () => {
    const get = deferred<unknown>();
    request.mockImplementation((_path, init) =>
      init?.method === 'PUT' ? Promise.resolve({ preferences: { view_mode: 'pro' } }) : get.promise,
    );
    const loading = loadPreferences('u', 'a');
    savePreferredViewMode('professional');
    get.resolve({ preferences: { view_mode: 'simple' } });
    await loading;
    expect(useUiStore.getState().viewMode).toBe('professional');
  });
  it('keeps a local choice when retrying an earlier failed load', async () => {
    request.mockRejectedValueOnce(new Error('offline'));
    await loadPreferences('u', 'a');
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    savePreferredViewMode('professional');
    await waitFor(() => expect(usePreferenceStore.getState().saving).toBe(false));
    await loadPreferences('u', 'a');
    expect(useUiStore.getState().viewMode).toBe('professional');
  });
  it('serializes writes so the server ends on the last selected mode', async () => {
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    await loadPreferences('u', 'a');
    const first = deferred<unknown>();
    request.mockReturnValueOnce(first.promise).mockResolvedValue({ preferences: { view_mode: 'simple' } });
    savePreferredViewMode('professional');
    savePreferredViewMode('simple');
    expect(request.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    first.resolve({ preferences: { view_mode: 'pro' } });
    await waitFor(() => expect(usePreferenceStore.getState().saving).toBe(false));
    const writes = request.mock.calls.filter(([, init]) => init?.method === 'PUT');
    expect(writes.map(([, init]) => JSON.parse(init.body).value)).toEqual(['pro', 'simple']);
    expect(useUiStore.getState().viewMode).toBe('simple');
  });
  it('keeps failed choice unsaved and offers an explicit retry', async () => {
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    await loadPreferences('u', 'a');
    request.mockRejectedValueOnce(new Error('failed'));
    savePreferredViewMode('professional');
    await waitFor(() => expect(usePreferenceStore.getState().error).toBe('save'));
    expect(useUiStore.getState().viewMode).toBe('professional');
    request.mockResolvedValue({ preferences: { view_mode: 'pro' } });
    retryPreferenceSave();
    await waitFor(() => expect(usePreferenceStore.getState().error).toBe(''));
    expect(JSON.parse(request.mock.calls.at(-1)![1].body)).toEqual({ value: 'pro' });
  });
  it('aborts and ignores responses from a cleared identity', async () => {
    const old = deferred<unknown>();
    request.mockReturnValueOnce(old.promise);
    const loading = loadPreferences('old', 'a');
    const signal = request.mock.calls[0][1].signal;
    clearPreferences();
    request.mockResolvedValue({ preferences: { view_mode: 'pro' } });
    await loadPreferences('new', 'b');
    old.resolve({ preferences: { view_mode: 'simple' } });
    await loading;
    expect(signal.aborted).toBe(true);
    expect(useUiStore.getState().viewMode).toBe('professional');
  });
  it('continues with the newest queued choice when an earlier write fails', async () => {
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    await loadPreferences('u', 'a');
    const old = deferred<unknown>();
    request.mockReturnValueOnce(old.promise).mockResolvedValue({ preferences: { view_mode: 'simple' } });
    savePreferredViewMode('professional');
    savePreferredViewMode('simple');
    old.reject(new Error('older write failed'));
    await waitFor(() => expect(usePreferenceStore.getState().saving).toBe(false));
    expect(usePreferenceStore.getState().error).toBe('');
    expect(request.mock.calls.filter(([, init]) => init?.method === 'PUT').map(([, init]) => JSON.parse(init.body).value)).toEqual(['pro', 'simple']);
  });
  it('drops queued writes and ignores a late save after the identity is cleared', async () => {
    request.mockResolvedValue({ preferences: { view_mode: 'simple' } });
    await loadPreferences('old', 'a');
    const pending = deferred<unknown>();
    request.mockReturnValueOnce(pending.promise);
    savePreferredViewMode('professional');
    savePreferredViewMode('simple');
    const signal = request.mock.calls.at(-1)![1].signal;
    clearPreferences();
    request.mockResolvedValue({ preferences: { view_mode: 'pro' } });
    await loadPreferences('new', 'b');
    pending.resolve({ preferences: { view_mode: 'simple' } });
    await pending.promise;
    expect(signal.aborted).toBe(true);
    expect(request.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    expect(useUiStore.getState().viewMode).toBe('professional');
    expect(usePreferenceStore.getState().saving).toBe(false);
  });
  it('keeps load failures nonblocking and rejects invalid server values', async () => {
    request.mockRejectedValueOnce(new Error('offline'));
    await loadPreferences('u', 'a');
    expect(usePreferenceStore.getState().error).toBe('load');
    expect(useUiStore.getState().viewMode).toBe('professional');
    request.mockResolvedValue({ preferences: { view_mode: 'unexpected' } });
    await loadPreferences('u', 'a');
    expect(useUiStore.getState().viewMode).toBe('professional');
  });
});
