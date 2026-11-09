import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Database, Download, FileSpreadsheet, FolderOpen, Layers, Search, Scale, Upload, Workflow, X } from 'lucide-react';
import { api, useApi } from '../api';
import { Badge, Empty, FilterTabs, Loading, Modal, PageHeader } from '../components/ui';
import './assets.css';
import { entityHref } from '../domain';
import { KnowledgeLibrary } from './Knowledge';
import './catalog.css';

export type AssetEntity = {
  id: string; kind: string; title: string; summary?: string; domain?: string; org_id?: string;
  status?: string; source_kind?: string; version?: number; created_at?: string; updated_at?: string;
  payload?: Record<string, any>; relations?: { incoming?: any[]; outgoing?: any[] }; relation_counts?: { incoming: number; outgoing: number };
};

export const assetKinds = [
  { kind: 'data', name: '数据资产', short: '数据', icon: Database, prefix: 'D', description: '统一数据含义，建立可核验的业务口径', color: 'blue' },
  { kind: 'rule', name: '规则资产', short: '规则', icon: Scale, prefix: 'R', description: '从制度原文到适用条件与管理要求', color: 'red' },
  { kind: 'algorithm', name: '算法资产', short: '算法', icon: Workflow, prefix: 'A', description: '让分析方法拥有清晰的输入和输出', color: 'violet' },
  { kind: 'knowledge', name: '知识资产', short: '知识', icon: BookOpen, prefix: 'K', description: '沉淀背景、辨析、核查路径与经验', color: 'green' },
];

export const readableStatus = (status?: string) => ({ draft: '草稿', review: '待审核', approved: '审核通过', published: '已发布', returned: '已退回', archived: '已归档', confirmed: '已确认', candidate: '候选参考', pending: '待办理', in_progress: '进行中', completed: '已完成', open: '待核查', closed: '已闭环', active: '使用中', invalid: '待核验', source_verified: '来源文本一致', demo: '演示关联', rejected: '已排除' }[status || ''] || status || '待完善');
export const sourceLabel = (source?: string) => ({ imported: '存量导入', regulation: '法规原文', demo: '演示资产', user: '用户资料' }[source || ''] || '待标注来源');
export const kindLabel = (kind?: string) => ({ data: '数据资产', rule: '规则资产', algorithm: '算法资产', knowledge: '知识资产', material: '材料', dataset: '业务台账', project: '业务专题', task: '工作任务', organization: '组织单位', person: '任职人员', case: '业务案例', issue: '待核查问题' }[kind || ''] || '业务对象');
export function plainText(value: any): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(plainText).filter(Boolean).join('\n');
  const primary = value.content ?? value.text ?? value.description ?? value.label ?? value.name ?? value.title ?? value.value;
  if (primary !== undefined) return plainText(primary);
  return Object.entries(value).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v)).map(([, v]) => plainText(v)).join(' · ');
}
export function assetField(entity: AssetEntity, ...keys: string[]) {
  const payload = entity.payload || {};
  for (const key of keys) if (payload[key] !== undefined && payload[key] !== null && payload[key] !== '') return payload[key];
  return '';
}
export function assetTopic(entity: AssetEntity): string {
  return plainText(entity.payload?.topic_label || entity.payload?.business_theme || entity.payload?.topic);
}
export function assetScenario(entity: AssetEntity): string {
  return plainText(entity.payload?.scenario_label);
}
export function assetRoute(id: string, kind?: string) {
  return entityHref(id,kind);
}

const number = (n: number) => new Intl.NumberFormat('zh-CN').format(n);
const statusChoices = ['draft', 'review', 'approved', 'published', 'returned', 'archived'];
const perPage = 20;

