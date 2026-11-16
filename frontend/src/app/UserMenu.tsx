import { App, Badge, Button, Dropdown } from 'antd';
import { ChevronDown, KeyRound, LogOut, Users } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useSessionStore } from '../state/session';
import { useUiStore } from '../state/ui';
import {
  loadPreferences,
  retryPreferenceSave,
  savePreferredViewMode,
  usePreferenceStore,
} from '../state/preferences';
import { ViewModeSwitch } from '../ui/ViewModeSwitch';
import { terms } from '../i18n/zh-CN';
import { roleLabel } from './identity';
import styles from './WorkspaceShell.module.css';

export function UserMenu() {
  const session = useSessionStore((state) => state.session);
  const logout = useSessionStore((state) => state.logout);
  const permissions = useSessionStore((state) => state.permission_keys);
  const viewMode = useUiStore((state) => state.viewMode);
  const { saving, error: preferenceError } = usePreferenceStore();
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
          { key: 'mode', label: <ViewModeSwitch value={viewMode} onChange={savePreferredViewMode} /> },
          ...(saving || preferenceError
            ? [
                {
                  key: 'preference-status',
                  label: (
                    <div aria-live="polite">
                      {saving
                        ? terms.labels.identity_prefs_saving
                        : preferenceError === 'save'
                          ? terms.labels.identity_prefs_save_failed
                          : terms.labels.identity_prefs_load_failed}
                      {preferenceError && (
                        <Button
                          onClick={() =>
                            preferenceError === 'save'
                              ? retryPreferenceSave()
                              : void loadPreferences(session.user.id, session.workspace.tenant_id)
                          }
                        >
                          {preferenceError === 'save'
                            ? terms.actions.identity_prefs_retry
                            : terms.actions.identity_prefs_load}
                        </Button>
                      )}
                    </div>
                  ),
                },
              ]
            : []),
          {
            key: 'security',
            icon: <KeyRound />,
            label: (
              <Badge dot={session.password_change_recommended}>
                <Link to="/account/security">{terms.labels.shell_security}</Link>
                {session.password_change_recommended && (
                  <span>{terms.labels.identity_password_recommended}</span>
                )}
              </Badge>
            ),
          },
          ...(session.permissions.manage_users && permissions.includes('admin.users')
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
      <Button
        autoInsertSpace={false}
        type="text"
        aria-label={`${terms.labels.shell_account}：${session.user.display_name} ${roleLabel(session.user.role)}`}
      >
        <span className={styles['user-name']}>
          <strong>{session.user.display_name}</strong>
          <small>{roleLabel(session.user.role)}</small>
        </span>
        <ChevronDown />
      </Button>
    </Dropdown>
  );
}
