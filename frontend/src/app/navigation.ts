import { terms } from '../i18n/zh-CN';

export type Workspace = 'home' | 'build' | 'ontology' | 'apply' | 'govern' | 'admin';
export interface RouteEntry {
  key: string;
  label: string;
  href: string;
}
export interface NavigationPrincipal {
  role: string;
  superadmin: boolean;
  permissions: readonly string[];
}

const workspaces: (RouteEntry & { key: Workspace })[] = [
  { key: 'home', label: terms.labels.home, href: '/' },
  { key: 'build', label: terms.labels.build, href: '/build' },
  { key: 'ontology', label: terms.labels.ontology, href: '/ontology' },
  { key: 'apply', label: terms.labels.use, href: '/apply' },
  { key: 'govern', label: terms.labels.govern, href: '/govern' },
  { key: 'admin', label: terms.labels.admin, href: '/admin' },
];
const entries: Record<Workspace, RouteEntry[]> = {
  home: [],
  build: [
    { key: 'materials', label: terms.labels.shell_materials, href: '/materials' },
    { key: 'datasets', label: terms.labels.shell_datasets, href: '/datasets' },
    { key: 'sources', label: terms.labels.shell_sources, href: '/data-sources' },
    { key: 'experience', label: terms.labels.shell_experience, href: '/experience' },
    { key: 'skills', label: terms.labels.shell_skills, href: '/skill-development' },
    { key: 'projects', label: terms.labels.shell_projects, href: '/projects' },
    { key: 'work', label: terms.labels.shell_work, href: '/work' },
  ],
  ontology: [
    { key: 'assets', label: terms.labels.shell_assets, href: '/assets' },
    { key: 'authority', label: terms.labels.shell_authority, href: '/authority' },
    { key: 'graph', label: terms.labels.shell_graph, href: '/graph' },
    { key: 'group', label: terms.labels.shell_group, href: '/group' },
  ],
  apply: [{ key: 'applications', label: terms.labels.shell_applications, href: '/applications' }],
  govern: [
    { key: 'maintenance', label: terms.labels.shell_maintenance, href: '/maintenance' },
    { key: 'impact', label: terms.labels.shell_impact, href: '/impact' },
  ],
  admin: [
    { key: 'orgs', label: terms.labels.shell_orgs, href: '/admin/orgs' },
    { key: 'users', label: terms.labels.shell_users, href: '/admin/users' },
    { key: 'model', label: terms.labels.shell_model, href: '/admin/model' },
    { key: 'integrations', label: terms.labels.shell_integrations, href: '/integration-applications' },
    { key: 'backup', label: terms.labels.shell_backup, href: '/admin/backup' },
    { key: 'demo', label: terms.labels.shell_demo, href: '/admin/demo' },
    { key: 'system', label: terms.labels.system_info, href: '/admin/system' },
    { key: 'audit', label: terms.labels.shell_audit, href: '/admin/audit' },
  ],
};

export function primaryNavigation(role: string): (RouteEntry & { key: Workspace })[] {
  if (['admin', 'modeler', 'reviewer'].includes(role)) return workspaces;
  if (['viewer', 'analyst'].includes(role)) {
    return workspaces.filter((item) => ['home', 'ontology', 'apply'].includes(item.key));
  }
  return [];
}

export function secondaryNavigation(
  workspace: Workspace,
  principal: NavigationPrincipal,
  completed: ReadonlySet<string>,
): RouteEntry[] {
  if (!primaryNavigation(principal.role).some((item) => item.key === workspace)) return [];
  return entries[workspace].filter((entry) => {
    if (entry.href.startsWith('/admin/') && !completed.has(entry.href)) return false;
    if (entry.key === 'group') return principal.permissions.includes('federation.read');
    if (['users', 'integrations'].includes(entry.key)) return principal.role === 'admin';
    if (entry.key === 'orgs') return principal.superadmin && principal.permissions.includes('admin.org');
    if (entry.key === 'audit') return principal.superadmin && principal.permissions.includes('audit.read');
    return true;
  });
}

export function workspaceFor(pathname: string): Workspace {
  if (/^\/(admin|integration-applications)(\/|$)/.test(pathname)) return 'admin';
  if (
    /^\/(build|materials|datasets|data-sources|experience|skill-development|projects|tasks|work)(\/|$)/.test(
      pathname,
    )
  )
    return 'build';
  if (/^\/(ontology|assets|authority|graph|group|knowledge)(\/|$)/.test(pathname)) return 'ontology';
  if (/^\/(apply|applications)(\/|$)/.test(pathname)) return 'apply';
  if (/^\/(govern|maintenance|impact)(\/|$)/.test(pathname)) return 'govern';
  return 'home';
}

export function routeEntry(pathname: string): RouteEntry | undefined {
  if (pathname.startsWith('/tasks/')) return entries.build.find((entry) => entry.key === 'work');
  if (pathname.startsWith('/knowledge/collections/'))
    return entries.ontology.find((entry) => entry.key === 'assets');
  return Object.values(entries)
    .flat()
    .find((entry) => pathname === entry.href || pathname.startsWith(entry.href + '/'));
}

export function legacyRedirect(pathname: string, search: string, hash: string): string | null {
  const targets: Record<string, string> = {
    '/security': '/account/security',
    '/administration': '/admin/users',
    '/settings': hash === '#demo' ? '/admin/demo' : '/admin/model',
  };
  return targets[pathname] ? targets[pathname] + search + hash : null;
}
