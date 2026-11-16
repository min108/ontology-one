import {useSearchParams} from 'react-router-dom';
import {useEffect, useMemo, useRef, useState, FormEvent} from 'react';
import {ReactFlow, Background, Controls, Handle, Position, MarkerType, Node, NodeProps, Edge} from '@xyflow/react';
import {ArrowRight, Building2, Check, ChevronLeft, ChevronRight, CircleHelp, Eye, Files, Layers3, Link2, LockKeyhole, Network, Plus, RefreshCw, Search, ShieldCheck, X} from 'lucide-react';
import {api, useApi, kindLabel, statusLabel} from '../api';
import {Badge, Card, Empty, ErrorPanel, FilterTabs, Loading, Modal, PageHeader, ParagraphText, Stat, useToast} from '../components/ui';
import {FederatedEntity, FederatedRelation, FederationDetail, FederationGraph, FederationOverview, FederationSearch, FederationTenant, federationEntityPath, visibilityLabel} from '../federation';
import '@xyflow/react/dist/style.css';
import './group.css';

type Selection = {tenantId: string; entityId: string};
const assetKinds = ['data', 'rule', 'skill', 'knowledge', 'material', 'dataset', 'project', 'task', 'collection'];
const accessLabel = (mode: FederationTenant['access_mode']) => ({own: '本机构', all: '授权读取', headquarters: '总部公开成果'}[mode]);
const number = (value: number | undefined) => Number(value || 0).toLocaleString('zh-CN');

function AvailabilityNote({partial, unavailable, tenants = []}: {partial?: boolean; unavailable?: string[]; tenants?: FederationTenant[]}) {
  if (!partial) return null;
  const names = (unavailable || []).map(id => tenants.find(t => t.tenant_id === id)?.tenant_name).filter(Boolean);
  return <div className="fg-partial" role="status"><CircleHelp size={17}/><span>部分授权机构暂时无法读取，本页仅显示已成功读取的结果。{names.length > 0 && `暂不可用：${names.join('、')}。`}</span></div>;
}

function ReadonlyDetail({selection, onClose}: {selection: Selection; onClose: () => void}) {
  const detail = useApi<FederationDetail>(federationEntityPath(selection.tenantId, selection.entityId));
  const entity = detail.data?.entity;
  const payload = entity?.payload || {};
  const sections = useMemo(() => {
    const result: {title: string; body: string}[] = [];
    if (Array.isArray(payload.article_sections)) {
      for (const item of payload.article_sections) {
        if (item && typeof item === 'object') {
          const section = item as Record<string, unknown>;
          const body = section.content || section.body || section.text;
          if (typeof body === 'string' && body.trim()) result.push({title: String(section.title || section.heading || '知识正文'), body});
        }
      }
    }
    for (const [key, label] of [['body', '内容正文'], ['content', '内容正文'], ['definition', '业务定义'], ['standard_definition', '标准定义'], ['objective', '业务目标'], ['scope', '适用范围'], ['method', '分析方法'], ['inputs', '输入要求'], ['outputs', '输出成果'], ['boundary', '使用边界']] as const) {
      const value = payload[key];
      if (typeof value === 'string' && value.trim() && !result.some(x => x.body === value)) result.push({title: label, body: value});
    }
    return result;
  }, [payload]);
  const relations = [...(detail.data?.relations.incoming || []).map(r => ({...r, incoming: true})), ...(detail.data?.relations.outgoing || []).map(r => ({...r, incoming: false}))];
  return <Modal title="成果详情 · 授权阅读" onClose={onClose} wide>
    {detail.loading ? <Loading/> : detail.error ? <ErrorPanel error={detail.error} reload={detail.reload}/> : entity && <div className="fg-detail">
      <div className="fg-detail-owner"><Building2 size={19}/><strong>{entity.tenant_name}</strong><Badge>{visibilityLabel(entity.visibility)}</Badge><Badge tone="blue">只读</Badge></div>
      <div className="fg-detail-heading"><Badge>{kindLabel(entity.kind)}</Badge><Badge>{statusLabel(entity.status)}</Badge><span>V{entity.version}</span></div>
      <h2>{entity.title}</h2><p className="fg-detail-summary">{entity.summary}</p>
      <dl className="fg-detail-facts"><div><dt>业务领域</dt><dd>{entity.domain || '未分类'}</dd></div><div><dt>成果编号</dt><dd>{entity.id}</dd></div><div><dt>所属机构</dt><dd>{entity.tenant_name}</dd></div><div><dt>可见范围</dt><dd>{visibilityLabel(entity.visibility)}</dd></div></dl>
      <div className="fg-detail-note"><ShieldCheck size={17}/><p>当前详情保留原机构归属与权限范围。集团阅读不会切换工作区，也不会改变成果内容或可见性。</p></div>
      {sections.map((section, index) => <section className="fg-detail-section" key={index}><h3>{section.title}</h3><ParagraphText text={section.body}/></section>)}
      {!sections.length && <p className="muted">当前成果暂无可展示的正文，摘要与归属信息见上方。</p>}
      <section className="fg-detail-section"><h3>可读取的关联 <small>{relations.length} 条</small></h3>{relations.length ? <div className="fg-detail-relations">{relations.map((r, index) => <div key={r.id + '-' + index}><span>{r.source_title || [r.source_tenant_name, r.source_entity_id].filter(Boolean).join(' · ') || '关联来源'}</span><span className="fg-relation-label">{r.label || r.type}<ArrowRight size={13}/></span><span>{r.target_title || [r.target_tenant_name, r.target_entity_id].filter(Boolean).join(' · ') || '关联目标'}</span>{r.cross_tenant && <Badge>跨机构</Badge>}</div>)}</div> : <p className="muted">当前权限范围内暂无关联。</p>}{!!detail.data?.hidden_reference_count && <p className="fg-detail-hidden">另有 {number(detail.data.hidden_reference_count)} 条引用受权限限制，相关内容未在本页展示。</p>}</section>
    </div>}
  </Modal>;
}

