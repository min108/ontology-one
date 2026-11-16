import { Button } from 'antd';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { terms } from '../i18n/zh-CN';
import type { NavigationItem } from './types';
import styles from './NavRail.module.css';
export interface NavRailProps {
  items: NavigationItem[];
  permissions: Record<string, boolean>;
  currentKey: string;
  onNavigate?: (href: string) => void;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}
export function NavRail({
  items,
  permissions,
  currentKey,
  onNavigate,
  collapsed = false,
  onCollapsedChange,
}: NavRailProps) {
  return (
    <nav
      className={`${styles.rail} ${collapsed ? styles.collapsed : ''}`}
      aria-label={terms.labels.navigation}
    >
      {onCollapsedChange && (
        <Button
          autoInsertSpace={false}
          type="text"
          aria-label={collapsed ? terms.actions.navigate : terms.actions.collapse}
          onClick={() => onCollapsedChange(!collapsed)}
          icon={collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        />
      )}
      {items
        .filter((item) => !item.permission || permissions[item.permission] === true)
        .map((item) => (
          <a
            key={item.key}
            href={item.href}
            title={item.label}
            aria-label={item.label}
            className={`${styles.item} ${currentKey === item.key ? styles.current : ''}`}
            aria-current={currentKey === item.key ? 'page' : undefined}
            onClick={(event) => {
              if (onNavigate && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
                event.preventDefault();
                onNavigate(item.href);
              }
            }}
          >
            <span className={styles.icon}>{item.icon ?? item.label.slice(0, 1)}</span>
            <span className={styles.label}>{item.label}</span>
          </a>
        ))}
    </nav>
  );
}
