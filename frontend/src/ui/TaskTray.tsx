import { Badge, Button, Drawer, Progress } from 'antd';
import { ListTodo } from 'lucide-react';
import { useState } from 'react';
import { terms } from '../i18n/zh-CN';
import { StatusTag } from './StatusTag';
export interface TaskTrayItem {
  id: string;
  title: string;
  status: string;
  progress?: number;
  onOpen?: () => void;
}
export function TaskTray({ items }: { items: TaskTrayItem[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Badge count={items.filter((item) => item.status === 'waiting_user').length}>
        <Button
          autoInsertSpace={false}
          aria-label={terms.actions.show_tasks}
          icon={<ListTodo />}
          onClick={() => setOpen(true)}
        />
      </Badge>
      <Drawer title={terms.labels.tasks} open={open} onClose={() => setOpen(false)}>
        <div aria-live="polite">
          {items.map((item) => (
            <section key={item.id}>
              <h3>{item.title}</h3>
              <StatusTag domain="assistant" status={item.status} />
              {item.progress !== undefined && (
                <Progress percent={Math.min(100, Math.max(0, item.progress))} />
              )}
              {item.onOpen && (
                <Button autoInsertSpace={false} onClick={item.onOpen}>
                  {terms.actions.open}
                </Button>
              )}
            </section>
          ))}
          {!items.length && <p>{terms.labels.no_tasks}</p>}
        </div>
      </Drawer>
    </>
  );
}