type RootData = {label: string; count: number; orgId: string};
type EntityData = {label: string; kind: string; visibility?: string; status?: string; entityId: string; tenantId: string; tenantName: string};
function TenantRootNode({data}: NodeProps<Node<RootData>>) {
  return <div className="fg-graph-root"><header><Building2 size={20}/><div><strong>{data.label}</strong><small>机构成果归属</small></div><b>{data.count} 项</b></header></div>;
}
function FederatedEntityNode({data}: NodeProps<Node<EntityData>>) {
  return <div className="fg-graph-entity"><Handle type="target" position={Position.Top} isConnectable={false}/><span className="fg-graph-kind" title={data.tenantName}>{kindLabel(data.kind)} · {data.tenantName}</span><strong>{data.label}</strong><small>{data.visibility === 'headquarters' ? <Eye size={11}/> : <LockKeyhole size={11}/>} {visibilityLabel(data.visibility || 'institution')} · {statusLabel(data.status || '')}</small><Handle type="source" position={Position.Bottom} isConnectable={false}/></div>;
}
const nodeTypes = {tenantRoot: TenantRootNode, federatedEntity: FederatedEntityNode};

function GroupGraph({data, onOpen}: {data: FederationGraph; onOpen: (selection: Selection) => void}) {
  const diagram = useMemo(() => {
    const nodes: Node[] = [];
    const roots = (data.roots || []).filter(root => data.nodes.some(node => node.parent_id === root.id));
    const groupWidth = 800, gap = 70;
    const direct = data.nodes.filter(n => !n.parent_id);
    direct.forEach((member, index) => nodes.push({id: member.id, type: 'federatedEntity', position: {x: (index % 6) * 256, y: Math.floor(index / 6) * 112}, style: {width: 238, height: 91}, data: {label: member.label, kind: member.kind, visibility: member.visibility, status: member.status, entityId: member.entity_id, tenantId: member.tenant_id, tenantName: member.tenant_name}, draggable: false}));
    let rowY = direct.length ? Math.ceil(direct.length / 6) * 112 + gap : 0, previousHeight = 0;
    roots.forEach((root, index) => {
      const members = data.nodes.filter(n => n.parent_id === root.id);
      const height = Math.max(205, 100 + Math.ceil(members.length / 3) * 112);
      if (index % 2 === 0 && index > 0) {rowY += previousHeight + gap; previousHeight = 0;}
      previousHeight = Math.max(previousHeight, height);
      nodes.push({id: root.id, type: 'tenantRoot', position: {x: (index % 2) * (groupWidth + gap), y: rowY}, style: {width: groupWidth, height}, data: {label: root.label, count: members.length, orgId: root.org_id}, selectable: false, draggable: false});
      members.forEach((member, childIndex) => nodes.push({id: member.id, type: 'federatedEntity', parentId: root.id, extent: 'parent', position: {x: 22 + (childIndex % 3) * 256, y: 82 + Math.floor(childIndex / 3) * 112}, style: {width: 238, height: 91}, data: {label: member.label, kind: member.kind, visibility: member.visibility, status: member.status, entityId: member.entity_id, tenantId: member.tenant_id, tenantName: member.tenant_name}, draggable: false}));
    });
    const ids = new Set(nodes.map(n => n.id));
    const edges: Edge[] = data.edges.filter(e => ids.has(e.source) && ids.has(e.target)).map(e => ({id: e.id, source: e.source, target: e.target, label: e.label, type: 'smoothstep', markerEnd: {type: MarkerType.ArrowClosed, width: 16, height: 16, color: e.cross_tenant ? '#a3763c' : '#8cabbc'}, style: {stroke: e.cross_tenant ? '#a3763c' : '#8cabbc', strokeWidth: e.cross_tenant ? 1.8 : 1, strokeDasharray: e.cross_tenant ? '6 4' : undefined}, labelStyle: {fontSize: 10, fill: '#596f80'}, labelBgStyle: {fill: '#f7fafc'}, labelBgPadding: [5, 3]}));
    return {nodes, edges};
  }, [data]);
  if (!diagram.nodes.length) return <Empty title="当前范围暂无图谱节点" description="可调整机构、类型或关键词，查看当前账户获授权的业务成果。"/>;
  return <div className="fg-graph-canvas"><ReactFlow nodes={diagram.nodes} edges={diagram.edges} nodeTypes={nodeTypes} fitView fitViewOptions={{padding: .14, maxZoom: 1}} minZoom={.08} maxZoom={1.5} nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} onNodeClick={(_, node) => {if (node.type === 'federatedEntity') {const d = node.data as EntityData; onOpen({tenantId: d.tenantId, entityId: d.entityId});}}} proOptions={{hideAttribution: true}} aria-label="按机构分组的集团本体图谱"><Background color="#cfdae4" gap={24}/><Controls showInteractive={false}/></ReactFlow></div>;
}

