import { useEffect } from 'react';

/** Existing authenticated download links must keep the same tenant binding. */
export function useDownloadWorkspace(tenantId: string) {
  useEffect(() => {
    const decorate = () => {
      for (const anchor of document.querySelectorAll<HTMLAnchorElement>('a[href]')) {
        const url = new URL(anchor.href, window.location.origin);
        if (
          url.origin === window.location.origin &&
          url.pathname.startsWith('/api/') &&
          url.searchParams.get('workspace_id') !== tenantId
        ) {
          url.searchParams.set('workspace_id', tenantId);
          anchor.href = url.pathname + url.search + url.hash;
        }
      }
    };
    decorate();
    const observer = new MutationObserver(decorate);
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('click', decorate, true);
    return () => {
      observer.disconnect();
      document.removeEventListener('click', decorate, true);
    };
  }, [tenantId]);
}
