export const validPassword = (value: string): boolean =>
  value.length >= 12 && value.length <= 128 && /[A-Za-z]/.test(value) && /\d/.test(value);
export const validUsername = (value: string): boolean =>
  /^[a-z0-9][a-z0-9_.@-]{2,79}$/.test(value.trim().toLowerCase());
export const validReason = (value: string, required: boolean): boolean =>
  (!required && !value.trim()) || (value.trim().length >= 2 && value.trim().length <= 500);
export type GrantAccess = 'read_published' | 'read_private' | 'write';
export function allowedGrantAccess(
  user: { home_tenant_id: string; role: string },
  homeCategory: string,
  target: { id: string; category: string },
): GrantAccess[] {
  if (user.home_tenant_id === target.id) return [];
  if (user.home_tenant_id === 'hq')
    return user.role === 'admin'
      ? ['read_published', 'read_private', 'write']
      : ['read_published', 'read_private'];
  return homeCategory === 'headquarters_department' &&
    ['headquarters', 'headquarters_department'].includes(target.category)
    ? ['read_published']
    : [];
}
export function excludedParents(items: { id: string; parent_id?: string | null }[], id: string): Set<string> {
  const excluded = new Set([id]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const item of items)
      if (item.parent_id && excluded.has(item.parent_id) && !excluded.has(item.id)) {
        excluded.add(item.id);
        changed = true;
      }
  }
  return excluded;
}
