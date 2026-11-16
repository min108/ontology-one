import { Segmented } from 'antd';
import { terms } from '../i18n/zh-CN';
import type { ViewMode } from './types';
export function ViewModeSwitch({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (value: ViewMode) => void;
}) {
  return (
    <Segmented<ViewMode>
      aria-label={terms.labels.view_mode}
      value={value}
      onChange={onChange}
      options={[
        { label: terms.actions.simple, value: 'simple' },
        { label: terms.actions.professional, value: 'professional' },
      ]}
    />
  );
}
