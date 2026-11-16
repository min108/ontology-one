import { useState } from 'react';
import { Alert, App, Button, Card, Input, InputNumber, Tree, Tag } from 'antd';
import type { DataNode } from 'antd/es/tree';
import { useSessionStore } from '../../state/session';
import { useAddressState } from '../../app/addressState';
import { PageHeader } from '../../ui/PageHeader';
import { FilterBar } from '../../ui/FilterBar';
import { StateBlock } from '../../ui/StateBlock';
import {
  copy,
  action,
  EditDialog,
  Forbidden,
  QueryState,
  Timestamp,
  useIdentityQuery,
  useNarrowEditor,
} from '../identity/common';
import { excludedParents, validReason } from '../identity/validation';
import { manageOrg, type Org, type OrgManageInput, type List } from '../identity/contracts';
import styles from '../identity/Identity.module.css';
const categories: Org['category'][] = ['headquarters', 'resident', 'headquarters_department', 'subsidiary'];
const categoryLabel = (category: Org['category']) =>
  ({
    headquarters: copy.identity_org_headquarters,
    resident: copy.identity_org_resident,
    headquarters_department: copy.identity_org_department,
    subsidiary: copy.identity_org_subsidiary,
  })[category];
const statusLabel = (status: string) =>
  status === 'active'
    ? copy.identity_enabled
    : status === 'disabled'
      ? copy.identity_disabled
      : status === 'provisioning'
        ? copy.identity_provisioning
        : copy.identity_failed;