const linkTypes = [{value: 'references', label: '引用'}, {value: 'related_to', label: '业务相关'}, {value: 'uses_data', label: '使用数据'}, {value: 'implements_rule', label: '落实规则'}, {value: 'derived_from', label: '提炼自'}, {value: 'explains', label: '解释规则'}];
type LinkList = {items: FederatedRelation[]; can_create: boolean; can_review: boolean};

function EndpointPicker({title, tenants, selected, onSelect, excludedTenant}: {title: string; tenants: FederationTenant[]; selected: FederatedEntity | null; onSelect: (entity: FederatedEntity | null) => void; excludedTenant?: string}) {
  const [tenant, setTenant] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const queryString = new URLSearchParams({tenant_id: tenant, q: search, page: String(page), page_size: '6'});
  const options = useApi<FederationSearch>(tenant && tenant !== excludedTenant ? '/federation/search?' + queryString : '');
  useEffect(() => {if (tenant && tenant === excludedTenant) {setTenant(''); onSelect(null);}}, [excludedTenant]);
  return <section className="fg-endpoint"><h3>{title}</h3><label className="form-field">所属机构<select aria-label={title + '所属机构'} className="select" value={tenant} onChange={e => {setTenant(e.target.value); setPage(1); onSelect(null);}}><option value="">选择机构</option>{tenants.filter(t => t.provisioned).map(t => <option key={t.tenant_id} value={t.tenant_id} disabled={t.tenant_id === excludedTenant}>{t.tenant_name}</option>)}</select></label>{tenant && <><div className="fg-endpoint-search"><input aria-label={title + '搜索成果'} className="input" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') {e.preventDefault(); setSearch(query.trim()); setPage(1);}}} placeholder="成果名称或编号" maxLength={200}/><button type="button" className="btn" onClick={() => {setSearch(query.trim()); setPage(1);}}><Search size={14}/>查找</button></div>{options.loading ? <Loading variant="inline"/> : options.error ? <p className="fg-form-error" role="alert">{options.error}</p> : options.data?.items.length ? <div className="fg-endpoint-options">{options.data.items.map(entity => <button type="button" key={entity.node_id} className={selected?.id === entity.id && selected.tenant_id === entity.tenant_id ? 'selected' : ''} onClick={() => onSelect(entity)}><span><strong>{entity.title}</strong><small>{kindLabel(entity.kind)} · {entity.id} · {statusLabel(entity.status)}</small></span>{selected?.id === entity.id && <Check size={15}/>}</button>)}</div> : <p className="muted small">未找到可读取成果。</p>}<div className="fg-endpoint-pager"><span>共 {number(options.data?.total)} 项</span><button type="button" className="icon-btn" aria-label={title + '上一页'} disabled={page === 1} onClick={() => setPage(p => p - 1)}><ChevronLeft size={15}/></button><button type="button" className="icon-btn" aria-label={title + '下一页'} disabled={page * 6 >= (options.data?.total || 0)} onClick={() => setPage(p => p + 1)}><ChevronRight size={15}/></button></div></>}{selected && <div className="fg-endpoint-selected"><Check size={15}/><span>已选：{selected.tenant_name} · {selected.title}</span></div>}</section>;
}

function CrossInstitutionLinks({tenants, onOpen, onChanged}: {tenants: FederationTenant[]; onOpen: (selection: Selection) => void; onChanged: () => void}) {
  const links = useApi<LinkList>('/federation/links');
  const [creating, setCreating] = useState(false);
  const [source, setSource] = useState<FederatedEntity | null>(null);
  const [target, setTarget] = useState<FederatedEntity | null>(null);
  const [relationType, setRelationType] = useState('references');
  const [label, setLabel] = useState('引用');
  const [reason, setReason] = useState('');
  const [review, setReview] = useState<{link: FederatedRelation; status: 'confirmed' | 'rejected'} | null>(null);
  const [reviewReason, setReviewReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const toast = useToast();
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!links.data?.can_create || !source || !target || source.tenant_id === target.tenant_id) return;
    setBusy(true); setError('');
    try {
      await api('/federation/links', {method: 'POST', body: JSON.stringify({source_tenant_id: source.tenant_id, source_entity_id: source.id, target_tenant_id: target.tenant_id, target_entity_id: target.id, relation_type: relationType, label: label.trim(), reason: reason.trim()})});
      if (!alive.current) return;
      setCreating(false); setSource(null); setTarget(null); setReason(''); links.reload(); onChanged(); toast('跨机构关系已提交审核，确认后进入图谱');
    } catch (e: any) {if (alive.current) setError(e.message || '关系未能提交。');}
    finally {if (alive.current) setBusy(false);}
  }
  async function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!links.data?.can_review || !review?.link.link_id) return;
    setBusy(true); setError('');
    try {
      await api('/federation/links/' + encodeURIComponent(review.link.link_id), {method: 'PATCH', body: JSON.stringify({status: review.status, reason: reviewReason.trim()})});
      if (!alive.current) return;
      setReview(null); setReviewReason(''); links.reload(); onChanged(); toast(review.status === 'confirmed' ? '关系已确认，可在集团图谱中查看' : '关系已标记为不采纳');
    } catch (e: any) {if (alive.current) setError(e.message || '审核未能保存。');}
    finally {if (alive.current) setBusy(false);}
  }
  const endpoint = (relation: FederatedRelation, side: 'source' | 'target') => {
    const tenantId = relation[side + '_tenant_id' as 'source_tenant_id' | 'target_tenant_id'];
    const entityId = relation[side + '_entity_id' as 'source_entity_id' | 'target_entity_id'];
    const title = relation[side + '_title' as 'source_title' | 'target_title'] || entityId;
    const tenantName = relation[side + '_tenant_name' as 'source_tenant_name' | 'target_tenant_name'] || tenants.find(t => t.tenant_id === tenantId)?.tenant_name;
    return <button type="button" className="fg-link-endpoint" disabled={!tenantId || !entityId} onClick={() => tenantId && entityId && onOpen({tenantId, entityId})}><small><Building2 size={12}/>{tenantName || '所属机构'}</small><strong>{title || '业务成果'}</strong><span>阅读成果<ArrowRight size={12}/></span></button>;
  };
  return <Card className="fg-links-card"><div className="fg-results-heading"><div><h2>跨机构关系</h2><p>选择不同机构的业务成果建立候选关系，经审核确认后进入集团图谱。</p></div>{links.data?.can_create && <button type="button" className="btn btn-primary" onClick={() => {setCreating(true); setError('');}}><Plus size={15}/>建立候选关系</button>}</div>{links.loading ? <Loading/> : links.error ? <ErrorPanel error={links.error} reload={links.reload}/> : <>{!links.data?.can_create && !links.data?.can_review && <p className="fg-detail-note"><LockKeyhole size={15}/>当前账户可阅读已获授权的跨机构关系。</p>}{links.data?.items.length ? <div className="fg-links-list">{links.data.items.map(relation => <article className="fg-cross-link" key={relation.id}><div className="fg-cross-link-pair">{endpoint(relation, 'source')}<div className="fg-cross-link-label"><span>{relation.label}</span><ArrowRight size={22}/><Badge tone={relation.status === 'confirmed' ? 'green' : 'amber'}>{relation.status === 'confirmed' ? '已确认' : relation.status === 'rejected' ? '未采纳' : '待审核'}</Badge></div>{endpoint(relation, 'target')}</div>{links.data.can_review && relation.status === 'pending' && relation.link_id && <div className="fg-cross-link-review"><button type="button" className="btn" onClick={() => {setReview({link: relation, status: 'rejected'}); setReviewReason(''); setError('');}}><X size={14}/>不采纳</button><button type="button" className="btn btn-primary" onClick={() => {setReview({link: relation, status: 'confirmed'}); setReviewReason(''); setError('');}}><Check size={14}/>确认关系</button></div>}</article>)}</div> : <Empty title="暂无可读取的跨机构关系" description={links.data?.can_create ? '可选择两端成果，说明关系含义与依据，提交候选关系。' : '关系经总部授权人员建立并审核后，会在授权范围内显示。'}/>}</>}
    {creating && <Modal title="建立跨机构候选关系" onClose={() => !busy && setCreating(false)} wide><form className="fg-link-form" onSubmit={create}><p className="muted small">两端成果保持原机构归属。本操作建立候选关联，审核确认后才会进入集团图谱。</p><div className="fg-endpoint-grid"><EndpointPicker title="来源成果" tenants={tenants} selected={source} onSelect={setSource} excludedTenant={target?.tenant_id}/><EndpointPicker title="目标成果" tenants={tenants} selected={target} onSelect={setTarget} excludedTenant={source?.tenant_id}/></div><div className="grid-2"><label className="form-field">关系语义<select className="select" aria-label="关系语义" value={relationType} onChange={e => {setRelationType(e.target.value); setLabel(linkTypes.find(t => t.value === e.target.value)?.label || '');}}>{linkTypes.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label><label className="form-field">显示名称<input className="input" aria-label="关系显示名称" value={label} onChange={e => setLabel(e.target.value)} required maxLength={200}/></label></div><label className="form-field">关联依据<textarea className="input" aria-label="关联依据" rows={3} value={reason} onChange={e => setReason(e.target.value)} required maxLength={3000} placeholder="说明两端成果之间的业务关系、适用范围与依据。"/></label>{error && <p className="fg-form-error" role="alert">{error}</p>}<div className="fg-form-actions"><button type="button" className="btn" disabled={busy} onClick={() => setCreating(false)}>取消</button><button type="submit" className="btn btn-primary" disabled={busy || !source || !target || source.tenant_id === target.tenant_id || !label.trim() || !reason.trim()}>{busy ? '正在提交…' : '提交候选关系'}</button></div></form></Modal>}
    {review && <Modal title={review.status === 'confirmed' ? '确认跨机构关系' : '不采纳跨机构关系'} onClose={() => !busy && setReview(null)}><form className="fg-link-form" onSubmit={submitReview}><div className="fg-review-summary"><strong>{review.link.label}</strong><p>{review.link.source_tenant_name} · {review.link.source_title || review.link.source_entity_id}<ArrowRight size={14}/>{review.link.target_tenant_name} · {review.link.target_title || review.link.target_entity_id}</p></div><label className="form-field">审核意见<textarea className="input" aria-label="跨机构关系审核意见" rows={4} value={reviewReason} onChange={e => setReviewReason(e.target.value)} required maxLength={3000} placeholder="记录两端内容、关系语义与依据的核对结果。"/></label>{error && <p className="fg-form-error" role="alert">{error}</p>}<div className="fg-form-actions"><button type="button" className="btn" disabled={busy} onClick={() => setReview(null)}>取消</button><button type="submit" className="btn btn-primary" disabled={busy || !reviewReason.trim()}>{busy ? '正在保存…' : review.status === 'confirmed' ? '确认并加入图谱' : '保存不采纳意见'}</button></div></form></Modal>}
  </Card>;
}

