import { Select } from 'antd';
import type { ApplicabilityOption } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';

export function AuthoritySubjectSelect({
  value,
  options,
  disabled,
  onChange,
}: {
  value: string[];
  options: ApplicabilityOption[];
  disabled?: boolean;
  onChange: (value: string[]) => void;
}) {
  return (
    <Select
      mode="multiple"
      aria-label={terms.labels.r4a1_legacy_subject}
      showSearch
      optionFilterProp="label"
      allowClear
      value={value}
      disabled={disabled}
      placeholder={terms.labels.r4a1_legacy_subject_hint}
      options={options
        .filter((option) => option.kind === 'organization' || option.kind === 'person')
        .map((option) => ({ value: option.id, label: option.title }))}
      onChange={onChange}
    />
  );
}

export function SubjectSelect({
  value,
  options,
  disabled,
  onChange,
}: {
  value: string;
  options: ApplicabilityOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Select
      aria-label={terms.labels.r4a1_legacy_subject}
      showSearch
      optionFilterProp="label"
      allowClear
      value={value || undefined}
      disabled={disabled}
      placeholder={terms.labels.r4a1_legacy_subject_hint}
      options={options.map((option) => ({ value: option.id, label: option.title }))}
      onChange={(next) => onChange(next || '')}
    />
  );
}