export type DataInstanceSummary={standards:number;units:number;datasets:number;sheets:number;fields:number;records:number;cells:number;nonempty:number|null;empty:number|null;demo_records:number;user_records:number;value_counts_available?:boolean};
export function AssetPreview({ entity,instances }: { entity: AssetEntity; instances?: DataInstanceSummary|null }) {
  const meta = assetKinds.find(x => x.kind === entity.kind) || assetKinds[0];
  const Icon = meta.icon;
  const preview = plainText(assetField(entity, entity.kind === 'data' ? 'definition' : entity.kind === 'algorithm' ? 'scenario' : entity.kind === 'knowledge' ? 'context' : 'content', '业务含义', '适用场景', 'original_text')) || entity.summary || '业务内容待补充';
  const facts = entity.kind === 'data' ? [
    ['形态', assetField(entity, 'data_form', '数据形态')], ['归属', assetField(entity, 'ownership', '资产归属')], ['分级', assetField(entity, 'sensitivity', '敏感级别')],
  ] : entity.kind === 'rule' ? [
    ['类型', assetField(entity, 'rule_type', '规则类型')], ['来源', assetField(entity, 'asset_source')], ['依据', assetField(entity, 'basis', 'authority', '依据法规')], ['条款', assetField(entity, 'clause', '条款号')],
  ] : entity.kind === 'algorithm' ? [
    ['方法类型', assetField(entity, 'algorithm_type', '算法类型')], ['主题', assetField(entity, 'category', '业务主题')], ['成熟度', assetField(entity, 'maturity') || '方法资产'],
  ] : [
    ['知识类型', assetField(entity, 'knowledge_type') || '业务经验'], ['主题', assetField(entity, 'category') || entity.domain], ['版本', `V${entity.version || 1}`],
  ];
  return <Link className={`asset-result asset-result-${meta.color}`} to={assetRoute(entity.id, entity.kind)+(instances?.records?'#data-instances':'')}>
    <div className={`asset-result-icon asset-color-${meta.color}`}><Icon size={21} strokeWidth={1.6} /></div>
    <div className="asset-result-main">
      <div className="asset-result-top"><span className="asset-code">{entity.id}</span><span className={`asset-source asset-source-${entity.source_kind}`}>{sourceLabel(entity.source_kind)}</span><span className={`asset-state asset-state-${entity.status}`}>{readableStatus(entity.status)}</span></div>
      <h3>{entity.title}</h3>{(assetTopic(entity) || assetScenario(entity)) && <div className="asset-business-tags">{assetTopic(entity) && <span className="asset-topic-label">{assetTopic(entity)}</span>}{assetScenario(entity) && <span className="asset-scenario-label">{assetScenario(entity)}</span>}</div>}
      <p className="asset-result-excerpt">{preview}</p>
      {entity.kind==='data'&&instances!==undefined&&<div className={'asset-instance-summary '+(instances?.records?'has-records':'')}>{instances?.records?<><Database size={13}/><strong>{number(instances.records)} 条原始记录</strong><span>{instances.units} 个单位 · {instances.datasets} 份台账</span><small>{instances.user_records?'含用户接入数据':'演示数据实例'}</small><b>查看数据实例<ArrowUpRight size={12}/></b></>:<><Database size={13}/><span>暂无已确认数据实例</span><small>可从数据导入建立字段对应</small></>}</div>}
      <div className="asset-result-facts">{facts.map(([label, value]) => <span key={String(label)} title={plainText(value)}><b>{label}</b>{plainText(value) || '待补充'}</span>)}</div>
    </div>
    <div className="asset-result-side"><span>{entity.domain || '未分类'}</span><ArrowUpRight size={18} /></div>
  </Link>;
}

