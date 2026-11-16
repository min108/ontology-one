import { terms } from '../../i18n/zh-CN';
export type CommandAccess = {
  canEdit: boolean;
  canReview: boolean;
  manageUsers: boolean;
  federation: boolean;
};
export type HomeCommand = { id: string; label: string; href: string };
export function commandsFor(access: CommandAccess): HomeCommand[] {
  const entries: (HomeCommand & { allowed?: boolean })[] = [
    { id: 'home', label: terms.labels.home_command_home, href: '/' },
    {
      id: 'materials',
      label: terms.labels.home_command_materials,
      href: '/materials?upload=1',
      allowed: access.canEdit,
    },
    {
      id: 'datasets',
      label: terms.labels.home_command_datasets,
      href: '/datasets?upload=1',
      allowed: access.canEdit,
    },
    { id: 'assets', label: terms.labels.home_command_assets, href: '/assets' },
    { id: 'methods', label: terms.labels.home_command_methods, href: '/applications' },
    { id: 'work', label: terms.labels.home_command_work, href: '/work?status=active' },
    { id: 'projects', label: terms.labels.home_command_projects, href: '/projects' },
    { id: 'history', label: terms.labels.home_command_history, href: '/maintenance?tab=history' },
    {
      id: 'review',
      label: terms.labels.home_command_review,
      href: '/maintenance?tab=review&review_status=review',
      allowed: access.canReview,
    },
    { id: 'briefing', label: terms.labels.home_command_briefing, href: '/briefing' },
    { id: 'group', label: terms.labels.home_command_group, href: '/group', allowed: access.federation },
    { id: 'account', label: terms.labels.home_command_account, href: '/account/security' },
    {
      id: 'users',
      label: terms.labels.home_command_users,
      href: '/admin/users',
      allowed: access.manageUsers,
    },
    { id: 'demo', label: terms.labels.home_command_demo, href: '/admin/demo' },
  ];
  return entries.filter((item) => item.allowed !== false).map(({ id, label, href }) => ({ id, label, href }));
}
export function matchingCommands(commands: HomeCommand[], query: string): HomeCommand[] {
  const term = query.replace(/^\//, '').trim().toLowerCase();
  return commands.filter((item) => !term || item.label.includes(term) || item.id.includes(term));
}
export function openHomeSearch(value = ''): void {
  window.dispatchEvent(new CustomEvent('ontology:open-search', { detail: value }));
}
