import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { entityHref } from '@legacy/domain';
import { invokeCapability } from '../api/client';
import { queryClient, workspaceQueryKey } from '../api/query';
import { useScopeStore } from '../state/scope';
import { useSessionStore } from '../state/session';
import { Omnibox, type OmniboxItem } from '../ui/Omnibox';
import { terms } from '../i18n/zh-CN';

type SearchItem = OmniboxItem & { kind: string };
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error(terms.labels.global_search_failed);
  return value as Record<string, unknown>;
}
/** The capability returns JsonValue rows: validate before rendering or routing. */
export function parseSearchItems(value: unknown): SearchItem[] {
  const rows = object(value).items;
  if (!Array.isArray(rows)) throw new Error(terms.labels.global_search_failed);
  const seen = new Set<string>();
  return rows.map((row: unknown) => {
    const item = object(row);
    if (
      typeof item.id !== 'string' ||
      !item.id.trim() ||
      typeof item.kind !== 'string' ||
      !item.kind.trim() ||
      seen.has(item.id)
    )
      throw new Error(terms.labels.global_search_failed);
    for (const field of ['title', 'summary'])
      if (item[field] !== undefined && item[field] !== null && typeof item[field] !== 'string')
        throw new Error(terms.labels.global_search_failed);
    seen.add(item.id);
    const kinds: Record<string, string> = terms.kinds;
    return {
      id: item.id,
      kind: item.kind,
      label: typeof item.title === 'string' && item.title.trim() ? item.title : item.id,
      description: typeof item.summary === 'string' ? item.summary : undefined,
      group: Object.hasOwn(kinds, item.kind) ? kinds[item.kind] : terms.labels.unknown_kind,
    };
  });
}
export type GlobalSearchProps = { open: boolean; onClose: () => void };
export function GlobalSearch({ open, onClose }: GlobalSearchProps) {
  const tenantId = useScopeStore((state) => state.tenantId);
  const scopeKey = useScopeStore((state) => state.scopeKey);
  if (!open || !tenantId || !scopeKey) return null;
  // Remount on scope change: no prior input or result can flash in a new institution.
  return (
    <WorkspaceSearch
      key={JSON.stringify([tenantId, scopeKey])}
      tenantId={tenantId}
      scopeKey={scopeKey}
      onClose={onClose}
    />
  );
}
function WorkspaceSearch({
  tenantId,
  scopeKey,
  onClose,
}: {
  tenantId: string;
  scopeKey: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const canSearchGroup = useSessionStore((state) => state.permission_keys.includes('federation.read'));
  const [value, setValue] = useState('');
  const [debounced, setDebounced] = useState('');
  const text = value.trim();
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(text), 250);
    return () => window.clearTimeout(timer);
  }, [text]);
  const ready = Boolean(text) && text === debounced;
  const query = useQuery(
    {
      queryKey: workspaceQueryKey(tenantId, scopeKey, 'semantic.search', debounced),
      queryFn: async ({ signal }) =>
        parseSearchItems(await invokeCapability('semantic.search', { q: debounced }, { signal })),
      enabled: ready,
      retry: false,
    },
    queryClient,
  );
  const items = ready && !query.isError ? (query.data ?? []) : [];
  return (
    <Omnibox
      footer={
        canSearchGroup ? (
          <Link to="/group" onClick={onClose}>
            {terms.labels.global_search_group}
          </Link>
        ) : undefined
      }
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      value={value}
      onSearch={(next) => setValue(next.slice(0, 500))}
      items={items}
      loading={Boolean(text) && (!ready || query.isFetching)}
      error={ready && query.isError ? terms.labels.global_search_failed : undefined}
      onRetry={() => {
        void query.refetch();
      }}
      onSelect={(item) => {
        const selected = items.find((entry) => entry.id === item.id);
        if (!selected) return;
        navigate(entityHref(selected.id, selected.kind));
        onClose();
      }}
    />
  );
}
