import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { NavRail } from './NavRail';
import { terms } from '../i18n/zh-CN';

const entries = [
  {
    key: 'build',
    label: 'Build',
    href: '/build',
    children: [{ key: 'intake', label: 'Intake', href: '/build/intake' }],
  },
  {
    key: 'ontology',
    label: 'Ontology',
    href: '/ontology',
    children: [{ key: 'catalog', label: 'Catalog', href: '/ontology/catalog' }],
  },
];
it('places current secondary links inside the primary navigation and reports expansion', () => {
  render(<NavRail items={entries} permissions={{}} currentKey="build" currentHref="/build/intake" />);
  expect(screen.getByRole('link', { name: 'Build' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'Intake' })).toHaveAttribute('aria-current', 'page');
  expect(
    screen
      .getByRole('navigation', { name: terms.labels.shell_navigation_secondary })
      .closest('[data-nav-rail]'),
  ).not.toBeNull();
});
it('keeps labels when collapsed and opens secondary links by keyboard', () => {
  const follow = vi.fn();
  render(<NavRail items={entries} permissions={{}} currentKey="build" collapsed onNavigate={follow} />);
  expect(screen.getByRole('link', { name: 'Ontology' })).toHaveTextContent('Ontology');
  fireEvent.keyDown(screen.getByRole('link', { name: 'Ontology' }), { key: 'ArrowRight' });
  expect(screen.getByRole('link', { name: 'Ontology' })).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(screen.getByRole('link', { name: 'Catalog' }));
  expect(follow).toHaveBeenCalledWith('/ontology/catalog');
});
it('filters denied secondary entries and does not intercept modified navigation', () => {
  const follow = vi.fn();
  render(
    <NavRail
      items={[
        {
          ...entries[0],
          children: [{ key: 'secret', label: 'Secret', href: '/secret', permission: 'secret' }],
        },
      ]}
      permissions={{}}
      currentKey="build"
      onNavigate={follow}
    />,
  );
  expect(screen.queryByRole('link', { name: 'Secret' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Build' }), { ctrlKey: true });
  expect(follow).not.toHaveBeenCalled();
});
