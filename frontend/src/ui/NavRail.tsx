import { Button } from 'antd';
import { ChevronDown, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useState } from 'react';
import { terms } from '../i18n/zh-CN';
import type { NavigationItem } from './types';
import styles from './NavRail.module.css';
export interface NavRailProps {
  items: NavigationItem[];
  permissions: Record<string, boolean>;
  currentKey: string;
  currentHref?: string;
  onNavigate?: (href: string) => void;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}
export function NavRail({
  items,
  permissions,
  currentKey,
  currentHref = '',
  onNavigate,
  collapsed = false,
  onCollapsedChange,
}: NavRailProps) {
  const [expanded, setExpanded] = useState<string>();
  const [flyout, setFlyout] = useState('');
  const follow = (event: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    if (
      onNavigate &&
      !event.defaultPrevented &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.shiftKey &&
      !event.altKey &&
      event.button === 0
    ) {
      event.preventDefault();
      setExpanded(undefined);
      setFlyout('');
      onNavigate(href);
    }
  };
  return (
    <nav
      className={`${styles.rail} ${collapsed ? styles.collapsed : ''}`}
      aria-label={terms.labels.navigation}
      data-nav-rail
    >
      <div className={styles.entries}>
        {items
          .filter((item) => !item.permission || permissions[item.permission] === true)
          .map((item) => {
            const children =
              item.children?.filter((child) => !child.permission || permissions[child.permission] === true) ??
              [];
            const open = collapsed ? flyout === item.key : (expanded ?? currentKey) === item.key;
            const selected = currentKey === item.key;
            return (
              <div
                key={item.key}
                className={styles.group}
                onMouseEnter={() => collapsed && setFlyout(item.key)}
                onMouseLeave={() => collapsed && setFlyout('')}
                onKeyDown={(event) => {
                  if (event.key === 'Escape' || event.key === 'ArrowLeft') {
                    setExpanded(event.key === 'Escape' ? undefined : '');
                    setFlyout('');
                  }
                  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                    setExpanded(item.key);
                    setFlyout(item.key);
                  }
                }}
              >
                <a
                  href={item.href}
                  data-primary-link
                  title={item.label}
                  aria-label={item.label}
                  aria-expanded={children.length ? open : undefined}
                  aria-current={selected ? 'page' : undefined}
                  className={`${styles.item} ${selected && (collapsed || !children.length || !open) ? styles.current : ''}`}
                  onClick={(event) => follow(event, item.href)}
                >
                  <span className={styles.icon}>{item.icon ?? item.label.slice(0, 1)}</span>
                  <span className={styles.label}>{item.label}</span>
                  {children.length > 0 && !collapsed && (
                    <ChevronDown className={styles.chevron} aria-hidden="true" />
                  )}
                </a>
                {children.length > 0 && open && (
                  <nav className={styles.secondary} aria-label={terms.labels.shell_navigation_secondary}>
                    {children.map((child) => (
                      <a
                        key={child.key}
                        href={child.href}
                        className={`${styles.child} ${currentHref === child.href || currentHref.startsWith(child.href + '/') ? styles.current : ''}`}
                        aria-current={
                          currentHref === child.href || currentHref.startsWith(child.href + '/')
                            ? 'page'
                            : undefined
                        }
                        onClick={(event) => follow(event, child.href)}
                      >
                        {child.label}
                      </a>
                    ))}
                  </nav>
                )}
              </div>
            );
          })}
      </div>
      {onCollapsedChange && (
        <Button
          className={styles.toggle}
          autoInsertSpace={false}
          type="text"
          aria-label={collapsed ? terms.labels.r3_ui_nav_expand : terms.labels.r3_ui_nav_collapse}
          onClick={() => onCollapsedChange(!collapsed)}
          icon={collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        >
          {!collapsed && terms.labels.r3_ui_nav_collapse}
        </Button>
      )}
    </nav>
  );
}
