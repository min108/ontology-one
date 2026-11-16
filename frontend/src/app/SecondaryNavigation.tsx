import { NavLink } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import type { RouteEntry } from './navigation';
import styles from './SecondaryNavigation.module.css';

export function SecondaryNavigation({ entries }: { entries: RouteEntry[] }) {
  if (!entries.length) return null;
  return (
    <nav className={styles.navigation} aria-label={terms.labels.shell_navigation_secondary}>
      {entries.map((entry) => (
        <NavLink
          key={entry.key}
          to={entry.href}
          className={({ isActive }) => (isActive ? styles.active : styles.item)}
        >
          {entry.label}
        </NavLink>
      ))}
    </nav>
  );
}
