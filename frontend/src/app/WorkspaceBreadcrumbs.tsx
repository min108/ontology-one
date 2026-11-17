import { ChevronLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import { routeEntry } from './navigation';
import styles from './WorkspaceBreadcrumbs.module.css';

export function legacyTitleRegion(container: Element): HTMLElement | null {
  const heading = container.querySelector<HTMLElement>('h1');
  return heading?.closest<HTMLElement>('header') ?? heading?.parentElement ?? null;
}

// Retain the original ContextTrail when it has a source-aware return link.
// Directly opened object URLs get a fallback inside that same title region.
export function WorkspaceBreadcrumbs() {
  const { pathname } = useLocation();
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [originalReturn, setOriginalReturn] = useState(false);
  const entry = routeEntry(pathname);
  const detail =
    /^\/(?:materials|datasets|data-sources|assets|authority|projects|tasks|skill-development)\/[^/]+$/.test(
      pathname,
    ) || /^\/knowledge\/collections\/[^/]+$/.test(pathname);
  useEffect(() => {
    setTarget(null);
    setOriginalReturn(false);
    if (!detail) return;
    const content = document.querySelector('[data-workspace-content]');
    if (!content) return;
    const refresh = () => {
      const legacy = content.querySelector('[data-legacy-page]');
      const header = legacy ? legacyTitleRegion(legacy) : null;
      const next = header?.querySelector<HTMLElement>('[data-legacy-header-context]') ?? header;
      setTarget((previous) => (previous === next ? previous : next));
      setOriginalReturn(Boolean(legacy?.querySelector('.context-back')));
    };
    const observer = new MutationObserver(refresh);
    observer.observe(content, { childList: true, subtree: true, characterData: true });
    refresh();
    return () => observer.disconnect();
  }, [pathname, detail]);
  if (!detail || !target || !entry || originalReturn) return null;
  return createPortal(
    <nav aria-label={terms.labels.shell_breadcrumb} className={styles.breadcrumb} data-object-return>
      <Link to={entry.href}>
        <ChevronLeft aria-hidden="true" />
        {entry.label}
      </Link>
    </nav>,
    target,
  );
}
