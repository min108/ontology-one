import {useEffect, useRef, useState} from 'react';
import {Building2, Check, Eye, LockKeyhole, RefreshCw} from 'lucide-react';
import {api, useApi, statusLabel} from '../api';
import {Badge, useToast} from './ui';
import {FederationDetail, Visibility, federationEntityPath, visibilityLabel} from '../federation';
import '../pages/group.css';

export default function EntityVisibility({entityId, tenantId, canEdit = false, onUpdated}: {
  entityId: string;
  tenantId: string;
  canEdit?: boolean;
  onUpdated?: () => void;
}) {
  const path = tenantId && entityId ? federationEntityPath(tenantId, entityId) : '';
  const detail = useApi<FederationDetail>(path);
  const [selection, setSelection] = useState<Visibility>('institution');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const toast = useToast();
  const identity = tenantId + ':' + entityId;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (detail.data) setSelection(detail.data.entity.visibility);
    setError('');
  }, [detail.data, entityId, tenantId]);
  if (!path) return null;
  if (detail.loading) return <aside className="entity-visibility" aria-busy="true"><Building2 size={17}/><span>正在核对机构归属与可见范围…</span></aside>;
  if (detail.error || !detail.data) return <aside className="entity-visibility entity-visibility-error" role="status"><span>机构归属信息暂时未能加载。{detail.error}</span><button type="button" className="btn btn-ghost" onClick={detail.reload}><RefreshCw size={14}/>重试</button></aside>;
  const {entity, can_set_visibility, visibility_options} = detail.data;
  const editable = canEdit && can_set_visibility && visibility_options.length > 1;
  async function save() {
    if (!editable || selection === entity.visibility) return;
    const savingIdentity = identity;
    setSaving(true); setError('');
    try {
      await api(path + '/visibility', {method: 'POST', body: JSON.stringify({visibility: selection})});
      if (!alive.current || currentIdentity.current !== savingIdentity) return;
      detail.reload(); onUpdated?.(); toast('成果可见范围已更新');
    } catch (e: any) {if (alive.current && currentIdentity.current === savingIdentity) setError(e.message || '可见范围未能更新，请重试。');}
    finally {if (alive.current && currentIdentity.current === savingIdentity) setSaving(false);}
  }
  return <aside className="entity-visibility" aria-label="成果归属与可见范围">
    <div className="entity-visibility-owner"><Building2 size={18}/><div><small>所属机构</small><strong>{entity.tenant_name}</strong></div></div>
    <div className="entity-visibility-scope">{entity.visibility === 'headquarters' ? <Eye size={17}/> : <LockKeyhole size={17}/>}<div><small>可见范围</small><strong>{visibilityLabel(entity.visibility)}</strong></div><Badge>{statusLabel(entity.status)}</Badge></div>
    {editable && <div className="entity-visibility-editor"><label className="sr-only" htmlFor={'visibility-' + entityId}>设置成果可见范围</label><select id={'visibility-' + entityId} className="select" value={selection} onChange={e => setSelection(e.target.value as Visibility)} disabled={saving}>{visibility_options.map(value => <option key={value} value={value}>{visibilityLabel(value)}</option>)}</select><button type="button" className="btn" onClick={save} disabled={saving || selection === entity.visibility}><Check size={15}/>{saving ? '正在保存…' : '保存范围'}</button></div>}
    <p className="entity-visibility-note">{entity.visibility === 'headquarters' || selection === 'headquarters' ? '总部公开范围仅对已发布成果生效。集团级授权读取仍按账户权限执行。' : '所属机构内按账户权限使用；集团级授权读取按总部权限执行。'}</p>
    {error && <p className="entity-visibility-error" role="alert">{error}</p>}
  </aside>;
}