export default function Group() {
  const overview = useApi<FederationOverview>('/federation/overview');
  const [tab, setTab] = useState('overview');
  const [tenantId, setTenantId] = useState('');
  const [kind, setKind] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [searchParams,setSearchParams]=useSearchParams();
  const parseSelection=()=>{const tenantId=searchParams.get('tenant_id')||'',entityId=searchParams.get('entity_id')||'';return tenantId&&entityId&&tenantId.length<=191&&entityId.length<=191?{tenantId,entityId}:null};
  const [selection, updateSelection] = useState<Selection | null>(parseSelection);
  useEffect(()=>{updateSelection(parseSelection())},[searchParams]);
  function setSelection(value:Selection|null){updateSelection(value);const parameters=new URLSearchParams(searchParams);if(value){parameters.set('tenant_id',value.tenantId);parameters.set('entity_id',value.entityId)}else{parameters.delete('tenant_id');parameters.delete('entity_id')}setSearchParams(parameters,{replace:true})}

  const parameters = new URLSearchParams({q: search, kind, tenant_id: tenantId, page: String(page), page_size: '20'});
  const results = useApi<FederationSearch>(tab === 'search' ? '/federation/search?' + parameters : '');
  const graphParameters = new URLSearchParams({q: search, kind, tenant_id: tenantId, limit: '150'});
  const graph = useApi<FederationGraph>(tab === 'graph' ? '/federation/graph?' + graphParameters : '');
  const tenants = overview.data?.tenants || [];
  useEffect(() => {setPage(1);}, [tenantId, kind, search]);
  const submit = (event: FormEvent) => {event.preventDefault(); setSearch(query.trim()); setPage(1);};
  function openInstitution(tenant: FederationTenant, view: string) {setTenantId(tenant.tenant_id); setQuery(''); setSearch(''); setKind(''); setPage(1); setTab(view);}
  function refresh() {overview.reload(); results.reload(); graph.reload();}
  if (overview.loading) return <div className="page"><Loading label="正在读取授权机构与集团成果…"/></div>;
  if (overview.error || !overview.data) return <div className="page"><ErrorPanel error={overview.error} reload={overview.reload}/></div>;
  const data = overview.data;
  const currentTenant = tenants.find(t => t.tenant_id === tenantId);
  return <div className="page fg-page">
    <PageHeader eyebrow="GROUP ONTOLOGY / 机构成果联阅" title="集团总览" description="在授权范围内查阅各机构的本体成果，保留原机构归属、来源与可见边界。" actions={<button type="button" className="btn" onClick={refresh}><RefreshCw size={15}/>刷新</button>}/>
    <section className="fg-scope"><div className="fg-scope-icon"><Building2 size={25}/></div><div><span>当前身份机构</span><strong>{data.scope.tenant_name}</strong><p>{data.scope.can_global_read ? '可按集团授权阅读各机构成果，包括机构内成果；跨机构阅读保持只读。' : '可见范围由当前账户和机构授权确定，公开成果仅在发布后进入共享范围。'}</p></div><Badge tone="blue"><ShieldCheck size={12}/>授权读取</Badge></section>
    <AvailabilityNote partial={data.partial} unavailable={data.unavailable_tenants} tenants={tenants}/>
    <div className="metric-grid fg-metrics"><Stat label="可读取机构" value={data.totals.tenants} detail="当前账户授权范围"/><Stat label="本体资产" value={data.totals.assets} detail="数据、规则、技能与知识"/><Stat label="业务对象" value={data.totals.entities} detail="按机构归属分别计数"/><Stat label="本体关系" value={data.totals.relations} detail="仅统计可读取关系"/></div>
    <FilterTabs label="查看方式" value={tab} onChange={setTab} options={[{value: 'overview', label: '机构总览', icon: <Building2 size={15}/>}, {value: 'search', label: '跨机构检索', icon: <Search size={15}/>}, {value: 'graph', label: '机构分组图谱', icon: <Network size={15}/>}, {value: 'links', label: '跨机构关系', icon: <Link2 size={15}/>} ]}/>
    {tab === 'links' ? <CrossInstitutionLinks tenants={tenants} onOpen={setSelection} onChanged={refresh}/> : tab === 'overview' ? <section className="fg-institutions" aria-label="授权机构目录">{tenants.length ? tenants.map(tenant => <Card className="fg-institution" key={tenant.tenant_id}><div className="fg-institution-top"><Building2 size={22}/><Badge tone={tenant.access_mode === 'own' ? 'blue' : 'neutral'}>{accessLabel(tenant.access_mode)}</Badge></div><h2>{tenant.tenant_name}</h2><p>{tenant.provisioned ? '独立机构工作区 · 成果保留原机构归属' : '机构工作区尚未完成准备'}</p><dl><div><dt>本体资产</dt><dd>{number(tenant.counts.assets)}</dd></div><div><dt>业务对象</dt><dd>{number(tenant.counts.entities)}</dd></div><div><dt>关联关系</dt><dd>{number(tenant.counts.relations)}</dd></div></dl><div className="fg-institution-actions"><button type="button" className="btn btn-ghost" disabled={!tenant.provisioned} onClick={() => openInstitution(tenant, 'search')}>查阅成果<ArrowRight size={14}/></button><button type="button" className="btn btn-ghost" disabled={!tenant.provisioned} onClick={() => openInstitution(tenant, 'graph')}><Network size={14}/>查看图谱</button></div></Card>) : <Card><Empty title="暂无可读取机构" description="机构目录将随账户授权显示。"/></Card>}</section> : <>
      <Card className="fg-query-card"><form className="fg-query" onSubmit={submit}><label className="fg-search"><Search size={17}/><input aria-label="搜索跨机构成果" value={query} onChange={e => setQuery(e.target.value)} placeholder="成果名称、摘要或业务关键词" maxLength={200}/></label><label className="fg-filter"><span>机构</span><select aria-label="筛选机构" className="select" value={tenantId} onChange={e => {setTenantId(e.target.value); setPage(1);}}><option value="">全部授权机构</option>{tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.tenant_name}</option>)}</select></label><label className="fg-filter"><span>对象类型</span><select aria-label="筛选对象类型" className="select" value={kind} onChange={e => {setKind(e.target.value); setPage(1);}}><option value="">全部类型</option>{assetKinds.map(k => <option value={k} key={k}>{kindLabel(k)}</option>)}</select></label><button type="submit" className="btn btn-primary">检索</button></form><div className="fg-query-note"><LockKeyhole size={14}/><span>{currentTenant ? `当前查阅：${currentTenant.tenant_name}。` : '当前查阅全部授权机构。'}结果归属与原机构可见性始终保留。</span></div></Card>
      {tab === 'search' ? <Card className="fg-results-card"><AvailabilityNote partial={results.data?.partial} unavailable={results.data?.unavailable_tenants} tenants={tenants}/>{results.loading ? <Loading label="正在检索授权机构成果…"/> : results.error ? <ErrorPanel error={results.error} reload={results.reload}/> : <><div className="fg-results-heading"><h2>可读取成果</h2><span>共 {number(results.data?.total)} 项</span></div>{results.data?.items.length ? <div className="fg-results">{results.data.items.map(entity => <button type="button" className="fg-result" key={entity.node_id || entity.tenant_id + ':' + entity.id} onClick={() => setSelection({tenantId: entity.tenant_id, entityId: entity.id})}><span className="fg-result-symbol"><Files size={21}/></span><div><div className="fg-result-meta"><Badge tone="blue">{kindLabel(entity.kind)}</Badge><span><Building2 size={12}/>{entity.tenant_name}</span><span>{visibilityLabel(entity.visibility)}</span></div><h3>{entity.title}</h3><p>{entity.summary || '暂无摘要'}</p><div className="fg-result-foot"><span>{entity.domain || '未分类'}</span><span>{statusLabel(entity.status)}</span><span>V{entity.version}</span></div></div><ChevronRight size={18}/></button>)}</div> : <Empty title="当前条件下没有可读取成果" description="可调整机构、类型或关键词。仅显示当前账户获授权的内容。"/>}<div className="fg-pagination"><span>第 {page} / {Math.max(1, Math.ceil((results.data?.total || 0) / 20))} 页</span><button type="button" className="btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}><ChevronLeft size={15}/>上一页</button><button type="button" className="btn" disabled={page * 20 >= (results.data?.total || 0)} onClick={() => setPage(p => p + 1)}>下一页<ChevronRight size={15}/></button></div></>}</Card> : <Card className="fg-graph-card"><div className="fg-results-heading"><div><h2>按机构归属展开本体</h2><p>有成果的部门与子公司按机构分组，总部业务成果直接展示；点击节点阅读。</p></div><Badge><Eye size={12}/>只读图谱</Badge></div><AvailabilityNote partial={graph.data?.partial} unavailable={graph.data?.unavailable_tenants} tenants={tenants}/>{graph.loading ? <Loading label="正在组织机构分组图谱…"/> : graph.error ? <ErrorPanel error={graph.error} reload={graph.reload}/> : graph.data && <><GroupGraph key={graphParameters.toString()} data={graph.data} onOpen={setSelection}/><div className="fg-graph-footer"><span><Layers3 size={14}/>{number(graph.data.roots.filter(root => graph.data!.nodes.some(node => node.parent_id === root.id)).length)} 个机构分组 · {number(graph.data.nodes.length)} 个成果节点 · {number(graph.data.edges.length)} 条关系</span><span><i className="fg-edge-key"/>机构内关系 <i className="fg-edge-key cross"/>跨机构关系</span></div>{graph.data.truncated && <div className="fg-partial" role="status"><CircleHelp size={17}/><span>当前图谱达到 150 个成果节点的显示上限。请选择具体机构、对象类型或关键词，逐步缩小范围；未显示内容仍可通过检索读取。</span></div>}<p className="fg-graph-explanation">每项成果保留机构归属，总部机构根节点不入图。暂无成果的机构仍可在机构总览中查阅。跨机构连线仅显示当前账户可读取的两端关系。</p></>}</Card>}
    </>}
    {selection && <ReadonlyDetail key={selection.tenantId + ':' + selection.entityId} selection={selection} onClose={() => setSelection(null)}/>}
  </div>;
}
