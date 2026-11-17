import { useQuery } from '@tanstack/react-query';
import { workspaceQueryKey } from '../../api/query';
import { useScopeStore } from '../../state/scope';
import { api } from '../../api/client';
import { useSessionStore } from '../../state/session';
import { StateBlock } from '../../ui/StateBlock';
import { terms } from '../../i18n/zh-CN';

export const copy = terms.labels;
export function useManagementSession() {
  const session = useSessionStore((state) => state.session);
  return { session, canManage: Boolean(session?.permissions.manage_users && !session.workspace.readonly) };
}
export function useResource<T>(path: string, enabled = true) {
  const { session } = useManagementSession();
  const scope = useScopeStore((state) => state.scopeKey);
  const ready = enabled && Boolean(session);
  const query = useQuery({
    queryKey: workspaceQueryKey(
      session?.workspace.tenant_id ?? 'unavailable',
      scope,
      'resource',
      session?.user.id,
      path,
    ),
    queryFn: ({ signal }) => {
      return api<T>(path, { signal });
    },
    enabled: ready,
    staleTime: 0,
  });
  return {
    data: ready ? query.data : undefined,
    error:
      ready && query.error
        ? query.error instanceof Error
          ? query.error.message
          : copy.admin_failure
        : undefined,
    refreshing: ready && query.isFetching,
    reload: () => {
      if (ready) void query.refetch();
    },
  };
}
export function ResourceState({ error, reload }: { error?: string; reload: () => void }) {
  return (
    <StateBlock
      state={error ? 'error' : 'loading'}
      description={error}
      action={{ label: copy.admin_retry, onClick: reload }}
    />
  );
}
export interface ModelStatus {
  available: boolean;
  configured: boolean;
  verified: boolean;
  model: string;
  base_url: string;
  version?: string;
  reason?: string;
  migration_required?: boolean;
  configuration_notice?: string;
}
export interface SystemMetadata {
  product_name: string;
  product_name_en: string;
  version: string;
  runtime_mode: 'windows' | 'server';
  control: { current: number; pending: string[] };
  workspace: { current: number; pending: string[] };
}
export function modelLabel(status: ModelStatus) {
  return !status.available
    ? copy.admin_runtime_unavailable
    : status.verified
      ? copy.admin_verified
      : status.configured
        ? copy.admin_unverified
        : copy.admin_unconfigured;
}
