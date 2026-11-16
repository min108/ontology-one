import { useState } from 'react';
import { Alert, App, Button, Input, Tabs, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { useSessionStore } from '../../state/session';
import { useUiStore } from '../../state/ui';
import { useAddressState } from '../../app/addressState';
import { terms } from '../../i18n/zh-CN';
import { PageHeader } from '../../ui/PageHeader';
import { DataTable } from '../../ui/DataTable';
import { FilterBar } from '../../ui/FilterBar';
import {
  copy,
  action,
  EditDialog,
  Forbidden,
  QueryState,
  Timestamp,
  useIdentityQuery,
  useNarrowEditor,
} from '../identity/common';
import {
  allowedGrantAccess,
  validPassword,
  validReason,
  validUsername,
  type GrantAccess,
} from '../identity/validation';
import {
  manageUser,
  manageGrant,
  type User,
  type Org,
  type Grant,
  type List,
  type UserManageInput,
} from '../identity/contracts';
import styles from '../identity/Identity.module.css';

const roles = ['admin', 'modeler', 'reviewer'] as const;
type UserMode = 'create' | 'edit' | 'password' | 'status';
type UserEdit = { mode: UserMode; target?: User };
type IdentityQuery<T> = ReturnType<typeof useIdentityQuery<List<T>>>;
export default function UsersPage() {
  const { session, permission_keys } = useSessionStore();
  const { params, update } = useAddressState();
  const allowed = !!session?.permissions.manage_users && permission_keys.includes('admin.users');
  const grants = !!session?.user.is_superadmin && session.permissions.manage_grants;
  const tab = params.get('tab') === 'grants' && grants ? 'grants' : 'users';
  const users = useIdentityQuery<List<User>>('identity-users', '/v2/users', allowed);
  const orgs = useIdentityQuery<List<Org>>('identity-orgs', '/v2/orgs', allowed);
  const grantRows = useIdentityQuery<List<Grant>>(
    'identity-grants',
    '/v2/grants',
    allowed && tab === 'grants',
  );
  if (!allowed)
    return (
      <section aria-label={copy.identity_users}>
        <Forbidden />
      </section>
    );
  return (
    <section
      aria-label={copy.identity_users}
      aria-busy={users.isPending || orgs.isPending || (tab === 'grants' && grantRows.isPending)}
      className={styles.page}
    >
      <PageHeader title={copy.identity_users} />
      <Tabs
        activeKey={tab}
        onChange={(key) => update({ tab: key, q: null })}
        items={[
          {
            key: 'users',
            label: copy.identity_accounts_tab,
            children: <Accounts users={users} orgs={orgs} />,
          },
          ...(grants
            ? [
                {
                  key: 'grants',
                  label: copy.identity_grants_tab,
                  children: <Grants users={users} orgs={orgs} grants={grantRows} />,
                },
              ]
            : []),
        ]}
      />
    </section>
  );
}
function Accounts({ users, orgs }: { users: IdentityQuery<User>; orgs: IdentityQuery<Org> }) {
  const { session } = useSessionStore();
  const viewMode = useUiStore((state) => state.viewMode);
  const narrow = useNarrowEditor();
  const { params, update } = useAddressState();
  const [editing, setEditing] = useState<UserEdit | null>(null);
  const q = params.get('q') || '';
  const shown = (users.data?.items || []).filter((user) =>
    (user.username + ' ' + user.display_name + ' ' + user.tenant_name)
      .toLowerCase()
      .includes(q.toLowerCase()),
  );
  const columns: TableColumnsType<User> = [
    {
      key: 'name',
      title: copy.identity_name,
      render: (_, row) => (
        <div>
          <strong>{row.display_name}</strong>
          <div>{row.username}</div>
        </div>
      ),
    },
    { key: 'org', title: copy.identity_organization, dataIndex: 'tenant_name' },
    {
      key: 'role',
      title: copy.identity_role,
      render: (_, row) => (
        <>
          {terms.roles[row.role]}
          {row.is_superadmin && <div>{copy.identity_superadmin}</div>}
        </>
      ),
    },
    {
      key: 'status',
      title: copy.identity_status,
      render: (_, row) => <Tag>{row.is_active ? copy.identity_enabled : copy.identity_disabled}</Tag>,
    },
    {
      key: 'actions',
      title: copy.identity_actions,
      render: (_, row) => (
        <div className={styles.actions}>
          <Button
            autoInsertSpace={false}
            disabled={narrow}
            onClick={() => setEditing({ mode: 'edit', target: row })}
          >
            {action.identity_edit_user}
          </Button>
          <Button
            autoInsertSpace={false}
            disabled={narrow}
            onClick={() => setEditing({ mode: 'password', target: row })}
          >
            {action.identity_reset_password}
          </Button>
          <Button
            autoInsertSpace={false}
            disabled={narrow || row.id === session!.user.id}
            title={row.id === session!.user.id ? copy.identity_self_disable : undefined}
            onClick={() => setEditing({ mode: 'status', target: row })}
          >
            {row.is_active ? action.identity_disable : action.identity_enable}
          </Button>
        </div>
      ),
    },
  ];
  return (
    <div
      className={styles.stack}
      aria-label={copy.identity_accounts_tab}
      aria-busy={users.isPending || orgs.isPending}
    >
      <p className={styles.help}>{copy.identity_user_list_help}</p>
      {narrow && <Alert title={copy.identity_narrow} type="info" />}
      <div className={styles.actions}>
        <Button
          autoInsertSpace={false}
          type="primary"
          disabled={narrow || orgs.isPending || orgs.isError}
          onClick={() => setEditing({ mode: 'create' })}
        >
          {action.identity_create_user}
        </Button>
        <Button
          autoInsertSpace={false}
          onClick={() => {
            void users.refetch();
            void orgs.refetch();
          }}
        >
          {action.identity_refresh}
        </Button>
      </div>
      <FilterBar
        value={q}
        onChange={(value) => update({ q: value, 'identity-users.page': null })}
        onReset={() => update({ q: null })}
      />
      <QueryState query={users}>
        <DataTable<User>
          key={q}
          columns={columns}
          dataSource={shown}
          rowKey="id"
          viewMode={viewMode}
          urlKey="identity-users"
          emptyAction={{
            label: action.identity_create_user,
            onClick: () => setEditing({ mode: 'create' }),
            disabled: narrow || orgs.isError || orgs.isPending,
          }}
        />
      </QueryState>
      {orgs.isError && <QueryState query={orgs}>{null}</QueryState>}
      {editing && (
        <UserDialog
          edit={editing}
          orgs={orgs.data?.items || []}
          onClose={() => setEditing(null)}
          onSaved={() => void users.refetch()}
          narrow={narrow}
        />
      )}
    </div>
  );
}
function UserDialog({
  edit,
  orgs,
  onClose,
  onSaved,
  narrow,
}: {
  edit: UserEdit;
  orgs: Org[];
  onClose: () => void;
  onSaved: () => void;
  narrow: boolean;
}) {
  const { session, reload } = useSessionStore();
  const { message } = App.useApp();
  const { mode, target } = edit;
  const [form, setForm] = useState({
    username: '',
    display_name: target?.display_name || '',
    tenant_id: target?.home_tenant_id || session!.workspace.tenant_id,
    role: target?.role || ('' as User['role'] | ''),
    password: '',
    reason: '',
  });
  const own = target?.id === session!.user.id,
    reasonRequired = !!target?.is_superadmin && !own;
  const update = (key: keyof typeof form, value: string) =>
    setForm((previous) => ({ ...previous, [key]: value }));
  const validName = !!form.display_name.trim() && form.display_name.trim().length <= 100;
  const valid =
    mode === 'create'
      ? validUsername(form.username) &&
        validName &&
        roles.includes(form.role as User['role']) &&
        orgs.some((org) => org.id === form.tenant_id && org.status === 'active') &&
        validPassword(form.password)
      : validReason(form.reason, reasonRequired) &&
        (mode === 'edit'
          ? validName && (own || roles.includes(form.role as User['role']))
          : mode === 'password'
            ? validPassword(form.password)
            : !own);
  const title =
    mode === 'create'
      ? copy.identity_user_create
      : mode === 'edit'
        ? copy.identity_user_edit
        : mode === 'password'
          ? copy.identity_user_password
          : target?.is_active
            ? copy.identity_user_disable
            : copy.identity_user_enable;
  const impact =
    mode === 'create'
      ? copy.identity_user_create_impact
      : own
        ? copy.identity_self_update_impact
        : mode === 'status'
          ? target?.is_active
            ? copy.identity_user_disable_impact
            : copy.identity_user_enable_impact
          : copy.identity_user_update_impact;
  async function save() {
    if (!valid) throw new Error('Invalid account form');
    let body: UserManageInput;
    if (mode === 'create')
      body = {
        action: 'create',
        create: {
          username: form.username.trim().toLowerCase(),
          display_name: form.display_name.trim(),
          tenant_id: form.tenant_id,
          role: form.role as User['role'],
          password: form.password,
        },
      };
    else
      body = {
        action: 'update',
        user_id: target!.id,
        changes: {
          ...(mode === 'password'
            ? { password: form.password }
            : mode === 'status'
              ? { is_active: !target!.is_active }
              : {
                  display_name: form.display_name.trim(),
                  ...(!own ? { role: form.role as User['role'] } : {}),
                }),
          ...(form.reason.trim() ? { reason: form.reason.trim() } : {}),
        },
      };
    await manageUser(body);
    if (own) await reload();
    else onSaved();
    void message.success(copy.identity_user_saved);
  }
  return (
    <EditDialog
      title={title}
      summary={title + ' · ' + (target?.display_name || form.display_name)}
      impact={impact}
      value={form}
      valid={valid}
      onSave={save}
      onClose={onClose}
      narrow={narrow}
    >
      <div className={styles.form}>
        {mode === 'create' && (
          <label className={styles.field} htmlFor="identity-user-name">
            {copy.identity_username}
            <Input
              id="identity-user-name"
              aria-label={copy.identity_username}
              autoComplete="off"
              maxLength={80}
              value={form.username}
              onChange={(event) => update('username', event.target.value)}
            />
            <span className={styles.help}>{copy.identity_username_invalid}</span>
          </label>
        )}
        {(mode === 'create' || mode === 'edit') && (
          <>
            <label className={styles.field} htmlFor="identity-display-name">
              {copy.identity_name}
              <Input
                id="identity-display-name"
                aria-label={copy.identity_name}
                maxLength={100}
                value={form.display_name}
                onChange={(event) => update('display_name', event.target.value)}
              />
            </label>
            {mode === 'create' && (
              <label className={styles.field} htmlFor="identity-user-org">
                {copy.identity_organization}
                <select
                  id="identity-user-org"
                  aria-label={copy.identity_organization}
                  className={styles.control}
                  value={form.tenant_id}
                  onChange={(event) => update('tenant_id', event.target.value)}
                >
                  {orgs
                    .filter((org) => org.status === 'active')
                    .map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
            <label className={styles.field} htmlFor="identity-user-role">
              {copy.identity_role}
              <select
                id="identity-user-role"
                aria-label={copy.identity_role}
                className={styles.control}
                disabled={own}
                value={form.role}
                onChange={(event) => update('role', event.target.value)}
              >
                <option value="">{copy.identity_choose_role}</option>
                {roles.map((role) => (
                  <option key={role} value={role}>
                    {terms.roles[role]}
                  </option>
                ))}
              </select>
              {own && <span className={styles.help}>{copy.identity_self_role}</span>}
            </label>
          </>
        )}
        {(mode === 'create' || mode === 'password') && (
          <>
            <label className={styles.field} htmlFor="identity-user-password">
              {mode === 'create' ? copy.identity_initial_password : copy.identity_temporary_password}
              <Input.Password
                id="identity-user-password"
                aria-label={
                  mode === 'create' ? copy.identity_initial_password : copy.identity_temporary_password
                }
                autoComplete="new-password"
                maxLength={128}
                value={form.password}
                onChange={(event) => update('password', event.target.value)}
              />
            </label>
            <p className={styles.help}>{copy.identity_password_rule}</p>
            <p className={styles.help}>{copy.identity_password_delivery}</p>
          </>
        )}
        {mode === 'status' && <p>{impact}</p>}
        {mode !== 'create' && (
          <label className={styles.field} htmlFor="identity-user-reason">
            {reasonRequired ? copy.identity_reason_required : copy.identity_reason}
            <Input.TextArea
              id="identity-user-reason"
              aria-label={reasonRequired ? copy.identity_reason_required : copy.identity_reason}
              rows={3}
              maxLength={500}
              value={form.reason}
              onChange={(event) => update('reason', event.target.value)}
            />
            <span className={styles.help}>{copy.identity_reason_rule}</span>
          </label>
        )}
      </div>
    </EditDialog>
  );
}
function Grants({
  grants,
  users,
  orgs,
}: {
  grants: IdentityQuery<Grant>;
  users: IdentityQuery<User>;
  orgs: IdentityQuery<Org>;
}) {
  const viewMode = useUiStore((state) => state.viewMode),
    narrow = useNarrowEditor();
  const [editing, setEditing] = useState<Grant | 'new' | null>(null);
  const { params, update } = useAddressState();
  const q = (params.get('q') || '').toLowerCase();
  const shown = (grants.data?.items || []).filter((grant) =>
    (grant.username + ' ' + grant.tenant_name).toLowerCase().includes(q),
  );
  const columns: TableColumnsType<Grant> = [
    { key: 'user', title: copy.identity_grant_user, dataIndex: 'username' },
    { key: 'org', title: copy.identity_grant_org, dataIndex: 'tenant_name' },
    { key: 'access', title: copy.identity_grant_access, render: (_, row) => accessLabel(row.access) },
    {
      key: 'time',
      title: copy.identity_grant_time,
      render: (_, row) => <Timestamp value={row.created_at} />,
    },
    {
      key: 'actions',
      title: copy.identity_actions,
      render: (_, row) => (
        <Button autoInsertSpace={false} disabled={narrow} onClick={() => setEditing(row)}>
          {action.identity_revoke_grant}
        </Button>
      ),
    },
  ];
  const unavailable = narrow || users.isPending || users.isError || orgs.isPending || orgs.isError;
  return (
    <div
      className={styles.stack}
      aria-label={copy.identity_grants_tab}
      aria-busy={grants.isPending || users.isPending || orgs.isPending}
    >
      <Alert type="info" title={copy.identity_grant_help} />
      <Button autoInsertSpace={false} type="primary" disabled={unavailable} onClick={() => setEditing('new')}>
        {action.identity_create_grant}
      </Button>
      <FilterBar
        value={params.get('q') || ''}
        onChange={(value) => update({ q: value, 'identity-grants.page': null })}
        onReset={() => update({ q: null, 'identity-grants.page': null })}
      />
      <QueryState query={grants}>
        <DataTable
          key={q}
          columns={columns}
          dataSource={shown}
          rowKey="id"
          urlKey="identity-grants"
          viewMode={viewMode}
          emptyAction={{
            label: action.identity_create_grant,
            onClick: () => setEditing('new'),
            disabled: unavailable,
          }}
        />
      </QueryState>
      {users.isError && <QueryState query={users}>{null}</QueryState>}
      {orgs.isError && <QueryState query={orgs}>{null}</QueryState>}
      {editing && (
        <GrantDialog
          target={editing === 'new' ? undefined : editing}
          users={users.data?.items || []}
          orgs={orgs.data?.items || []}
          narrow={narrow}
          onClose={() => setEditing(null)}
          onSaved={() => void grants.refetch()}
        />
      )}
    </div>
  );
}
function accessLabel(access: GrantAccess): string {
  return {
    read_published: copy.identity_grant_public,
    read_private: copy.identity_grant_private,
    write: copy.identity_grant_write,
  }[access];
}
function GrantDialog({
  target,
  users,
  orgs,
  onClose,
  onSaved,
  narrow,
}: {
  target?: Grant;
  users: User[];
  orgs: Org[];
  onClose: () => void;
  onSaved: () => void;
  narrow: boolean;
}) {
  const [form, setForm] = useState({
    user_id: '',
    tenant_id: '',
    access: 'read_published' as GrantAccess,
    reason: '',
  });
  const { message } = App.useApp();
  const user = users.find((item) => item.id === form.user_id),
    org = orgs.find((item) => item.id === form.tenant_id),
    home = orgs.find((item) => item.id === user?.home_tenant_id);
  const allowed = user && org ? allowedGrantAccess(user, home?.category || '', org) : [];
  const valid =
    !!target ||
    (!!user?.is_active &&
      org?.status === 'active' &&
      allowed.includes(form.access) &&
      validReason(form.reason, true));
  const title = target ? copy.identity_grant_revoke : copy.identity_grant_create;
  async function save() {
    if (!valid) throw new Error('Invalid grant');
    await manageGrant(
      target
        ? { action: 'revoke', grant_id: target.id }
        : { action: 'set', grant: { ...form, reason: form.reason.trim() } },
    );
    onSaved();
    void message.success(copy.identity_grant_saved);
  }
  return (
    <EditDialog
      title={title}
      summary={
        title +
        ' · ' +
        (target?.username || user?.display_name || '') +
        ' · ' +
        (target?.tenant_name || org?.name || '')
      }
      impact={target ? copy.identity_grant_revoke_impact : copy.identity_grant_impact}
      value={form}
      valid={valid}
      onSave={save}
      onClose={onClose}
      narrow={narrow}
    >
      {target ? (
        <p>
          {target.username} · {target.tenant_name} · {accessLabel(target.access)}
        </p>
      ) : (
        <div className={styles.form}>
          <label className={styles.field} htmlFor="identity-grant-user">
            {copy.identity_grant_user}
            <select
              id="identity-grant-user"
              aria-label={copy.identity_grant_user}
              className={styles.control}
              value={form.user_id}
              onChange={(event) =>
                setForm({ ...form, user_id: event.target.value, access: 'read_published' })
              }
            >
              <option value="">{copy.identity_grant_user}</option>
              {users
                .filter((item) => item.is_active)
                .map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.display_name} · {item.username} · {item.tenant_name}
                  </option>
                ))}
            </select>
          </label>
          <label className={styles.field} htmlFor="identity-grant-org">
            {copy.identity_grant_org}
            <select
              id="identity-grant-org"
              aria-label={copy.identity_grant_org}
              className={styles.control}
              value={form.tenant_id}
              onChange={(event) =>
                setForm({ ...form, tenant_id: event.target.value, access: 'read_published' })
              }
            >
              <option value="">{copy.identity_choose_org}</option>
              {orgs
                .filter((item) => item.status === 'active' && item.id !== user?.home_tenant_id)
                .map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </label>
          <label className={styles.field} htmlFor="identity-grant-access">
            {copy.identity_grant_access}
            <select
              id="identity-grant-access"
              aria-label={copy.identity_grant_access}
              className={styles.control}
              value={form.access}
              onChange={(event) => setForm({ ...form, access: event.target.value as GrantAccess })}
            >
              {(['read_published', 'read_private', 'write'] as const).map((access) => (
                <option value={access} key={access} disabled={!allowed.includes(access)}>
                  {accessLabel(access)}
                </option>
              ))}
            </select>
          </label>
          {user && org && !allowed.length && <Alert type="warning" title={copy.identity_grant_invalid} />}
          <label className={styles.field} htmlFor="identity-grant-reason">
            {copy.identity_grant_reason}
            <Input.TextArea
              id="identity-grant-reason"
              aria-label={copy.identity_grant_reason}
              rows={3}
              maxLength={500}
              value={form.reason}
              onChange={(event) => setForm({ ...form, reason: event.target.value })}
            />
            <span className={styles.help}>{copy.identity_reason_rule}</span>
          </label>
        </div>
      )}
    </EditDialog>
  );
}
