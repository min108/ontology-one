import { describe, expect, it } from 'vitest';
import { legacyRedirect, primaryNavigation, secondaryNavigation, workspaceFor } from './navigation';

const oldRoles = ['admin', 'modeler', 'reviewer'];
const completed = new Set(['/admin/users', '/admin/model', '/admin/backup', '/admin/system', '/admin/demo']);

describe('workspace navigation contracts', () => {
  for (const role of oldRoles) {
    it(`keeps six workspaces for the existing ${role} role`, () => {
      expect(primaryNavigation(role).map((item) => item.key)).toEqual([
        'home',
        'build',
        'ontology',
        'apply',
        'govern',
        'admin',
      ]);
    });
  }
  for (const role of ['viewer', 'analyst']) {
    it(`uses the declared three workspaces for ${role}`, () => {
      expect(primaryNavigation(role).map((item) => item.key)).toEqual(['home', 'ontology', 'apply']);
    });
  }
  it('does not admit an unknown role', () => {
    expect(primaryNavigation('unknown')).toEqual([]);
  });
  it('shows the group entry only for an explicit grant', () => {
    const principal = { role: 'modeler', superadmin: false, permissions: [] as string[] };
    expect(secondaryNavigation('ontology', principal, completed).some((item) => item.href === '/group')).toBe(
      false,
    );
    expect(
      secondaryNavigation('ontology', { ...principal, permissions: ['federation.read'] }, completed).some(
        (item) => item.href === '/group',
      ),
    ).toBe(true);
  });
  it('keeps read-only administration useful to existing non-admin roles', () => {
    expect(
      secondaryNavigation('admin', { role: 'reviewer', superadmin: false, permissions: [] }, completed).map(
        (item) => item.href,
      ),
    ).toEqual(['/admin/model', '/admin/backup', '/admin/demo', '/admin/system']);
  });
  it('does not expose unfinished or ungranted administration entries', () => {
    const principal = { role: 'admin', superadmin: false, permissions: [] };
    expect(secondaryNavigation('admin', principal, completed).map((item) => item.href)).not.toContain(
      '/admin/orgs',
    );
    expect(
      secondaryNavigation('admin', principal, new Set([...completed, '/admin/orgs', '/admin/audit'])).map(
        (item) => item.href,
      ),
    ).not.toContain('/admin/audit');
  });
  it('keeps detail routes in their workspace', () => {
    expect(workspaceFor('/materials/example')).toBe('build');
    expect(workspaceFor('/tasks/example')).toBe('build');
    expect(workspaceFor('/knowledge/collections/example')).toBe('ontology');
    expect(workspaceFor('/integration-applications')).toBe('admin');
    expect(workspaceFor('/briefing')).toBe('home');
  });
});

describe('saved legacy links', () => {
  for (const [from, to] of [
    ['/security', '/account/security'],
    ['/administration', '/admin/users'],
    ['/settings', '/admin/model'],
  ]) {
    it(`redirects ${from} without losing URL state`, () => {
      expect(legacyRedirect(from, '?page=2&query=example', '#section')).toBe(
        `${to}?page=2&query=example#section`,
      );
    });
  }
  it('routes the old demo anchor to its own administration page', () => {
    expect(legacyRedirect('/settings', '?sample=one', '#demo')).toBe('/admin/demo?sample=one#demo');
  });
  it('leaves retained routes intact', () => {
    expect(legacyRedirect('/materials', '?page=2', '#files')).toBeNull();
  });
});
