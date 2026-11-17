import { Button } from 'antd';
import { Check } from 'lucide-react';
import type { ReactNode } from 'react';
import { terms } from '../i18n/zh-CN';
import type { Action } from './types';
import styles from './Foundation.module.css';
export function NextStepBar({
  current,
  description,
  action,
  complete = false,
  full = false,
  onStep,
}: {
  current: number;
  description?: ReactNode;
  action?: Action;
  complete?: boolean;
  full?: boolean;
  onStep?: (step: number) => void;
}) {
  const labels = [
    terms.actions.r3_ui_step1,
    terms.actions.r3_ui_step2,
    terms.actions.r3_ui_step3,
    terms.actions.r3_ui_step4,
    terms.actions.r3_ui_step5,
  ];
  return (
    <nav className={`${styles.next} ${full ? styles.fullSteps : ''}`} aria-label={terms.labels.r3_ui_next}>
      <ol>
        {labels.map((label, index) => (
          <li
            key={label}
            className={index + 1 === current ? styles.activeStep : index + 1 < current ? styles.doneStep : ''}
            aria-current={index + 1 === current ? 'step' : undefined}
          >
            {onStep && index + 1 < current ? (
              <button aria-label={label} onClick={() => onStep(index + 1)}>
                <Check aria-hidden="true" />
              </button>
            ) : (
              <span className={styles.step}>
                {index + 1 < current ? <Check aria-hidden="true" /> : index + 1}
              </span>
            )}
            {(full || index + 1 === current) && <span>{label}</span>}
          </li>
        ))}
      </ol>
      {description && <span className={styles.nextDescription}>{description}</span>}
      {action && (
        <Button
          type={complete ? 'primary' : 'text'}
          onClick={action.onClick}
          disabled={action.disabled}
          loading={action.loading}
        >
          {action.label}
        </Button>
      )}
    </nav>
  );
}
