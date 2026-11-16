import type { ReactNode } from 'react';
export type ViewMode = 'simple' | 'professional';
export interface Action {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}
export interface NavigationItem {
  key: string;
  label: string;
  href: string;
  icon?: ReactNode;
  permission?: string;
}
