import { HomeIntake } from '../features/home/HomeIntake';
import { RecentRecorder } from '../features/home/RecentRecorder';
import { Alert, Button } from 'antd';
import { Blocks, Building2, Hammer, House, Play, ShieldCheck, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import LegacyHarnessConversation from '@legacy/components/LegacyHarnessConversation';
import { terms } from '../i18n/zh-CN';
import { setWorkspaceConfirmation, useSessionStore } from '../state/session';
import { useUiStore } from '../state/ui';
import { AppShell } from '../ui/AppShell';
import { LegacyBlock } from '../ui/LegacyBlock';
import { NavRail } from '../ui/NavRail';
import { AppRoutes, completedAdministration } from './AppRoutes';
import { GlobalSearch } from './GlobalSearch';
import { primaryNavigation, secondaryNavigation, workspaceFor } from './navigation';
import { WorkspaceBreadcrumbs } from './WorkspaceBreadcrumbs';
import { WorkspaceConfirmation } from './WorkspaceConfirmation';
import { WorkspaceHeader } from './WorkspaceHeader';
import { useDownloadWorkspace } from './useDownloadWorkspace';
import { Link } from 'react-router-dom';
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
  const { key, pathname, search } = useLocation();
  const navigate = useNavigate();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [narrowSplit, setNarrowSplit] = useState(() => window.matchMedia('(max-width: 1439px)').matches);
  useEffect(() => {
    if (new URLSearchParams(search).get('harness_run')?.trim()) setAssistantOpen(true);
  }, [key, pathname, search]);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1439px)');
    const update = () => setNarrowSplit(media.matches);
    media.addEventListener('change', update);
    const close = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // An inner dialog owns Escape, including when its handler unmounts it.
      if (
        event
          .composedPath()
          .some((node) => node instanceof Element && node.matches('[role=dialog], [role=alertdialog]'))
      )
        return;
      const hasVisibleDialog = [
        ...document.querySelectorAll('[role=dialog][aria-modal=true], [role=alertdialog][aria-modal=true]'),
      ].some((dialog) => {
        for (let node: Element | null = dialog; node; node = node.parentElement) {
          const style = getComputedStyle(node);
          if (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            node.getAttribute('aria-hidden') === 'true'
          )
            return false;
        }
        return true;
      });
      if (hasVisibleDialog) return;
      setAssistantOpen(false);
    };
    const show = () => setAssistantOpen(true);
    window.addEventListener('keydown', close);
    window.addEventListener('ontology:harness-run-started', show);
    return () => {
      media.removeEventListener('change', update);
      window.removeEventListener('keydown', close);
      window.removeEventListener('ontology:harness-run-started', show);
    };
  }, []);
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
  const splitPage = pathname.startsWith('/build/changesets/') || pathname === '/govern/reviews';
  const navCollapsed = collapsed || (splitPage && narrowSplit);
  const navPath = pathname.startsWith('/build/changesets/')
    ? '/build/intake'
    : pathname.startsWith('/ontology/elements/')
      ? '/ontology/catalog'
      : pathname;
  const principal = {
    role: session.user.role,
    superadmin: session.user.is_superadmin,
    permissions: permission_keys,
  };
  const items = primaryNavigation(principal.role).map((entry) => {
    const Icon = icons[entry.key];
    return {
      ...entry,
      icon: <Icon />,
      children: secondaryNavigation(entry.key, principal, completedAdministration),
    };
  });
  return (
    <AppShell
      collapsed={navCollapsed}
      assistantOpen={assistantOpen}
      assistant={
        <div className={styles['assistant-layout']}>
          <header className={styles['assistant-header']}>
            <h2>{terms.labels.r3_ui_assistant_title}</h2>
            <Button
              type="text"
              aria-label={terms.actions.r3_ui_close}
              icon={<X aria-hidden="true" />}
              onClick={() => setAssistantOpen(false)}
            />
          </header>
          <div className={styles['assistant-body']}>
            <p>{terms.labels.r3_ui_assistant_help}</p>
            <Link to="/applications" onClick={() => setAssistantOpen(false)}>
              {terms.actions.r3_ui_assistant_open}
            </Link>
          </div>
          <LegacyBlock page={false} className={styles['assistant-task']}>
            <LegacyHarnessConversation />
          </LegacyBlock>
        </div>
      }
      navigation={
        <div className={`sidebar ${styles.navigation}`}>
          <NavRail
            items={items}
            permissions={{}}
            currentKey={workspace}
            currentHref={navPath}
            onNavigate={navigate}
            collapsed={navCollapsed}
            onCollapsedChange={splitPage && narrowSplit ? undefined : setCollapsed}
          />
        </div>
      }
      topBar={
        <WorkspaceHeader
          collapsed={navCollapsed}
          assistantOpen={assistantOpen}
          onAssistant={() => setAssistantOpen((value) => !value)}
          onSearch={() => {
            setSearchValue('');
            setSearchOpen(true);
          }}
        />
      }
    >
      <WorkspaceConfirmation register={setWorkspaceConfirmation} />
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
