import type { HTMLAttributes } from 'react';
import styles from './LegacyBlock.module.css';

type LegacyBlockProps = HTMLAttributes<HTMLDivElement> & {
  variant?: 'block' | 'contents';
  page?: boolean;
};

export function LegacyBlock({
  variant = 'block',
  page = variant === 'block',
  className,
  children,
  ...props
}: LegacyBlockProps) {
  return (
    <div
      {...props}
      className={['legacy-root', styles[variant], className].filter(Boolean).join(' ')}
      data-legacy-page={page ? '' : undefined}
    >
      {children}
    </div>
  );
}
