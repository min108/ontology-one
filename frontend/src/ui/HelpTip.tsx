import { Button, Tooltip } from 'antd';
import { CircleHelp } from 'lucide-react';
import { terms, type HelpTerm } from '../i18n/zh-CN';
export function HelpTip({ term }: { term: HelpTerm }) {
  return (
    <Tooltip title={terms.help[term]} trigger={['hover', 'focus']}>
      <Button autoInsertSpace={false} type="text" icon={<CircleHelp />} aria-label={terms.labels.help} />
    </Tooltip>
  );
}
