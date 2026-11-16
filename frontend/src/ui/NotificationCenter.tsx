import { Badge, Button, Drawer } from 'antd';
import { Bell } from 'lucide-react';
import { useState } from 'react';
import { terms } from '../i18n/zh-CN';
export interface NotificationItem {
  id: string;
  title: string;
  read: boolean;
  onOpen?: () => void;
}
export function NotificationCenter({
  items,
  onRead,
}: {
  items: NotificationItem[];
  onRead: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Badge count={items.filter((item) => !item.read).length}>
        <Button
          autoInsertSpace={false}
          aria-label={terms.actions.show_notifications}
          icon={<Bell />}
          onClick={() => setOpen(true)}
        />
      </Badge>
      <Drawer title={terms.labels.notifications} open={open} onClose={() => setOpen(false)}>
        <div aria-live="polite">
          {items.map((item) => (
            <p key={item.id}>
              <Button
                autoInsertSpace={false}
                type="link"
                onClick={() => {
                  onRead(item.id);
                  item.onOpen?.();
                }}
              >
                {item.title}
              </Button>
            </p>
          ))}
          {!items.length && <p>{terms.labels.no_notifications}</p>}
        </div>
      </Drawer>
    </>
  );
}
