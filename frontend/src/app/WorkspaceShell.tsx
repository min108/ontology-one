import { HomeIntake } from '../features/home/HomeIntake';
import { RecentRecorder } from '../features/home/RecentRecorder';
import { Alert } from 'antd';
import { Blocks, Building2, Hammer, House, Play, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import LegacyHarnessConversation from '@legacy/components/LegacyHarnessConversation';
import { terms } from '../i18n/zh-CN';
import { setWorkspaceConfirmation, useSessionStore } from '../state/session';
import { useUiStore } from '../state/ui';
import { AppShell } from '../ui/AppShell';
import { IdentityBar } from '../ui/IdentityBar';
import { LegacyBlock } from '../ui/LegacyBlock';
import { NavRail } from '../ui/NavRail';
import { AppRoutes, completedAdministration } from './AppRoutes';
import { GlobalSearch } from './GlobalSearch';
import { roleLabel } from './identity';
import { primaryNavigation, secondaryNavigation, workspaceFor } from './navigation';
import { SecondaryNavigation } from './SecondaryNavigation';
import { WorkspaceBreadcrumbs } from './WorkspaceBreadcrumbs';
import { WorkspaceConfirmation } from './WorkspaceConfirmation';
import { WorkspaceHeader } from './WorkspaceHeader';
import { useDownloadWorkspace } from './useDownloadWorkspace';
import styles from './WorkspaceShell.module.css';

const icons = {
  home: House,
  build: Hammer,
  ontology: Blocks,
  apply: Play,
  govern: ShieldCheck,
  admin: Building2,
};

export function WorkspaceShell() {
  const { session, permission_keys, notice, setNotice } = useSessionStore();
  const { collapsed, setCollapsed } = useUiStore();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  useDownloadWorkspace(session?.workspace.tenant_id ?? '');
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchValue('');
        setSearchOpen(true);
      }
    };
    const open = (event: Event) => {
      setSearchValue(
        event instanceof CustomEvent && typeof event.detail === 'string' ? event.detail.slice(0, 500) : '',
      );
      setSearchOpen(true);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('ontology:open-search', open);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('ontology:open-search', open);
    };
  }, []);
  if (!session) return null;
  const workspace = workspaceFor(pathname);
  const principal = {
    role: session.user.role,
    superadmin: session.user.is_superadmin,
    permissions: permission_keys,
  };
  const items = primaryNavigation(principal.role).map((entry) => {
    const Icon = icons[entry.key];
    return { ...entry, icon: <Icon /> };
  });
  return (
    <AppShell
      navigation={
        <div className={`sidebar ${styles.navigation}`}>
          <NavRail
            items={items}
            permissions={{}}
            currentKey={workspace}
            onNavigate={navigate}
            collapsed={collapsed}
            onCollapsedChange={setCollapsed}
          />
        </div>
      }
      topBar={
        <WorkspaceHeader
          onSearch={() => {
            setSearchValue('');
            setSearchOpen(true);
          }}
        />
      }
      identity={
        <div className={styles.identity}>
          <IdentityBar
            organization={session.workspace.name}
            role={roleLabel(session.user.role)}
            scope={terms.labels.shell_scope_local}
          />
          <SecondaryNavigation entries={secondaryNavigation(workspace, principal, completedAdministration)} />
        </div>
      }
    >
      <WorkspaceConfirmation register={setWorkspaceConfirmation} />
      {!session.permissions.can_edit && (
        <Alert
          className={styles.notice}
          type="info"
          showIcon
          title={session.permissions.can_review ? terms.labels.shell_reviewonly : terms.labels.shell_readonly}
        />
      )}
      {notice && (
        <Alert
          className={styles.notice}
          type="success"
          role="status"
          title={notice}
          closable={{ closeIcon: terms.actions.shell_acknowledge }}
          onClose={() => setNotice('')}
        />
      )}
      <LegacyBlock variant="contents">
        <LegacyHarnessConversation />
      </LegacyBlock>
      <WorkspaceBreadcrumbs />
      <RecentRecorder />
      <HomeIntake key={session.user.id + ':' + session.workspace.tenant_id} />
      <div data-workspace-content>
        <AppRoutes principal={principal} />
      </div>
      <GlobalSearch initialValue={searchValue} open={searchOpen} onClose={() => setSearchOpen(false)} />
    </AppShell>
  );
}
