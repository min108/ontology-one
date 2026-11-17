import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { terms } from '../i18n/zh-CN';
export function BusinessDatePicker({
  value,
  onChange,
  disabled,
  id,
  'aria-label': ariaLabel,
}: {
  value?: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
  'aria-label'?: string;
}) {
  return (
    <DatePicker
      id={id}
      aria-label={ariaLabel ?? terms.labels.r3_ui_choose_date}
      value={value ? dayjs(value) : null}
      onChange={(value) => onChange(value ? value.format('YYYY-MM-DD') : '')}
      format="YYYY-MM-DD"
      placeholder={terms.labels.r3_ui_choose_date}
      disabled={disabled}
    />
  );
}
