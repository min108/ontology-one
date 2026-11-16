import { Button, Timeline as AntTimeline } from 'antd';
import { terms } from '../i18n/zh-CN';
export interface TimelineItem {
  id: string;
  title: string;
  time: string;
  description?: string;
  onCompare?: () => void;
}
export function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <AntTimeline
      aria-label={terms.labels.history}
      items={items.map((item) => ({
        key: item.id,
        content: (
          <>
            <strong>{item.title}</strong>
            <p>{item.time}</p>
            {item.description && <p>{item.description}</p>}
            {item.onCompare && (
              <Button autoInsertSpace={false} type="link" onClick={item.onCompare}>
                {terms.actions.compare}
              </Button>
            )}
          </>
        ),
      }))}
    />
  );
}
