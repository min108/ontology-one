import { ChevronLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import { routeEntry } from './navigation';
import styles from './WorkspaceBreadcrumbs.module.css';

// New object pages own their return path in PageHeader. Retained object pages
// keep their source-aware navigation, presented inside their existing title.
export function WorkspaceBreadcrumbs() {
  const { pathname } = useLocation();
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [source, setSource] = useState<{ href: string; label: string } | null>(null);
  const entry = routeEntry(pathname);
  const detail =
    /^\/(?:materials|datasets|data-sources|assets|authority|projects|tasks|skill-development)\/[^/]+$/.test(
      pathname,
    ) || /^\/knowledge\/collections\/[^/]+$/.test(pathname);
  useEffect(() => {
    setTarget(null);
    setSource(null);
    if (!detail) return;
    const content = document.querySelector('[data-workspace-content]');
    if (!content) return;
    const refresh = () => {
      const heading = content.querySelector<HTMLElement>('[data-legacy-page] h1');
      const header = heading?.closest<HTMLElement>('header') ?? heading?.parentElement ?? null;
      setTarget((previous) => (previous === header ? previous : header));
      const origin = content.querySelector<HTMLAnchorElement>('.context-back');
      const href = origin?.getAttribute('href');
      const label = origin?.textContent?.trim();
      if (href?.startsWith('/') && !href.startsWith('//') && label)
        setSource((previous) =>
          previous?.href === href && previous.label === label ? previous : { href, label },
        );
    };
    const observer = new MutationObserver(refresh);
    observer.observe(content, { childList: true, subtree: true, characterData: true });
    refresh();
    return () => observer.disconnect();
  }, [pathname, detail]);
  if (!detail || !target || !entry) return null;
  return createPortal(
    <nav aria-label={terms.labels.shell_breadcrumb} className={styles.breadcrumb} data-object-return>
      <Link to={source?.href ?? entry.href}>
        <ChevronLeft aria-hidden="true" />
        {source?.label ?? entry.label}
      </Link>
    </nav>,
    target,
  );
}
