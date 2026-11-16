import {
  assertBusinessReady,
  csrfHeaders,
  workspaceHeaders,
  notifyUnauthorized,
  setBusinessBlocked,
  invalidateRequests,
} from '@legacy/api';
import { terms } from '../i18n/zh-CN';
import { setWorkspaceTransition } from '../state/workspace-guard';

let epoch = 0;
const pending = new Set<AbortController>();
const aborted = () => new DOMException(terms.labels.client_request_cancelled, 'AbortError');
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: unknown = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export function invalidateClientRequests(): void {
  epoch++;
  for (const controller of pending) controller.abort();
  pending.clear();
}
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}
/** Same-origin JSON requests, protected until the response body has finished. */
export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  if (
    !path.startsWith('/') ||
    path.startsWith('//') ||
    path.includes('\\') ||
    Array.from(path).some((character) => character.charCodeAt(0) <= 32) ||
    path.split(/[/?#]/).some((part) => decodeURIComponent(part) === '..')
  )
    throw new TypeError('Expected a same-origin API path');
  const url = path.startsWith('/api/') ? path : '/api' + path;
  const authRequest = url.startsWith('/api/auth/');
  if (!authRequest) assertBusinessReady();
  const generation = epoch;
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  const check = () => {
    if (controller.signal.aborted || generation !== epoch) throw aborted();
  };
  pending.add(controller);
  try {
    check();
    const headers = new Headers(init.headers);
    if (!authRequest)
      for (const [key, value] of Object.entries(workspaceHeaders())) if (value) headers.set(key, value);
    if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase()))
      for (const [key, value] of Object.entries(csrfHeaders())) if (value) headers.set(key, value);
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type'))
      headers.set('Content-Type', 'application/json');
    const response = await fetch(url, {
      ...init,
      headers,
      credentials: 'include',
      signal: controller.signal,
    });
    check();
    let body: unknown = null;
    let parseError: unknown;
    if (response.status !== 204) {
      try {
        body = await response.json();
      } catch (error) {
        parseError = error;
      }
    }
    check();
    if (response.status === 401) {
      notifyUnauthorized();
      throw new ApiError(terms.labels.client_login_expired, 401);
    }
    if (!response.ok) {
      const data = record(body),
        detail = record(data.detail),
        error = record(data.error);
      if (
        response.status === 409 &&
        [data.code, detail.code, error.code].some(
          (code) => code === 'workspace_changed' || code === 'WORKSPACE_CHANGED',
        )
      ) {
        setWorkspaceTransition('leaving');
        setBusinessBlocked(true);
        invalidateClientRequests();
        try {
          invalidateRequests();
        } finally {
          window.location.replace('/');
        }
        throw new ApiError(terms.labels.client_workspace_changed, 409, body);
      }
      const message =
        typeof error.message === 'string'
          ? error.message
          : typeof data.detail === 'string'
            ? data.detail
            : typeof detail.message === 'string'
              ? detail.message
              : terms.labels.client_request_failed;
      throw new ApiError(message, response.status, body);
    }
    if (parseError) throw new ApiError(terms.labels.client_response_invalid, response.status);
    return body as T;
  } finally {
    pending.delete(controller);
    init.signal?.removeEventListener('abort', abort);
  }
}
export function invokeCapability<T = unknown>(
  id: string,
  body: unknown,
  options: Pick<RequestInit, 'signal'> = {},
): Promise<T> {
  return api<T>('/v2/capabilities/' + encodeURIComponent(id) + ':invoke', {
    method: 'POST',
    body: JSON.stringify(body),
    signal: options.signal,
  });
}
