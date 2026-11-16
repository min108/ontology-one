import type {
  backend__api__v2__orgs__Organization,
  User,
  Grant,
  UserManageInput,
  GrantManageInput,
  OrgManageInput,
  MutationOutput,
  AuditOutput,
} from '../../api/generated/types';
import { invokeCapability } from '../../api/client';
export type Org = backend__api__v2__orgs__Organization;
export type { User, Grant, UserManageInput, GrantManageInput, OrgManageInput, AuditOutput };
export type List<T> = { items: T[]; total: number; page?: number; page_size?: number };
export const manageUser = (body: UserManageInput) => invokeCapability<MutationOutput>('user.manage', body);
export const manageGrant = (body: GrantManageInput) => invokeCapability<MutationOutput>('grant.manage', body);
export const manageOrg = (body: OrgManageInput) => invokeCapability<MutationOutput>('org.manage', body);
