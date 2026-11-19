import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { invokeCapability } from '../api/client';
import { queryClient, workspaceQueryKey } from '../api/query';
import { useScopeStore } from '../state/scope';
import { useSessionStore } from '../state/session';
import { Omnibox, type OmniboxItem } from '../ui/Omnibox';
import { terms } from '../i18n/zh-CN';
import { commandsFor, matchingCommands } from '../features/home/commands';
import { readRecent } from '../features/home/recent';
import { requestRouteNavigation } from './guardedNavigation';

type SearchItem = OmniboxItem & { href: string };
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error(terms.labels.global_search_failed);
  return value as Record<string, unknown>;
}
/** Validate search rows and construct local destinations from their identifiers. */
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
    for (const field of ['title', 'summary', 'status', 'highlight', 'entity_id', 'chunk_id', 'doc_type'])
      if (item[field] !== undefined && item[field] !== null && typeof item[field] !== 'string')
        throw new Error(terms.labels.global_search_failed);
    if (item.doc_type && item.doc_type !== 'element' && item.doc_type !== 'chunk')
      throw new Error(terms.labels.global_search_failed);
    seen.add(item.id);
    const kinds: Record<string, string> = terms.kinds;
    const statuses: Record<string, string> = terms.statuses.element;
    const status =
      typeof item.status === 'string' && item.status
        ? Object.hasOwn(statuses, item.status)
          ? statuses[item.status]
          : terms.labels.unknown_status
        : '';
    const kind = Object.hasOwn(kinds, item.kind) ? kinds[item.kind] : terms.labels.unknown_kind;
    let href = '/ontology/elements/' + encodeURIComponent(item.id);
    if (item.doc_type === 'chunk') {
      if (
        typeof item.entity_id !== 'string' ||
        !item.entity_id.trim() ||
        typeof item.chunk_id !== 'string' ||
        !item.chunk_id.trim()
      )
        throw new Error(terms.labels.global_search_failed);
      href =
        '/materials/' + encodeURIComponent(item.entity_id) + '?chunk=' + encodeURIComponent(item.chunk_id);
    } else if (!item.doc_type && item.kind === 'material') {
      href = '/materials/' + encodeURIComponent(item.id);
    }
    const description = typeof item.summary === 'string' ? item.summary : undefined;
    return {
      id: item.id,
      href,
      label: typeof item.title === 'string' && item.title.trim() ? item.title : item.id,
      description,
      excerpt:
        typeof item.highlight === 'string' && !description?.includes(item.highlight)
          ? item.highlight
          : undefined,
      group: [item.doc_type === 'chunk' ? terms.labels.global_search_source_chunk : kind, status]
        .filter(Boolean)
        .join(' · '),
    };
  });
}
export type GlobalSearchProps = { open: boolean; onClose: () => void; initialValue?: string };
export function GlobalSearch({ open, onClose, initialValue = '' }: GlobalSearchProps) {
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
      initialValue={initialValue}
    />
  );
}
function WorkspaceSearch({
  tenantId,
  scopeKey,
  onClose,
  initialValue,
}: {
  tenantId: string;
  scopeKey: string;
  onClose: () => void;
  initialValue: string;
}) {
  const navigate = useNavigate();
  const selectRoute = (href: string) => {
    if (requestRouteNavigation(href, navigate)) onClose();
  };
  const canSearchGroup = useSessionStore((state) => state.permission_keys.includes('federation.read'));
  const session = useSessionStore((state) => state.session);
  const [value, setValue] = useState(initialValue);
  const [debounced, setDebounced] = useState('');
  const text = value.trim();
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(text), 250);
    return () => window.clearTimeout(timer);
  }, [text]);
  const commandMode = text.startsWith('/');
  const ready = Boolean(text) && !commandMode && text === debounced;
  const commands = matchingCommands(
    commandsFor({
      canEdit: !!session?.permissions.can_edit && !session.workspace.readonly,
      canReview: !!session?.permissions.can_review && !session.workspace.readonly,
      manageUsers: !!session?.permissions.manage_users,
      federation: canSearchGroup,
    }),
    text,
  );
  const recent = session ? readRecent({ userId: session.user.id, tenantId, scopeKey }) : [];
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
  const searchItems = ready && !query.isError ? (query.data ?? []) : [];
  const items: OmniboxItem[] = commandMode
    ? commands.map((item) => ({
        id: 'command:' + item.id,
        label: item.label,
        group: terms.labels.home_commands,
      }))
    : !text
      ? recent.map((item) => ({
          id: 'recent:' + item.href,
          label: item.title,
          group: terms.labels.home_recent,
        }))
      : searchItems;
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
      highlight={ready ? text : undefined}
      emptyMessage={
        ready && !commandMode
          ? Array.from(text).length < 2
            ? terms.labels.global_search_short
            : terms.labels.global_search_insufficient
          : undefined
      }
      loading={!commandMode && Boolean(text) && (!ready || query.isFetching)}
      error={ready && query.isError ? terms.labels.global_search_failed : undefined}
      onRetry={() => {
        void query.refetch();
      }}
      onSelect={(item) => {
        if (commandMode) {
          const command = commands.find((entry) => 'command:' + entry.id === item.id);
          if (command) {
            selectRoute(command.href);
          }
          return;
        }
        if (!text) {
          const visit = recent.find((entry) => 'recent:' + entry.href === item.id);
          if (visit) {
            selectRoute(visit.href);
          }
          return;
        }
        const selected = searchItems.find((entry) => entry.id === item.id);
        if (!selected) return;
        selectRoute(selected.href);
      }}
    />
  );
}
