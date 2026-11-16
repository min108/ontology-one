import { App, Button, Dropdown } from 'antd';
import { ChevronDown, KeyRound, LogOut, Users } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useSessionStore } from '../state/session';
import { useUiStore } from '../state/ui';
import { ViewModeSwitch } from '../ui/ViewModeSwitch';
import { terms } from '../i18n/zh-CN';
import { roleLabel } from './identity';
import styles from './WorkspaceShell.module.css';

export function UserMenu() {
  const session = useSessionStore((state) => state.session);
  const logout = useSessionStore((state) => state.logout);
  const viewMode = useUiStore((state) => state.viewMode);
  const setViewMode = useUiStore((state) => state.setViewMode);
  const { message } = App.useApp();
  if (!session) return null;
  return (
    <Dropdown
      trigger={['click']}
      menu={{
        items: [
          {
            key: 'identity',
            disabled: true,
            label: (
              <div className={styles['account-identity']}>
                <strong>{session.user.username}</strong>
                <span>{session.workspace.name}</span>
                {session.user.is_superadmin && <span>{terms.labels.shell_headquarters_admin}</span>}
              </div>
            ),
          },
          { key: 'mode', label: <ViewModeSwitch value={viewMode} onChange={setViewMode} /> },
          {
            key: 'security',
            icon: <KeyRound />,
            label: <Link to="/account/security">{terms.labels.shell_security}</Link>,
          },
          ...(session.permissions.manage_users
            ? [
                {
                  key: 'users',
                  icon: <Users />,
                  label: <Link to="/admin/users">{terms.actions.shell_account_users}</Link>,
                },
              ]
            : []),
          {
            key: 'logout',
            icon: <LogOut />,
            label: terms.actions.shell_logout,
            onClick: () => {
              void logout().catch((error: unknown) =>
                message.error(error instanceof Error ? error.message : terms.states.error.description),
              );
            },
          },
        ],
      }}
    >
      <Button autoInsertSpace={false} type="text" aria-label={terms.labels.shell_account}>
        <span className={styles['user-name']}>
          <strong>{session.user.display_name}</strong>
          <small>{roleLabel(session.user.role)}</small>
        </span>
        <ChevronDown />
      </Button>
    </Dropdown>
  );
}