type Editing = { action: OrgManageInput['action']; target?: Org };
export default function OrganizationsPage() {
  const { session, permission_keys } = useSessionStore();
  if (!session?.user.is_superadmin || !permission_keys.includes('admin.org'))
    return (
      <section aria-label={copy.identity_orgs}>
        <Forbidden headquarters />
      </section>
    );
  return <OrganizationDirectory />;
}
function OrganizationDirectory() {
  const query = useIdentityQuery<List<Org>>('identity-orgs', '/v2/orgs');
  const { params, update } = useAddressState();
  const narrow = useNarrowEditor();
  const [selectedId, setSelectedId] = useState(''),
    [editing, setEditing] = useState<Editing | null>(null);
  const items = query.data?.items || [],
    selected = items.find((org) => org.id === selectedId) || items.find((org) => org.id === 'hq') || items[0],
    q = (params.get('q') || '').toLowerCase();
  const shown = new Set(
    items
      .filter((org) => (org.name + ' ' + org.id + ' ' + (org.short_name || '')).toLowerCase().includes(q))
      .map((org) => org.id),
  );
  for (const id of [...shown]) {
    let parent = items.find((org) => org.id === id)?.parent_id;
    const visited = new Set<string>();
    while (parent && !visited.has(parent)) {
      shown.add(parent);
      visited.add(parent);
      parent = items.find((org) => org.id === parent)?.parent_id;
    }
  }
  function nodes(parent: string | null, visited = new Set<string>()): DataNode[] {
    return items
      .filter(
        (org) =>
          shown.has(org.id) &&
          !visited.has(org.id) &&
          (parent === null
            ? !org.parent_id || !items.some((item) => item.id === org.parent_id)
            : org.parent_id === parent),
      )
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name, 'zh-CN'))
      .map((org) => ({
        key: org.id,
        title: (
          <span aria-label={org.name}>
            {org.name}
            {org.status !== 'active' && <Tag>{statusLabel(org.status)}</Tag>}
            {org.is_demo && <Tag>{copy.identity_demo_org}</Tag>}
          </span>
        ),
        children: nodes(org.id, new Set([...visited, org.id])),
      }));
  }
  return (
    <section aria-label={copy.identity_orgs} aria-busy={query.isPending} className={styles.page}>
      <PageHeader
        title={copy.identity_orgs}
        primaryAction={{
          label: action.identity_create_org,
          onClick: () => setEditing({ action: 'create' }),
          disabled: narrow || query.isPending || query.isError,
        }}
      />
      <p className={styles.help}>{copy.identity_org_no_inheritance}</p>
      {narrow && <Alert title={copy.identity_narrow} type="info" />}
      <FilterBar
        value={params.get('q') || ''}
        onChange={(value) => update({ q: value })}
        onReset={() => update({ q: null })}
      />
      <QueryState query={query}>
        {items.length ? (
          <div className={styles.grid}>
            <Card className={styles.tree}>
              <Tree
                key={q}
                defaultExpandAll
                blockNode
                aria-label={copy.identity_orgs}
                treeData={nodes(null)}
                selectedKeys={selected ? [selected.id] : []}
                onSelect={(keys) => {
                  if (keys[0]) setSelectedId(String(keys[0]));
                }}
              />
            </Card>
            {selected && (
              <Card title={selected.name}>
                <div className={styles.stack}>
                  <dl className={styles.facts}>
                    {[
                      [copy.identity_org_id, selected.id],
                      [copy.identity_org_category, categoryLabel(selected.category)],
                      [
                        copy.identity_org_parent,
                        items.find((org) => org.id === selected.parent_id)?.name || copy.identity_org_root,
                      ],
                      [copy.identity_org_short_name, selected.short_name || '—'],
                      [copy.identity_org_code, selected.org_code || '—'],
                      [copy.identity_org_sort, String(selected.sort_order || 0)],
                      [copy.identity_status, statusLabel(selected.status)],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt>{label}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                  {!['active', 'disabled'].includes(selected.status) && (
                    <Alert type="warning" title={copy.identity_org_offline_check} />
                  )}
                  {selected.status === 'disabled' && (
                    <p className={styles.help}>{copy.identity_org_enable_first}</p>
                  )}
                  {selected.disabled_at && (
                    <p>
                      <Timestamp value={selected.disabled_at} /> · {selected.disabled_reason}
                    </p>
                  )}
                  <div className={styles.actions}>
                    <Button
                      autoInsertSpace={false}
                      disabled={narrow || selected.status !== 'active'}
                      onClick={() => setEditing({ action: 'update', target: selected })}
                    >
                      {action.identity_edit_org}
                    </Button>
                    <Button
                      autoInsertSpace={false}
                      disabled={
                        narrow || selected.id === 'hq' || !['active', 'disabled'].includes(selected.status)
                      }
                      title={selected.id === 'hq' ? copy.identity_org_hq_protected : undefined}
                      onClick={() =>
                        setEditing({
                          action: selected.status === 'active' ? 'disable' : 'enable',
                          target: selected,
                        })
                      }
                    >
                      {selected.status === 'active' ? action.identity_disable : action.identity_enable}
                    </Button>
                  </div>
                </div>
              </Card>
            )}
          </div>
        ) : (
          <StateBlock
            state="empty"
            description={copy.identity_no_orgs}
            action={{
              label: action.identity_create_org,
              onClick: () => setEditing({ action: 'create' }),
              disabled: narrow || query.isPending || query.isError,
            }}
          />
        )}
      </QueryState>
      {editing && (
        <OrganizationDialog
          editing={editing}
          items={items}
          narrow={narrow}
          onClose={() => setEditing(null)}
          onSaved={() => void query.refetch()}
        />
      )}
    </section>
  );
}
function OrganizationDialog({
  editing,
  items,
  narrow,
  onClose,
  onSaved,
}: {
  editing: Editing;
  items: Org[];
  narrow: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { target, action: operation } = editing;
  const reload = useSessionStore((state) => state.reload);
  const { message } = App.useApp();
  const [form, setForm] = useState({
    name: target?.name || '',
    category: target?.category || ('subsidiary' as Org['category']),
    parent_id: target ? target.parent_id || '' : 'hq',
    short_name: target?.short_name || '',
    org_code: target?.org_code || '',
    sort_order: target?.sort_order || 0,
    reason: '',
  });
  const excluded = excludedParents(items, target?.id || '');
  const editingFields = operation === 'create' || operation === 'update';
  const valid = editingFields
    ? !!form.name.trim() &&
      form.name.trim().length <= 200 &&
      categories.includes(form.category) &&
      Number.isInteger(form.sort_order) &&
      form.sort_order >= 0 &&
      form.sort_order <= 1000000 &&
      !excluded.has(form.parent_id) &&
      (!form.parent_id || items.some((org) => org.id === form.parent_id && org.status === 'active'))
    : operation === 'disable'
      ? validReason(form.reason, true)
      : true;
  const title =
    operation === 'create'
      ? copy.identity_org_create
      : operation === 'update'
        ? copy.identity_org_edit
        : operation === 'disable'
          ? copy.identity_org_disable
          : copy.identity_org_enable;
  const impact =
    operation === 'create'
      ? copy.identity_org_create_impact
      : operation === 'update'
        ? copy.identity_org_edit_impact
        : operation === 'disable'
          ? copy.identity_org_disable_impact
          : copy.identity_org_enable_impact;
  async function save() {
    if (!valid) throw new Error('Invalid organization');
    await manageOrg({
      action: operation,
      ...(target ? { org_id: target.id } : {}),
      ...(editingFields
        ? {
            name: form.name.trim(),
            category: form.category,
            parent_id: form.parent_id || null,
            short_name: form.short_name.trim(),
            org_code: form.org_code.trim() || null,
            sort_order: form.sort_order,
          }
        : { ...(operation === 'disable' ? { reason: form.reason.trim() } : {}) }),
    });
    onSaved();
    await reload();
    void message.success(copy.identity_org_saved);
  }
  return (
    <EditDialog
      title={title}
      impact={impact}
      summary={title + ' · ' + (target?.name || form.name)}
      value={form}
      valid={valid}
      onSave={save}
      onClose={onClose}
      narrow={narrow}
    >
      <div className={styles.form}>
        {editingFields ? (
          <>
            <label className={styles.field} htmlFor="identity-org-name">
              {copy.identity_org_name}
              <Input
                id="identity-org-name"
                aria-label={copy.identity_org_name}
                maxLength={200}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </label>
            <label className={styles.field} htmlFor="identity-org-category">
              {copy.identity_org_category}
              <select
                id="identity-org-category"
                aria-label={copy.identity_org_category}
                className={styles.control}
                value={form.category}
                disabled={target?.id === 'hq'}
                onChange={(event) => setForm({ ...form, category: event.target.value as Org['category'] })}
              >
                {categories.map((category) => (
                  <option
                    key={category}
                    value={category}
                    disabled={category === 'headquarters' && target?.id !== 'hq'}
                  >
                    {categoryLabel(category)}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field} htmlFor="identity-org-parent">
              {copy.identity_org_parent}
              <select
                id="identity-org-parent"
                aria-label={copy.identity_org_parent}
                className={styles.control}
                value={target?.id === 'hq' ? '' : form.parent_id}
                disabled={target?.id === 'hq'}
                onChange={(event) => setForm({ ...form, parent_id: event.target.value })}
              >
                <option value="">{copy.identity_org_root}</option>
                {items
                  .filter((org) => org.status === 'active' && !excluded.has(org.id))
                  .map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className={styles.field} htmlFor="identity-org-short">
              {copy.identity_org_short_name}
              <Input
                id="identity-org-short"
                aria-label={copy.identity_org_short_name}
                maxLength={120}
                value={form.short_name}
                onChange={(event) => setForm({ ...form, short_name: event.target.value })}
              />
            </label>
            <label className={styles.field} htmlFor="identity-org-code">
              {copy.identity_org_code}
              <Input
                id="identity-org-code"
                aria-label={copy.identity_org_code}
                maxLength={120}
                value={form.org_code}
                onChange={(event) => setForm({ ...form, org_code: event.target.value })}
              />
            </label>
            <label className={styles.field} htmlFor="identity-org-sort">
              {copy.identity_org_sort}
              <InputNumber
                id="identity-org-sort"
                aria-label={copy.identity_org_sort}
                min={0}
                precision={0}
                max={1000000}
                value={form.sort_order}
                onChange={(value) => setForm({ ...form, sort_order: value ?? 0 })}
              />
            </label>
          </>
        ) : (
          <>
            <p>{impact}</p>
            {operation === 'disable' && (
              <label className={styles.field} htmlFor="identity-org-reason">
                {copy.identity_org_reason}
                <Input.TextArea
                  id="identity-org-reason"
                  aria-label={copy.identity_org_reason}
                  rows={3}
                  maxLength={500}
                  value={form.reason}
                  onChange={(event) => setForm({ ...form, reason: event.target.value })}
                />
                <span className={styles.help}>{copy.identity_reason_rule}</span>
              </label>
            )}
          </>
        )}
      </div>
    </EditDialog>
  );
}
