/** The v1 session contract remains intact while v2 permissions load separately. */
export type Tenant = {
  tenant_id: string;
  name: string;
  category: string;
  readonly: boolean;
  visibility?: string;
};
export type AuthSession = {
  authenticated: true;
  user: {
    id: string;
    username: string;
    display_name: string;
    home_tenant_id: string;
    role: string;
    is_superadmin: boolean;
    must_change_password: boolean;
  };
  workspace: Tenant;
  workspaces: Tenant[];
  roles: { id: string; name: string }[];
  permissions: { manage_users: boolean; manage_grants: boolean; can_edit: boolean; can_review: boolean };
  csrf_token: string;
  must_change_password: boolean;
};
export type SessionIdentity = { user_id: string; tenant_id: string; permissions: string[] };
