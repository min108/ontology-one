import type { HomeScope } from './intake';
import { transferScope } from './intake';
export type RecentVisit = { href: string; title: string; at: number };
const prefix = 'zy-recent-v1:';
export function recentHref(value: string): string | null {
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null;
  let url: URL;
  try {
    url = new URL(value, window.location.origin);
  } catch {
    return null;
  }
  if (
    url.origin !== window.location.origin ||
    !/^\/(?:assets|materials|datasets|projects|tasks|authority|skill-development)\/[^/]+$|^\/knowledge\/collections\/[^/]+$/.test(
      url.pathname,
    )
  )
    return null;
  // Keep only short UI positions; free text, return URLs and transient request data are not visit metadata.
  const retained = new URLSearchParams();
  for (const key of ['tab', 'view', 'section', 'page']) {
    const value = url.searchParams.get(key);
    if (value && /^[a-zA-Z0-9_-]{1,80}$/.test(value)) retained.set(key, value);
  }
  url.search = retained.toString();
  if (!/^#[a-zA-Z0-9_-]{1,80}$/.test(url.hash)) url.hash = '';
  return url.pathname + url.search + url.hash;
}
export function readRecent(scope: HomeScope): RecentVisit[] {
  if (!scope.userId || !scope.tenantId || !scope.scopeKey) return [];
  try {
    const raw = localStorage.getItem(prefix + encodeURIComponent(transferScope(scope)));
    if (!raw || raw.length > 16000) return [];
    const parsed: unknown = JSON.parse(raw);
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      !('scope' in parsed) ||
      parsed.scope !== transferScope(scope) ||
      !('items' in parsed) ||
      !Array.isArray(parsed.items)
    )
      return [];
    return parsed.items
      .filter(
        (item): item is RecentVisit =>
          !!item &&
          typeof item === 'object' &&
          typeof item.href === 'string' &&
          recentHref(item.href) === item.href &&
          typeof item.title === 'string' &&
          item.title.length <= 160 &&
          typeof item.at === 'number' &&
          Number.isFinite(item.at),
      )
      .slice(0, 8);
  } catch {
    return [];
  }
}
export function recordRecent(scope: HomeScope, item: Pick<RecentVisit, 'href' | 'title'>): void {
  const href = recentHref(item.href);
  if (!href || !scope.userId || !scope.tenantId || !scope.scopeKey) return;
  const items = [
    { href, title: item.title.slice(0, 160), at: Date.now() },
    ...readRecent(scope).filter((old) => old.href.split(/[?#]/)[0] !== href.split(/[?#]/)[0]),
  ].slice(0, 8);
  try {
    localStorage.setItem(
      prefix + encodeURIComponent(transferScope(scope)),
      JSON.stringify({ scope: transferScope(scope), items }),
    );
    window.dispatchEvent(new Event('ontology:recent-changed'));
  } catch {
    /* Storage may be disabled; business navigation still works. */
  }
}
