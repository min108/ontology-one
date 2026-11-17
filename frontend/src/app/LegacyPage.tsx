import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ContextTrail } from '@legacy/navigation';
import { LegacyBlock } from '../ui/LegacyBlock';
import { LegacyOwnership } from './LegacyOwnership';
import { legacyTitleRegion } from './WorkspaceBreadcrumbs';
import styles from './WorkspaceBreadcrumbs.module.css';

export function LegacyPage({ children }: { children: ReactNode }) {
  const fallback = useRef<HTMLDivElement>(null);
  // Keep this portal target stable: moving its DOM host must not remount the
  // governed visibility editor, lose its unsaved selection or unregister its guard.
  const [host] = useState(() => {
    const node = document.createElement('div');
    node.dataset.legacyHeaderContext = '';
    node.className = styles.context;
    return node;
  });
  useLayoutEffect(() => {
    const placeholder = fallback.current;
    const root = placeholder?.parentElement;
    if (!placeholder || !root) return;
    const move = () => {
      const destination = legacyTitleRegion(root) ?? placeholder;
      if (host.parentElement !== destination) destination.prepend(host);
    };
    const observer = new MutationObserver(move);
    observer.observe(root, { childList: true, subtree: true });
    move();
    return () => {
      observer.disconnect();
      host.remove();
    };
  }, [host]);
  return (
    <LegacyBlock>
      <div ref={fallback} className={styles.fallback} />
      {children}
      {createPortal(
        <>
          <ContextTrail />
          <LegacyOwnership />
        </>,
        host,
      )}
    </LegacyBlock>
  );
}
