import { terms } from '../i18n/zh-CN';

export function roleLabel(role: string): string {
  return Object.hasOwn(terms.roles, role)
    ? terms.roles[role as keyof typeof terms.roles]
    : terms.labels.shell_unknown_role;
}
