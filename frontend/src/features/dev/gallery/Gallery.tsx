import { Button, Card, Space } from 'antd';
import { Building2, FolderOpen, Home, LayoutGrid, Library, Settings2 } from 'lucide-react';
import { useState } from 'react';
import {
  AppShell,
  ConfirmCard,
  DataTable,
  DropOverlay,
  FilterBar,
  HelpTip,
  IdentityBar,
  KindTag,
  NavRail,
  NotificationCenter,
  Omnibox,
  PageHeader,
  StateBlock,
  StatusTag,
  TaskTray,
  Timeline,
  TopBar,
  ViewModeSwitch,
} from '../../../ui';
import type { ViewMode } from '../../../ui';
import { terms } from '../../../i18n/zh-CN';
import styles from './Gallery.module.css';
export default function Gallery() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<ViewMode>('simple');
  const [collapsed, setCollapsed] = useState(false);
  const [noticeRead, setNoticeRead] = useState(false);
  const [filter, setFilter] = useState('');
  const [feedback, setFeedback] = useState('');
  const nav = [
    { key: 'home', label: terms.labels.home, href: '#home', icon: <Home /> },
    { key: 'build', label: terms.labels.build, href: '#build', icon: <FolderOpen /> },
    { key: 'ontology', label: terms.labels.ontology, href: '#ontology', icon: <Library /> },
    { key: 'use', label: terms.labels.use, href: '#use', icon: <LayoutGrid /> },
    { key: 'govern', label: terms.labels.govern, href: '#govern', icon: <Building2 /> },
    { key: 'admin', label: terms.labels.admin, href: '#admin', icon: <Settings2 /> },
  ];
  const action = { label: terms.actions.create, onClick: () => setOpen(true) };
  return (
    <AppShell
      navigation={
        <NavRail
          items={nav}
          permissions={{}}
          currentKey="home"
          collapsed={collapsed}
          onCollapsedChange={setCollapsed}
        />
      }
      topBar={
        <TopBar
          workspaceName={terms.labels.sample_organization}
          onSearch={() => setOpen(true)}
          userMenu={<ViewModeSwitch value={mode} onChange={setMode} />}
        />
      }
      identity={
        <IdentityBar
          organization={terms.labels.sample_organization}
          role={terms.labels.sample_role}
          scope={terms.labels.sample_scope}
        />
      }
    >
      <div data-gallery="development-only" className={styles.gallery}>
        <PageHeader
          title={terms.labels.gallery}
          status={<StatusTag domain="origin" status="demo" />}
          primaryAction={action}
        />
        <p>{terms.labels.gallery_description}</p>
        <div className={styles.grid}>
          <Card title={terms.labels.status_title}>
            <Space wrap>
              {['draft', 'review', 'published', 'returned', 'archived'].map((status) => (
                <StatusTag key={status} status={status} />
              ))}
            </Space>
            <div className={styles.row}>
              <Space wrap>
                {['organization', 'material', 'metric', 'rule', 'skill', 'project'].map((kind) => (
                  <KindTag key={kind} kind={kind} />
                ))}
              </Space>
            </div>
            <HelpTip term="caliber" />
            <HelpTip term="view_mode" />
          </Card>
          <Card title={terms.labels.tools_title}>
            <Space>
              <Button onClick={() => setOpen(true)}>{terms.actions.search}</Button>
              <TaskTray
                items={[{ id: '1', title: terms.labels.sample_task, status: 'running', progress: 40 }]}
              />
              <NotificationCenter
                items={[{ id: '1', title: terms.labels.sample_notice, read: noticeRead }]}
                onRead={() => setNoticeRead(true)}
              />
            </Space>
            <p>{terms.labels.keyboard_hint}</p>
            <Timeline
              items={[
                {
                  id: '1',
                  title: terms.labels.version_current,
                  time: terms.labels.sample_time,
                  onCompare: () => setFeedback(terms.actions.compare),
                },
              ]}
            />
          </Card>
        </div>
        <Card title={terms.labels.table_title}>
          <FilterBar value={filter} onChange={setFilter} onReset={() => setFilter('')} />
          <DataTable
            columns={[
              { key: 'title', dataIndex: 'title', title: terms.labels.sample_name },
              { key: 'kind', title: terms.labels.sample_type, render: () => <KindTag kind="rule" /> },
              {
                key: 'status',
                title: terms.labels.sample_status,
                render: () => <StatusTag status="review" />,
              },
              { key: 'owner', dataIndex: 'owner', title: terms.labels.sample_owner },
            ]}
            dataSource={[
              { id: '1', title: terms.labels.sample_title, owner: terms.labels.sample_person },
            ].filter((item) => item.title.includes(filter))}
            rowKey="id"
            viewMode={mode}
            emptyAction={action}
          />
        </Card>
        <div className={styles.grid}>
          {(['loading', 'empty', 'forbidden', 'unconfigured', 'error', 'partial'] as const).map((state) => (
            <StateBlock
              key={state}
              state={state}
              action={{
                label: terms.actions[terms.states[state].action as keyof typeof terms.actions],
                onClick: () => setFeedback(terms.states[state].title),
              }}
              phase={terms.labels.sample_phase}
              requestId={state === 'error' ? 'demo-request-1' : undefined}
            />
          ))}
        </div>
        <ConfirmCard
          level="L2"
          summary={terms.labels.sample_rule}
          impact={terms.labels.sample_impact}
          gates={[terms.labels.sample_gate]}
          onConfirm={() => setFeedback(terms.actions.submit)}
          onCancel={() => setFeedback(terms.actions.cancel)}
        />
        <p role="status">{feedback}</p>
      </div>
      <Omnibox
        open={open}
        onOpenChange={setOpen}
        value={query}
        onSearch={setQuery}
        items={nav
          .filter((item) => item.label.includes(query))
          .map((item) => ({ id: item.key, label: item.label, group: terms.labels.command_group }))}
        onSelect={(item) => {
          setFeedback(item.label);
          setOpen(false);
        }}
      />
      <DropOverlay onFiles={(files) => setFeedback(files.map((file) => file.name).join('、'))} />
    </AppShell>
  );
}
