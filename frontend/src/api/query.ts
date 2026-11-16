import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

/** Every business cache belongs to an explicit institution and query scope. */
export function workspaceQueryKey(
  tenantId: string,
  scopeKey: string,
  resource: string,
  ...parts: readonly unknown[]
) {
  if (!tenantId || !scopeKey || !resource)
    throw new TypeError('Workspace query key requires tenant, scope and resource');
  return ['workspace', tenantId, scopeKey, resource, ...parts] as const;
}