type CatalogNode={id:string;label:string;axis:string;path:Record<string,string>;count:number;children:CatalogNode[];depth:number};
function CatalogBranch({node,params,choose}:{node:CatalogNode;params:URLSearchParams;choose:(node:CatalogNode)=>void}){
 const selected=Object.entries(node.path).every(([k,v])=>params.get(k)===v);
 const [open,setOpen]=useState(selected);
 useEffect(()=>{if(selected)setOpen(true)},[selected]);
 return <li><div className={'catalog-tree-row '+(selected?'selected':'')}><button className="catalog-expander" aria-label={(open?'收起':'展开')+node.label} aria-expanded={open} disabled={!node.children.length} onClick={()=>setOpen(!open)}>{node.children.length>0&&<ChevronRight size={13} className={open?'rotated':''}/>}</button><button className="catalog-tree-select" onClick={()=>choose(node)} title={node.label}><span>{node.label}</span><small>{number(node.count)}</small></button></div>{open&&node.children.length>0&&<ul>{node.children.map(child=><CatalogBranch key={child.id} node={child} params={params} choose={choose}/>)}</ul>}</li>
}
const branchKeys=['domain','category','subcategory','authority','basis','version_key'];
type CatalogSnapshot={query:string;params:string;kind:string;tree:{nodes:CatalogNode[];total:number;axes:string[]};result:any};
function useCatalogSnapshot(params:URLSearchParams,kind:string,revision:number){
 const query=useMemo(()=>{const p=new URLSearchParams(params);p.delete('via');p.set('kind',kind);p.set('page_size','20');if(revision)p.set('_refresh',String(revision));p.sort();return p.toString()},[params,kind,revision]);
 const caches=useRef({snapshots:new Map<string,CatalogSnapshot>(),trees:new Map<string,CatalogSnapshot['tree']>()});
 const [latest,setLatest]=useState<CatalogSnapshot|null>(null),[failure,setFailure]=useState({query:'',message:''}),[retry,setRetry]=useState(0);
 const cached=caches.current.snapshots.get(query),snapshot=cached||latest;
 useEffect(()=>{
  if(kind==='knowledge')return;
  if(cached){setLatest(cached);return;}
  const controller=new AbortController();let live=true;setFailure({query:'',message:''});
  const treeParams=new URLSearchParams(query);[...branchKeys,'page','page_size','display'].forEach(key=>treeParams.delete(key));const treeKey=treeParams.toString();
  const treeRequest=caches.current.trees.has(treeKey)?Promise.resolve(caches.current.trees.get(treeKey)!):api<CatalogSnapshot['tree']>('/asset-catalog/tree?'+treeKey,{signal:controller.signal});
  Promise.all([treeRequest,api('/asset-catalog/items?'+query,{signal:controller.signal})]).then(([tree,result])=>{
   if(!live)return;
   const next={query,params:query,kind,tree,result};caches.current.trees.set(treeKey,tree);caches.current.snapshots.set(query,next);
   if(caches.current.snapshots.size>36)caches.current.snapshots.delete(caches.current.snapshots.keys().next().value!);
   if(caches.current.trees.size>16)caches.current.trees.delete(caches.current.trees.keys().next().value!);
   setLatest(next);
  }).catch(error=>{if(live&&error.name!=='AbortError')setFailure({query,message:error.message})});
  return()=>{live=false;controller.abort()};
 },[query,kind,retry]);
 return {snapshot,busy:kind!=='knowledge'&&snapshot?.query!==query,error:failure.query===query?failure.message:'',retry:()=>setRetry(n=>n+1)};
}
function CatalogInitial(){return <div className="catalog-initial" aria-busy="true"><aside><strong>分类目录</strong>{Array.from({length:8},(_,i)=><div className="catalog-skeleton" key={i}/>)}</aside><div><Loading variant="inline" label="正在载入资产目录…"/><div className="catalog-skeleton title"/><div className="catalog-skeleton"/><div className="catalog-group-grid">{Array.from({length:6},(_,i)=><div className="catalog-skeleton cards" key={i}/>)}</div></div></div>}
type ImportResult={message:string;total:number;created:number;updated:number;filename:string;material_id:string};
function AssetImportControl({kind,onImported}:{kind:'data'|'rule'|'algorithm';onImported:()=>void}){
 const input=useRef<HTMLInputElement>(null),[busy,setBusy]=useState(false),[open,setOpen]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<ImportResult|null>(null);
 const name=assetKinds.find(x=>x.kind===kind)!.name;
 async function importFile(file?:File){
  if(!file||busy)return;
  if(!/\.xlsx$/i.test(file.name)){setError('请上传 .xlsx 文件；旧版 .xls 请先另存为 .xlsx。');setResult(null);setOpen(true);return}
  if(file.size>30*1024*1024){setError('文件超过 30 MB，请按业务领域分批导入。');setResult(null);setOpen(true);return}
  setOpen(true);setBusy(true);setError('');setResult(null);
  const form=new FormData();form.append('file',file);
  try{const r=await api<ImportResult>('/asset-import/'+kind,{method:'POST',body:form});setResult(r);onImported()}
  catch(e:any){setError(e?.message||'导入未完成，请检查文件后重试。')}
  finally{setBusy(false);if(input.current)input.current.value=''}
 }
 return <><a className="btn btn-ghost" href={'/api/asset-import/template/'+kind} download><Download size={15}/>下载标准模板</a><button className="btn btn-primary" disabled={busy} onClick={()=>{setError('');setResult(null);input.current?.click()}}><Upload size={15}/>{busy?'正在导入…':'Excel 一键导入'}</button><input ref={input} className="asset-import-input" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label={`选择${name}台账`} onChange={e=>importFile(e.target.files?.[0])}/>
 {open&&<Modal title={`${name} · Excel 台账导入`} onClose={()=>!busy&&setOpen(false)}><div className="asset-import-dialog"><div className="asset-import-guide"><FileSpreadsheet size={22}/><div><strong>保留原表字段即可直接导入</strong><p>支持系统标准模板与多工作表台账。整批校验通过后一次写入；同编号更新保留历史版本，来源工作表和行号随资产保存。</p></div></div>{busy&&<Loading label={`正在校验并导入${name}，请勿关闭页面…`}/>} {result&&<div className="asset-import-success"><CheckCircle2 size={24}/><div><h3>台账导入完成</h3><p>{result.message}</p><dl><div><dt>本次读取</dt><dd>{number(result.total)} 项</dd></div><div><dt>新增</dt><dd>{number(result.created)} 项</dd></div><div><dt>同编号更新</dt><dd>{number(result.updated)} 项</dd></div></dl><Link to={'/materials/'+encodeURIComponent(result.material_id)}>查看已保存的来源台账<ArrowUpRight size={14}/></Link></div></div>}{error&&<div className="asset-import-error" role="alert"><strong>本次未导入任何资产</strong><p>{error}</p><small>请按提示修正工作簿；也可以重新下载标准空模板填写。</small></div>}<div className="asset-import-footer"><a className="btn btn-ghost" href={'/api/asset-import/template/'+kind} download><Download size={14}/>下载空模板</a>{!busy&&<button className="btn btn-primary" onClick={()=>input.current?.click()}><Upload size={14}/>{result||error?'重新选择台账':'选择台账'}</button>}{!busy&&(result||error)&&<button className="btn btn-ghost" onClick={()=>setOpen(false)}>完成</button>}</div></div></Modal>}
 </>;
}
export function Assets(){
 const [params,setParams]=useSearchParams();
 const kind=assetKinds.some(x=>x.kind===params.get('kind'))?params.get('kind')!:'data';
 const [importRevision,setImportRevision]=useState(0);
 const catalog=useApi<any>('/catalog');const instanceData=useApi<{summary:DataInstanceSummary;by_standard:Record<string,DataInstanceSummary>;scope_note:string}>('/data-instances/summary');const {snapshot,busy,error,retry}=useCatalogSnapshot(params,kind,importRevision);
 const requestedName=assetKinds.find(x=>x.kind===kind)!.name;
 const imported=()=>{catalog.reload();instanceData.reload();setImportRevision(n=>n+1);const p=new URLSearchParams(params);p.set('kind',kind);p.set('display','all');p.delete('page');setParams(p)};
 return <div className="page assets-page"><PageHeader eyebrow="业务资产 · BUSINESS ASSETS" title="业务资产" description="沿业务分类查找标准、制度要求与分析方法，在完整经验中理解问题与边界。" actions={<>{kind!=='knowledge'&&<AssetImportControl kind={kind as 'data'|'rule'|'algorithm'} onImported={imported}/>}<Link className="btn btn-ghost" to="/graph"><Layers size={16}/>查看资产关联<ArrowUpRight size={15}/></Link></>}/>
 <div className="asset-kind-grid" role="tablist" aria-label="资产类型">{assetKinds.map(item=>{const Icon=item.icon;const count=catalog.data?.kinds?.find((x:any)=>x.kind===item.kind)?.count;return <button role="tab" aria-selected={kind===item.kind} key={item.kind} className={`asset-kind-tile asset-color-${item.color} ${kind===item.kind?'is-selected':''}`} onClick={()=>setParams({kind:item.kind})}><div className="asset-kind-top"><span className="asset-kind-symbol"><Icon size={22}/></span><span className="asset-kind-count">{count===undefined?'—':number(count)}<small>项</small></span></div><div className="asset-kind-name">{item.name}<ArrowRight size={16}/></div><p>{item.description}</p></button>})}</div>
 {kind==='data'&&instanceData.data&&<section className="data-instance-overview" aria-label="已接入数据实例"><div><span><Database size={17}/>标准与业务原表的对应</span><p>标准字典定义口径，数据实例保留单位、台账、原字段与原始行，按已确认对应贯通查阅。</p></div><dl><div><dt>已关联标准</dt><dd>{number(instanceData.data.summary.standards)}</dd></div><div><dt>涉及单位</dt><dd>{number(instanceData.data.summary.units)}</dd></div><div><dt>接入台账</dt><dd>{number(instanceData.data.summary.datasets)}</dd></div><div><dt>原始记录</dt><dd>{number(instanceData.data.summary.records)}</dd></div></dl><Link to="/datasets">查看接入原表<ArrowUpRight size={14}/></Link><small>{instanceData.data.summary.demo_records>0?`${number(instanceData.data.summary.demo_records)} 条为演示记录`:'暂无演示记录'}；{instanceData.data.summary.user_records>0?`${number(instanceData.data.summary.user_records)} 条为用户接入记录`:'尚无用户接入记录'}。记录按台账原始行去重，各标准关联数不可相加。</small></section>}
 {kind==='knowledge'?<KnowledgeLibrary/>:<div className={'catalog-shell '+(busy?'is-pending':'')} aria-busy={busy}>{error&&<div className="catalog-refresh-error" role="alert"><span>此次目录未能加载，仍保留上次完整视图。{error}</span><button className="btn btn-ghost" onClick={retry}>重新加载</button></div>}{busy&&!error&&snapshot&&<div className="catalog-pending"><Loading variant="overlay" label={snapshot.kind===kind?'正在更新分类与条目…':`正在载入${requestedName}，下方仍为${assetKinds.find(x=>x.kind===snapshot.kind)!.name}`}/></div>}{snapshot?<CatalogWorkspace snapshot={snapshot} requestedParams={params} requestedKind={kind} setParams={setParams} busy={busy} instances={instanceData.data?.by_standard}/>:error?<Empty title="资产目录暂时未能加载" description="请重新加载后继续。"/>:<CatalogInitial/>}</div>}
 </div>
}
function CatalogWorkspace({snapshot,requestedParams,requestedKind,setParams,busy,instances}:{snapshot:CatalogSnapshot;requestedParams:URLSearchParams;requestedKind:string;setParams:ReturnType<typeof useSearchParams>[1];busy:boolean;instances?:Record<string,DataInstanceSummary>}){
 const {kind,tree,result}=snapshot,params=new URLSearchParams(snapshot.params),meta=assetKinds.find(x=>x.kind===kind)!;
 const q=params.get('q')||'',view=params.get('view')||'business',page=Math.max(1,Number(params.get('page'))||1);
 const [search,setSearch]=useState(q);useEffect(()=>setSearch(q),[q]);
 const update=(changes:Record<string,string>,reset=true)=>{const p=new URLSearchParams(requestedParams);Object.entries(changes).forEach(([k,v])=>v?p.set(k,v):p.delete(k));if(reset)p.delete('page');setParams(p)};

 const choose=(node?:CatalogNode)=>{const changes:Record<string,string>={};branchKeys.forEach(k=>changes[k]='');Object.assign(changes,node?.path||{});update(changes)};
 const axes=tree.axes||[];
 const branch=axes.filter(k=>params.has(k));
 const selected=(function find(nodes:CatalogNode[]):CatalogNode|undefined{for(const n of nodes){const inner=find(n.children);if(inner)return inner;if(Object.entries(n.path).every(([k,v])=>params.get(k)===v))return n}})(tree.nodes||[]);
 const groups=selected?.children.length?selected.children:!selected?tree.nodes||[]:[];
 const hasFilters=['q','status','source_kind','asset_source','rule_type','algorithm_type','ownership'].some(k=>params.has(k));
 const showItems=!!selected||hasFilters||params.get('display')==='all';
 const facets=result.facets||{},total=result.total||0,pages=Math.max(1,Math.ceil(total/20));
 const facet=(field:string,label:string,extra=false)=>{const values=facets[field]||{};return <FilterTabs className={extra?'catalog-facet-extra':''} label={label} value={params.get(field)||''} onChange={v=>update({[field]:v})} options={[{value:'',label:'全部',count:Object.values(values).reduce((a:any,b:any)=>a+b,0) as number},...Object.entries(values).map(([value,count])=>({value,label:value==='__empty__'?'待标注':field==='status'?readableStatus(value):field==='source_kind'?sourceLabel(value):value,count:count as number}))]}/>};
 return <section className="catalog-workspace" data-catalog-query={snapshot.query}><aside className="catalog-sidebar" inert={busy&&(requestedKind!==kind||(requestedParams.get('view')||'business')!==view)?true:undefined}><div className="catalog-sidebar-head"><FolderOpen size={17}/><strong>分类目录</strong></div>{kind==='rule'&&<FilterTabs value={view} onChange={v=>{const changes:Record<string,string>={view:v};branchKeys.forEach(k=>changes[k]='');update(changes)}} options={[{value:'business',label:'业务主题'},{value:'source',label:'依据法规'}]}/>}<button className={'catalog-root '+(!selected?'active':'')} onClick={()=>choose()}><span>全部{meta.short}资产</span><b>{number(tree.total||0)}</b></button><ul className="catalog-tree">{(tree.nodes||[]).map(node=><CatalogBranch key={node.id} node={node} params={params} choose={choose}/>)}</ul><div className="catalog-tree-note">同一资产可沿业务或依据检索；收录与归类不会重复计数。</div></aside>
 <div className="catalog-content" inert={busy?true:undefined}><div className="catalog-title"><div><span className="eyebrow">{view==='source'?'按依据逐层查阅':'按业务逐层查阅'}</span><h2>{selected?.label||`${meta.name}分类总览`}</h2></div><span>{number(total)} 项</span></div><form className="catalog-search" onSubmit={e=>{e.preventDefault();update({q:search.trim()})}}><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索业务名称、条款要点或资产编号" aria-label="搜索资产"/>{search&&<button type="button" className="icon-btn" aria-label="清空检索" onClick={()=>{setSearch('');update({q:''})}}><X size={15}/></button>}<button className="btn btn-primary" type="submit">检索</button></form>
 <div className="catalog-facets">{facet('status','办理状态')}{facet('source_kind',kind==='rule'?'归集方式':'资产来源')}{kind==='rule'&&facet('asset_source','资产来源')}{kind==='rule'?facet('rule_type','规则类型',true):kind==='algorithm'?facet('algorithm_type','方法类型',true):facet('ownership','资产归属',true)}</div>
 {branch.length>0&&<div className="catalog-path"><button onClick={()=>choose()}>分类总览</button>{branch.map(k=><span key={k}><ChevronRight size={12}/>{params.get(k)==='__empty__'?'待归类':params.get(k)}</span>)}<button className="catalog-clear" onClick={()=>choose()}>清除分类</button></div>}
 {groups.length>0&&<div className="catalog-group-grid">{groups.map(node=><button key={node.id} className="catalog-group" onClick={()=>choose(node)}><span><FolderOpen size={19}/><b>{number(node.count)}</b></span><h3>{node.label}</h3><p>{node.children.length?node.children.slice(0,3).map(x=>x.label).join(' / '):'查看此类业务资产'}</p><ArrowUpRight size={16}/></button>)}</div>}
 {showItems?<>{total?<div className="asset-results">{result.items.map((e:AssetEntity)=><AssetPreview key={e.id} entity={e} instances={kind==='data'&&instances?instances[e.id]||null:undefined}/>)}</div>:<Empty title="没有匹配的资产" description="可清除分类或缩短检索词后重试。"/>}{total>0&&<div className="asset-pagination"><span>第 {(page-1)*20+1}–{Math.min(page*20,total)} 项 / 共 {number(total)} 项</span><div><button className="btn btn-ghost" disabled={page<=1} onClick={()=>update({page:String(page-1)},false)} aria-label="上一页"><ChevronLeft size={17}/></button><span>{page} / {pages}</span><button className="btn btn-ghost" disabled={page>=pages} onClick={()=>update({page:String(page+1)},false)} aria-label="下一页"><ChevronRight size={17}/></button></div></div>}</>:<div className="catalog-start"><span>选择一个业务分类，逐层进入具体资产。</span><button className="btn btn-ghost" onClick={()=>update({display:'all'})}>展开全部条目<ArrowRight size={15}/></button></div>}
 {!!result.historical_total&&<details className="catalog-history"><summary>历史条目与整理参考 · {number(result.historical_total)} 项</summary><p>已整合的知识、规则保留原编号和来源，当前目录以有效成果为准。历史条目只供追溯。</p><button className="btn btn-ghost" onClick={()=>update({history:params.get('history')==='all'?'':'all',display:'all'})}>{params.get('history')==='all'?'恢复当前成果口径':'连同历史条目检索'}</button></details>}
 </div></section>
}
export default Assets;
