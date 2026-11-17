import { Button } from 'antd';
import { RefreshCw } from 'lucide-react';
import IntegrationApplications, { type IntegrationPresentation } from '@legacy/pages/IntegrationApplications';
import { terms } from '../../../i18n/zh-CN';
import { FilterBar, FoldRow, PageHeader, PageLayout, TechnicalDetails } from '../../../ui';
import { LegacyBlock } from '../../../ui/LegacyBlock';
import styles from './IntegrationApplications.module.css';

const copy = terms.labels;
const presentation: IntegrationPresentation = {
  page: ({ content, information, dialogs, search, onSearch, onRefresh, onCreate, createDisabled }) => (
    <PageLayout
      variant="list"
      header={
        <PageHeader
          title={copy.r3_integration_title}
          lead={copy.r3_integration_lead}
          primaryAction={
            onCreate
              ? {
                  label: terms.actions.r3_integration_register,
                  onClick: onCreate,
                  disabled: createDisabled,
                }
              : undefined
          }
        />
      }
    >
      {onSearch && (
        <FilterBar value={search ?? ''} onChange={onSearch}>
          <Button onClick={onRefresh} icon={<RefreshCw aria-hidden="true" />}>
            {terms.actions.identity_refresh}
          </Button>
        </FilterBar>
      )}
      <LegacyBlock page={false} className={styles.content}>
        {content}
      </LegacyBlock>
      {information && (
        <FoldRow title={copy.r3_integration_information} summary={copy.r3_integration_information_summary}>
          <LegacyBlock page={false} className={styles.content}>
            {information}
          </LegacyBlock>
        </FoldRow>
      )}
      {dialogs}
    </PageLayout>
  ),
  technical: (children) => <TechnicalDetails>{children}</TechnicalDetails>,
  fold: (title, children) => (
    <FoldRow title={title} summary={copy.r3_integration_credentials_summary}>
      {children}
    </FoldRow>
  ),
};

/** Keep the original governed operations and token lifecycle behind a presentation-only adapter. */
export default function IntegrationApplicationsPage() {
  return <IntegrationApplications presentation={presentation} />;
}
