import { Drawer } from 'antd';
import type { ReactNode } from 'react';
import { terms } from '../i18n/zh-CN';
export function EvidenceDrawer({
  open,
  onClose,
  title = terms.labels.r3_ui_source,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <Drawer
      title={title}
      open={open}
      onClose={onClose}
      width={wide ? 'var(--zy-drawer-wide)' : 'var(--zy-drawer-width)'}
      footer={footer}
      destroyOnHidden
      keyboard
    >
      {children}
    </Drawer>
  );
}
