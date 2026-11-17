import { useEffect, useState } from 'react';
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
  const identity = `${session?.user.id}:${session?.workspace.tenant_id}`;
  const [state, setState] = useState<{
    identity?: string;
    path?: string;
    data?: T;
    error?: string;
    refreshing?: boolean;
  }>({});
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setState((current) =>
      current.identity === identity && current.path === path
        ? { ...current, error: undefined, refreshing: true }
        : { identity, path, refreshing: true },
    );
    if (!enabled) return;
    const controller = new AbortController();
    void api<T>(path, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted) setState({ identity, path, data, refreshing: false });
      },
      (error: unknown) => {
        if (!controller.signal.aborted)
          setState((current) => ({
            identity,
            path,
            data: current.identity === identity && current.path === path ? current.data : undefined,
            error: error instanceof Error ? error.message : copy.admin_failure,
            refreshing: false,
          }));
      },
    );
    return () => controller.abort();
  }, [path, enabled, identity, revision]);
  return {
    ...(enabled && state.identity === identity && state.path === path ? state : {}),
    reload: () => setRevision((value) => value + 1),
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
