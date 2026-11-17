import { useQuery } from '@tanstack/react-query';
import { App, Button, Popover, Select, Tag, Tooltip } from 'antd';
import {
  Bell,
  ChevronDown,
  CircleHelp,
  Globe,
  LockKeyhole,
  MessageSquare,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
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
import { roleLabel } from './identity';
import { requestRouteNavigation } from './guardedNavigation';
import styles from './WorkspaceShell.module.css';

export function WorkspaceHeader({
  onSearch,
  onAssistant,
  assistantOpen = false,
  collapsed: navCollapsed,
}: {
  onSearch: () => void;
  onAssistant?: () => void;
  assistantOpen?: boolean;
  collapsed?: boolean;
}) {
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
  const identityLabel = `${terms.labels.r3_ui_org_switch}：${session.workspace.name} ${roleLabel(session.user.role)}`;
  return (
    <TopBar
      workspaceName={session.workspace.name}
      collapsed={navCollapsed ?? collapsed}
      onSearch={onSearch}
      onHome={() => void requestRouteNavigation('/', navigate)}
      identity={
        <>
          <Popover
            trigger="click"
            placement="bottomLeft"
            content={
              <div className={styles['identity-popover']}>
                <label htmlFor="active-workspace">{terms.labels.organization}</label>
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
                <dl>
                  <dt>{terms.labels.r3_ui_role}</dt>
                  <dd>{roleLabel(session.user.role)}</dd>
                  <dt>{terms.labels.r3_ui_scope}</dt>
                  <dd>
                    {session.workspace.tenant_id !== session.user.home_tenant_id
                      ? terms.labels.r3_ui_cross_org
                      : terms.labels.shell_scope_local}
                  </dd>
                </dl>
              </div>
            }
          >
            <Button className={styles['identity-trigger']} type="text" aria-label={identityLabel}>
              <span>{session.workspace.name}</span>
              <span className={styles['identity-role']}>{roleLabel(session.user.role)}</span>
              <ChevronDown aria-hidden="true" />
            </Button>
          </Popover>
          {session.workspace.tenant_id !== session.user.home_tenant_id && (
            <Tag className={styles.restriction} icon={<Globe aria-hidden="true" />}>
              {terms.labels.r3_ui_cross_org}
            </Tag>
          )}
          {(!session.permissions.can_edit || session.workspace.readonly) && (
            <Tag
              className={styles.restriction}
              icon={
                session.permissions.can_review && !session.workspace.readonly ? (
                  <ShieldCheck aria-hidden="true" />
                ) : (
                  <LockKeyhole aria-hidden="true" />
                )
              }
            >
              {session.permissions.can_review && !session.workspace.readonly
                ? terms.labels.r3_ui_reviewonly
                : terms.labels.r3_ui_readonly}
            </Tag>
          )}
        </>
      }
      actions={
        <>
          <LegacyBlock variant="contents">
            <HarnessActivity />
          </LegacyBlock>
          <Tooltip
            title={
              query.data?.online_verified
                ? terms.labels.shell_assistant_online
                : query.data?.configured
                  ? terms.labels.shell_assistant_unverified
                  : terms.labels.shell_assistant_unconfigured
            }
          >
            <Link
              to="/admin/model"
              aria-label={
                query.data?.online_verified
                  ? terms.labels.shell_assistant_online
                  : query.data?.configured
                    ? terms.labels.shell_assistant_unverified
                    : terms.labels.shell_assistant_unconfigured
              }
              className={styles['pending-link']}
            >
              <Sparkles aria-hidden="true" />
            </Link>
          </Tooltip>
          <Link to="/work" aria-label={terms.actions.shell_pending} className={styles['pending-link']}>
            <Bell />
          </Link>
          <Tooltip title={terms.help.view_mode}>
            <Button type="text" aria-label={terms.labels.help} icon={<CircleHelp aria-hidden="true" />} />
          </Tooltip>
          <Button
            type="text"
            aria-label={terms.labels.r3_ui_assistant}
            aria-expanded={assistantOpen}
            onClick={onAssistant}
            icon={<MessageSquare aria-hidden="true" />}
          >
            {terms.labels.r3_ui_assistant}
          </Button>
        </>
      }
      userMenu={<UserMenu />}
    />
  );
}
