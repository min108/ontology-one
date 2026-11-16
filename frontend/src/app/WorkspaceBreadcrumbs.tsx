import { Breadcrumb } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import { primaryNavigation, routeEntry, workspaceFor } from './navigation';
import styles from './WorkspaceBreadcrumbs.module.css';

export function WorkspaceBreadcrumbs() {
  const location = useLocation();
  const [heading, setHeading] = useState('');
  const workspace = workspaceFor(location.pathname);
  const primary = primaryNavigation('admin').find((item) => item.key === workspace);
  const entry = routeEntry(location.pathname);
  const detail =
    /^\/(?:materials|datasets|data-sources|assets|authority|projects|tasks|skill-development)\/[^/]+$/.test(
      location.pathname,
    ) || /^\/knowledge\/collections\/[^/]+$/.test(location.pathname);
  useEffect(() => {
    setHeading('');
    if (!detail) return;
    const content = document.querySelector('[data-workspace-content]');
    if (!content) return;
    const refresh = () => setHeading(content.querySelector('h1')?.textContent?.trim() ?? '');
    const observer = new MutationObserver(refresh);
    observer.observe(content, { childList: true, subtree: true, characterData: true });
    refresh();
    return () => observer.disconnect();
  }, [location.pathname, detail]);
  const items = [{ title: <Link to={primary?.href ?? '/'}>{primary?.label ?? terms.labels.home}</Link> }];
  if (location.pathname === '/account/security') {
    items.push({ title: <span>{terms.labels.shell_security}</span> });
  } else if (location.pathname === '/briefing') {
    items.push({ title: <span>{terms.labels.shell_briefing}</span> });
  } else if (entry) {
    items.push({ title: detail ? <Link to={entry.href}>{entry.label}</Link> : <span>{entry.label}</span> });
  }
  if (detail && heading) items.push({ title: <span>{heading}</span> });
  return (
    <nav aria-label={terms.labels.shell_breadcrumb} className={styles.breadcrumb}>
      <Breadcrumb items={items} />
    </nav>
  );
}
