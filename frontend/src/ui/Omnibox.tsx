import { Button, Input, Modal, Spin } from 'antd';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type { InputRef } from 'antd';
import { terms } from '../i18n/zh-CN';
import styles from './Omnibox.module.css';
export interface OmniboxItem {
  id: string;
  label: string;
  description?: string;
  group?: string;
}
export interface OmniboxProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onSearch: (value: string) => void;
  items: OmniboxItem[];
  onSelect: (item: OmniboxItem) => void;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  footer?: ReactNode;
}
export function Omnibox({
  open,
  onOpenChange,
  value,
  onSearch,
  items,
  onSelect,
  loading = false,
  error,
  onRetry,
  footer,
}: OmniboxProps) {
  const [active, setActive] = useState(0);
  const input = useRef<InputRef>(null);
  const listId = useId();
  useEffect(() => {
    if (open) {
      setActive(0);
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [open, value]);
  const selected = Math.min(active, Math.max(0, items.length - 1));
  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      footer={footer ?? null}
      title={terms.actions.search}
      destroyOnHidden
      afterOpenChange={(visible) => {
        if (visible) input.current?.focus();
      }}
    >
      <Input
        ref={input}
        value={value}
        placeholder={terms.labels.search}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={items.length ? `${listId}-${selected}` : undefined}
        aria-label={terms.actions.search}
        onChange={(event) => onSearch(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActive((selected + 1) % Math.max(items.length, 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive((selected - 1 + items.length) % Math.max(items.length, 1));
          } else if (event.key === 'Enter' && items[selected]) {
            event.preventDefault();
            onSelect(items[selected]);
          } else if (event.key === 'Escape') {
            event.stopPropagation();
            onOpenChange(false);
          }
        }}
      />
      <p className={styles.hint}>{terms.labels.keyboard_hint}</p>
      {loading && <Spin aria-label={terms.labels.loading} />}
      {error && (
        <div role="alert">
          <p>{error}</p>
          {onRetry && (
            <Button aria-label={terms.actions.retry} onClick={onRetry}>
              {terms.actions.retry}
            </Button>
          )}
        </div>
      )}
      <ul id={listId} role="listbox" aria-label={terms.labels.search_results} className={styles.list}>
        {items.map((item, index) => (
          <li
            id={`${listId}-${index}`}
            key={item.id}
            role="option"
            aria-selected={selected === index}
            className={selected === index ? styles.active : styles.item}
            onMouseMove={() => setActive(index)}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onSelect(item)}
          >
            {item.group && <small>{item.group}</small>}
            <strong>{item.label}</strong>
            {item.description && <span>{item.description}</span>}
          </li>
        ))}
      </ul>
      {!loading && !error && !items.length && <p>{terms.labels.no_results}</p>}
    </Modal>
  );
}
