import { useQuery } from '@tanstack/react-query';
import { App, Select } from 'antd';
import { Bell, Sparkles } from 'lucide-react';
import { useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import HarnessActivity from '@legacy/components/HarnessActivity';
import { readAssistantStatus } from '../api/shell';
import { workspaceQueryKey } from '../api/query';
import { terms } from '../i18n/zh-CN';
import { useSessionStore } from '../state/session';
import { useScopeStore } from '../state/scope';
import { useUiStore } from '../state/ui';
import { LegacyBlock } from '../ui/LegacyBlock';
import { TopBar } from '../ui/TopBar';
import { UserMenu } from './UserMenu';
import styles from './WorkspaceShell.module.css';

export function WorkspaceHeader({ onSearch }: { onSearch: () => void }) {
  const { session, switching, switchWorkspace } = useSessionStore();
  const scopeKey = useScopeStore((state) => state.scopeKey);
  const collapsed = useUiStore((state) => state.collapsed);
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const query = useQuery({
    queryKey: workspaceQueryKey(session?.workspace.tenant_id ?? 'unavailable', scopeKey, 'ai-status'),
    queryFn: ({ signal }) => readAssistantStatus(signal),
    enabled: Boolean(session),
  });
  const { refetch } = query;
  useEffect(() => {
    const refresh = () => void refetch();
    window.addEventListener('ontology:harness-configured', refresh);
    return () => window.removeEventListener('ontology:harness-configured', refresh);
  }, [refetch]);
  useEffect(() => {
    void refetch();
  }, [pathname, refetch]);
  if (!session) return null;
  return (
    <TopBar
      workspaceName={session.workspace.name}
      collapsed={collapsed}
      onSearch={onSearch}
      onHome={() => void navigate('/')}
      identity={
        <Select
          id="active-workspace"
          virtual={false}
          aria-label={terms.labels.organization}
          className={styles['workspace-select']}
          value={session.workspace.tenant_id}
          disabled={switching || session.workspaces.length <= 1}
          options={session.workspaces.map((workspace) => ({
            value: workspace.tenant_id,
            label: workspace.name,
          }))}
          onChange={(id: string) => {
            void switchWorkspace(id).catch((error: unknown) =>
              message.error(error instanceof Error ? error.message : terms.states.error.description),
            );
          }}
        />
      }
      actions={
        <>
          <LegacyBlock variant="contents">
            <HarnessActivity />
          </LegacyBlock>
          <Link to="/admin/model" className={styles['assistant-status']}>
            <Sparkles />
            <span>
              {query.data?.online_verified
                ? terms.labels.shell_assistant_online
                : query.data?.configured
                  ? terms.labels.shell_assistant_unverified
                  : terms.labels.shell_assistant_unconfigured}
            </span>
          </Link>
          <Link to="/work" aria-label={terms.actions.shell_pending} className={styles['pending-link']}>
            <Bell />
          </Link>
        </>
      }
      userMenu={<UserMenu />}
    />
  );
}
