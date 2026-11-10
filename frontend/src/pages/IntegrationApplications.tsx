import {FormEvent, useCallback, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {ArrowRight, Check, Clipboard, Code2, Eye, EyeOff, KeyRound, Layers3, Link2, Plus, RefreshCw, Search, ShieldCheck, Unplug, X} from 'lucide-react';
import {api, useApi} from '../api';
import {useAuth} from '../auth';
import {Badge, Card, Empty, ErrorPanel, Loading, Modal, PageHeader, useToast} from '../components/ui';
import './integration-applications.css';

type Tool = {name:string;title:string;description:string};
type Credential = {id:string;prefix:string;expires_at:string|number;revoked_at:string|number|null;created_at:string|number;last_used_at:string|number|null;usable:boolean;unusable_reason?:string};
type Application = {id:string;tenant_id:string;name:string;description:string;delegate_user_id:string;delegate_username:string;enabled:boolean;version:number;tool_scopes:string[];mapping_ids:string[];created_at:string|number;updated_at:string|number;credentials:Credential[]};
type Issued = {application:Application;credential:{id:string;token:string;prefix:string;expires_at:string|number}};
type Options = {users:{id:string;username:string;display_name:string;role:string}[];mappings:{id:string;title:string;source_id:string;source_title:string;status:string;version:number}[];tools:Tool[]};
type Directory = {items:Application[];tools:Tool[];endpoint:string;contract_version:string;limits:{timeout_ms:number;max_concurrent_per_process:number;max_rows:number;max_join_keys:number;max_result_bytes:number};retention:{result:string;cache:string;audit:string}};
const ROOT = '/integrations/applications';
const href = (id:string) => ROOT+'/'+encodeURIComponent(id);
const timestamp = (value:string|number|null|undefined) => value ? new Date(typeof value==='number'&&value<1e12 ? value*1000 : value).getTime() : 0;
const date = (value:string|number|null|undefined) => value ? new Date(timestamp(value)).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) : '—';
const roleName = (value:string) => ({admin:'管理员',modeler:'本体建模师',reviewer:'审核官'} as Record<string,string>)[value]||value;
function errorMessage(error:any) {return error?.status===409 ? '应用已被其他操作更新。请关闭当前对话框、刷新列表，再基于最新版本重试。' : error?.message||'操作未完成，请稍后重试。'}
function credentialStatus(credential:Credential) {
 if(credential.revoked_at)return {label:'已撤销',tone:'neutral'};
 if(timestamp(credential.expires_at)<=Date.now())return {label:'已过期',tone:'amber'};
 return credential.usable?{label:'有效',tone:'green'}:{label:({application_disabled:'应用已停用',delegate_changed:'委托身份已变更'} as Record<string,string>)[credential.unusable_reason||'']||'当前不可用',tone:'red'};
}
function applicationStatus(application:Application) {
 if(!application.enabled)return {label:'已停用',tone:'neutral'};
 if(application.credentials.some(c=>c.usable&&!c.revoked_at&&timestamp(c.expires_at)>Date.now()))return {label:'可接入',tone:'green'};
 return {label:'无有效令牌',tone:'amber'};
}
function Notice({children,warning=false}:{children:React.ReactNode;warning?:boolean}) {return <div className={'ia-notice '+(warning?'warning':'')}><ShieldCheck size={17}/><div>{children}</div></div>}
function InlineError({error}:{error:string}) {return error ? <div className="ia-error" role="alert">{error}</div> : null}

/** Requests carrying a one-time token cannot populate an unmounted or cleared view. */
function useScopedOperation() {
 const generation=useRef(0),pending=useRef<AbortController|null>(null),active=useRef(true);
 useEffect(()=>{active.current=true;const clear=()=>{generation.current++;pending.current?.abort();pending.current=null};window.addEventListener('ontology:scope-cleared',clear);window.addEventListener('pagehide',clear);return()=>{active.current=false;clear();window.removeEventListener('ontology:scope-cleared',clear);window.removeEventListener('pagehide',clear)}},[]);
 return useCallback(async <T,>(path:string,body:unknown,method='POST'):Promise<T>=>{
  pending.current?.abort();const controller=new AbortController(),request=++generation.current;pending.current=controller;
  try {const result=await api<T>(path,{method,body:JSON.stringify(body),signal:controller.signal});if(!active.current||request!==generation.current)throw new DOMException('页面已离开或上下文已清除','AbortError');return result}
  finally {if(request===generation.current)pending.current=null}
 },[]);
}

export default function IntegrationApplications() {
 const {session}=useAuth();
 if(!session?.permissions.manage_users||session.workspace.readonly)return <div className="page"><PageHeader title="集成应用" description="由本机构管理员登记调用方并明确授权范围。"/><Card><Empty title="当前账号没有应用接入管理权限" description="如需接入上层应用，请联系本机构管理员。"/></Card></div>;
 return <ApplicationWorkspace key={session.workspace.tenant_id+':'+session.user.id}/>;
}

function ApplicationWorkspace() {
 const {session}=useAuth(),q=useApi<Directory>(ROOT),options=useApi<Options>(ROOT+'/options'),toast=useToast(),operate=useScopedOperation();
 const [search,setSearch]=useState(''),[editor,setEditor]=useState<Application|'new'|null>(null),[credentialAction,setCredentialAction]=useState<{application:Application;action:'rotate'|'revoke'}|null>(null),[issued,setIssued]=useState<Issued|null>(null),[busy,setBusy]=useState(''),[error,setError]=useState('');
 useEffect(()=>{const clear=()=>{setIssued(null);setEditor(null);setCredentialAction(null);setError('')};window.addEventListener('ontology:scope-cleared',clear);window.addEventListener('pagehide',clear);return()=>{window.removeEventListener('ontology:scope-cleared',clear);window.removeEventListener('pagehide',clear)}},[]);
 const refresh=()=>{q.reload();options.reload();setError('')};
 const items=(q.data?.items||[]).filter(a=>(a.name+' '+a.delegate_username+' '+a.description).toLowerCase().includes(search.toLowerCase()));
 async function toggle(application:Application) {
  setBusy(application.id);setError('');
  try {await operate(href(application.id),{expected_version:application.version,enabled:!application.enabled},'PATCH');q.reload();toast(application.enabled?'应用已停用，后续调用将被拒绝。':'应用已启用，仍须使用有效令牌并满足委托权限。')}
  catch(err:any){if(err.name!=='AbortError')setError(errorMessage(err))}finally{setBusy('')}
 }
 const tools=q.data?.tools||options.data?.tools||[];
 return <div className="page ia-page"><PageHeader eyebrow="APPLICATION ACCESS / MCP 服务接入" title="集成应用" description="为上层应用签发独立凭据，按本机构委托账号、工具和已发布映射限定可调用范围。" actions={<button className="btn btn-primary" disabled={!options.data||!!busy} onClick={()=>{setIssued(null);setEditor('new')}}><Plus size={16}/>登记集成应用</button>}/>
  <Notice>应用固定归属当前机构：<strong>{session!.workspace.name}</strong>。实际权限取委托账号、源系统授权和应用授权的交集，不继承总部跨机构权限。查询结果只随响应返回，审计仅记录调用元数据。</Notice>
  <div className="ia-summary"><div><strong>{q.data?.items.length||0}</strong><span>登记应用</span></div><div><strong>{q.data?.items.filter(a=>applicationStatus(a).label==='可接入').length||0}</strong><span>具备有效凭据</span></div><div><strong>{tools.length}</strong><span>受控 MCP 工具</span></div><div><strong>{q.data?.limits.max_rows||200}</strong><span>单次结果行数上限</span></div></div>
  <InlineError error={error}/>
  <Card><div className="ia-section-head"><div><h2>本机构调用方</h2><p>源授权、委托账号或应用范围变更后，后续请求按最新权限检查。</p></div><div className="toolbar"><label className="ia-search"><Search size={15}/><input aria-label="搜索集成应用" value={search} onChange={ev=>setSearch(ev.target.value)} placeholder="应用名称或委托账号"/></label><button className="btn" onClick={refresh}><RefreshCw size={15}/>刷新</button></div></div>
   {q.loading?<Loading/>:q.error?<ErrorPanel error={q.error} reload={q.reload}/>:items.length?<div className="ia-app-list">{items.map(application=><ApplicationCard key={application.id} application={application} tools={tools} options={options.data} busy={!!busy} onEdit={()=>setEditor(application)} onToggle={()=>toggle(application)} onRotate={()=>{setIssued(null);setCredentialAction({application,action:'rotate'})}} onRevoke={()=>setCredentialAction({application,action:'revoke'})}/>)}</div>:<Empty title={search?'没有匹配的集成应用':'尚未登记集成应用'} description={search?'调整搜索条件后重试。':'准备一个已启用的本机构委托账号，并授予其必要的源查询权限，再登记调用方。'}>{!search&&<button className="btn btn-primary" disabled={!options.data} onClick={()=>setEditor('new')}>登记第一个应用</button>}</Empty>}
  </Card>
  {options.error&&<InlineError error={'授权选项未加载：'+options.error}/>}
  {q.data&&<ConnectionGuide directory={q.data}/>}
  {editor&&options.data&&<ApplicationEditor key={editor==='new'?'new':editor.id} application={editor==='new'?undefined:editor} options={options.data} onClose={()=>setEditor(null)} onSaved={result=>{setEditor(null);refresh();if('credential' in result)setIssued(result);else toast('应用授权范围已更新。')}}/>}
  {credentialAction&&<CredentialAction {...credentialAction} onClose={()=>setCredentialAction(null)} onSaved={result=>{setCredentialAction(null);refresh();if('credential' in result)setIssued(result);else toast('全部应用令牌已撤销，重新接入须签发新令牌。')}}/>}
  {issued&&<OneTimeCredential issued={issued} onClose={()=>setIssued(null)}/>}
 </div>;
}

function ApplicationCard({application:a,tools,options,busy,onEdit,onToggle,onRotate,onRevoke}:{application:Application;tools:Tool[];options:Options|null;busy:boolean;onEdit:()=>void;onToggle:()=>void;onRotate:()=>void;onRevoke:()=>void}) {
 const state=applicationStatus(a),delegate=options?.users.find(u=>u.id===a.delegate_user_id);
 const lastUsed=a.credentials.reduce((latest,c)=>Math.max(latest,timestamp(c.last_used_at)),0);
 return <article className="ia-app-card" data-application-id={a.id}><div className="ia-section-head"><div><div className="ia-app-title"><span className="ia-app-icon"><Link2 size={20}/></span><h3>{a.name}</h3><Badge tone={state.tone}>{state.label}</Badge><span className="ia-version">v{a.version}</span></div><p>{a.description||'未填写应用说明'}</p></div><div className="toolbar"><button className="btn" disabled={busy||!options} onClick={onEdit}>编辑范围</button><button className="btn" disabled={busy} onClick={onToggle}>{a.enabled?'停用应用':'启用应用'}</button></div></div>
  <dl className="ia-app-facts"><div><dt>委托账号</dt><dd>{delegate?.display_name||a.delegate_username}<small>{a.delegate_username}{delegate?' · '+roleName(delegate.role):' · 账号当前不在可委托列表'}</small></dd></div><div><dt>授权工具</dt><dd>{a.tool_scopes.length} 项<small>按显式范围逐次核验</small></dd></div><div><dt>授权映射</dt><dd>{a.mapping_ids.length} 项<small>新发布映射不会自动加入</small></dd></div><div><dt>最近调用</dt><dd>{lastUsed?date(lastUsed):'尚无记录'}<small>创建于 {date(a.created_at)}</small></dd></div></dl>
  <div className="ia-tool-tags">{a.tool_scopes.map(name=><span key={name} title={tools.find(t=>t.name===name)?.description}><Code2 size={13}/>{tools.find(t=>t.name===name)?.title||name}</span>)}</div>
  {a.mapping_ids.length>0&&<details className="ia-mapping-details"><summary>查看授权映射</summary><div className="ia-bound-mappings">{a.mapping_ids.map(id=>{const mapping=options?.mappings.find(m=>m.id===id);return <div key={id}><Layers3 size={15}/><span><strong>{mapping?.title||id}</strong><small>{mapping?mapping.source_title+' · v'+mapping.version:'当前未发布、已失效或不可用；查询会重新核验'}</small></span>{mapping&&<Link to={'/data-sources/'+encodeURIComponent(mapping.source_id)+'?mapping='+encodeURIComponent(mapping.id)}>查看映射<ArrowRight size={13}/></Link>}</div>})}</div></details>}
  <div className="ia-credential-heading"><h4>凭据状态</h4><div className="toolbar"><button className="btn" onClick={onRotate} disabled={busy}><RefreshCw size={14}/>轮换令牌</button><button className="btn" onClick={onRevoke} disabled={busy||!a.credentials.some(c=>!c.revoked_at)}><Unplug size={14}/>撤销全部令牌</button></div></div>
  <div className="ia-table-scroll"><table className="ia-table"><thead><tr><th>令牌前缀</th><th>状态</th><th>到期时间</th><th>最后调用</th><th>签发 / 撤销</th></tr></thead><tbody>{a.credentials.map(c=>{const state=credentialStatus(c);return <tr key={c.id}><td><code>{c.prefix}…</code></td><td><Badge tone={state.tone}>{state.label}</Badge></td><td>{date(c.expires_at)}</td><td>{date(c.last_used_at)}</td><td>{date(c.created_at)}{c.revoked_at&&<small>撤销：{date(c.revoked_at)}</small>}</td></tr>})}</tbody></table>{!a.credentials.length&&<p className="ia-help ia-padded">尚无凭据。轮换令牌会签发一枚新的独立应用令牌。</p>}</div>
  {a.credentials.some(c=>!c.usable&&!c.revoked_at&&timestamp(c.expires_at)>Date.now())&&<p className="ia-help">未过期凭据仍可能因应用停用或委托账号状态、角色、密码变化而失效。核对委托身份后，由管理员重新轮换。</p>}
  <p className="ia-identity">应用标识 <code>{a.id}</code> · 本机构固定归属</p>
 </article>;
}

function ApplicationEditor({application,options,onClose,onSaved}:{application?:Application;options:Options;onClose:()=>void;onSaved:(result:Application|Issued)=>void}) {
 const [name,setName]=useState(application?.name||''),[description,setDescription]=useState(application?.description||''),[delegate,setDelegate]=useState(application?.delegate_user_id||''),[tools,setTools]=useState(application?.tool_scopes||['ontology_discover']),[mappings,setMappings]=useState(application?.mapping_ids||[]),[days,setDays]=useState(30),[search,setSearch]=useState(''),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const operate=useScopedOperation();
 const available=options.mappings.filter(m=>(m.title+' '+m.source_title+' '+m.id).toLowerCase().includes(search.toLowerCase()));
 const unavailable=mappings.filter(id=>!options.mappings.some(m=>m.id===id));
 const selectedDelegate=options.users.find(u=>u.id===delegate);
 function toggle<T,>(value:T,items:T[],set:(items:T[])=>void){set(items.includes(value)?items.filter(x=>x!==value):[...items,value]);setConfirmed(false)}
 async function submit(event:FormEvent) {
  event.preventDefault();setError('');if(!name.trim()){setError('请填写有效的应用名称。');return}if(!delegate||!tools.length||!confirmed)return;
  if(mappings.length>100){setError('一个应用最多显式授权 100 项映射，请缩小范围。');return}
  if(unavailable.length){setError('请移除当前不可用的映射。待重新审核发布后，再显式授权。');return}
  setBusy(true);
  try {const body={name:name.trim(),description:description.trim(),tool_scopes:tools,mapping_ids:mappings,...(application?{expected_version:application.version}:{delegate_user_id:delegate,expires_days:days})};onSaved(await operate<Application|Issued>(application?href(application.id):ROOT,body,application?'PATCH':'POST'))}
  catch(err:any){if(err.name!=='AbortError')setError(errorMessage(err))}finally{setBusy(false)}
 }
 return <Modal title={application?'编辑集成应用范围':'登记集成应用'} wide onClose={()=>!busy&&onClose()}><form className="ia-form" onSubmit={submit}>
  <Notice>委托账号决定应用的身份上限；以下工具和映射范围进一步收窄授权。应用令牌仅用于 MCP 接口，不能作为网页登录会话使用。</Notice>
  <div className="grid-2"><label htmlFor="ia-app-name">应用名称<input id="ia-app-name" className="input" required maxLength={160} value={name} onChange={ev=>{setName(ev.target.value);setConfirmed(false)}} placeholder="例如：集团知识检索助手"/></label><label htmlFor="ia-delegate">本机构委托账号<select id="ia-delegate" aria-label="本机构委托账号" className="select" required disabled={!!application} value={delegate} onChange={ev=>{setDelegate(ev.target.value);setConfirmed(false)}}><option value="">选择已启用且完成初始改密的账号</option>{application&&!selectedDelegate&&<option value={application.delegate_user_id}>{application.delegate_username}（当前不可委托）</option>}{options.users.map(u=><option value={u.id} key={u.id}>{u.display_name} · {u.username} · {roleName(u.role)}</option>)}</select><small>{application?'委托账号不可更改。更换委托身份请新建应用。':'建议使用职责清晰、权限最小的专用机构账号。'}</small></label></div>
  <label htmlFor="ia-app-description">应用说明<textarea id="ia-app-description" className="input" rows={3} maxLength={2000} value={description} onChange={ev=>{setDescription(ev.target.value);setConfirmed(false)}} placeholder="说明调用方、业务用途与责任人，便于审核和维护。"/></label>
  <fieldset><legend>允许调用的工具 <span>{tools.length} 项</span></legend><div className="ia-tool-options">{options.tools.map(tool=><label className={'ia-tool-option '+(tools.includes(tool.name)?'selected':'')} key={tool.name}><input type="checkbox" checked={tools.includes(tool.name)} onChange={()=>toggle(tool.name,tools,setTools)}/><span><strong>{tool.title}</strong><code>{tool.name}</code><small>{tool.description}</small></span></label>)}</div>{!tools.length&&<p className="ia-help">至少选择一项工具。</p>}</fieldset>
  <fieldset><legend>允许访问的已发布映射 <span>{mappings.length} / 100</span></legend><p className="ia-help">只显示本机构已发布的映射。委托账号还须具备相应源查询授权，后端将在保存与调用时再次核验。</p><label className="ia-search"><Search size={15}/><input aria-label="搜索可授权映射" value={search} onChange={ev=>setSearch(ev.target.value)} placeholder="搜索映射名称、数据源或标识"/></label>
   {unavailable.length>0&&<div className="ia-unavailable"><strong>以下旧授权当前不可用，请移除后保存：</strong>{unavailable.map(id=><div key={id}><code>{id}</code><button type="button" className="btn" onClick={()=>toggle(id,mappings,setMappings)}><X size={13}/>移除此项</button></div>)}</div>}
   <div className="ia-mapping-options">{available.map(m=><label key={m.id} className={'ia-mapping-option '+(mappings.includes(m.id)?'selected':'')}><input type="checkbox" checked={mappings.includes(m.id)} disabled={!mappings.includes(m.id)&&mappings.length>=100} onChange={()=>toggle(m.id,mappings,setMappings)}/><Layers3 size={17}/><span><strong>{m.title}</strong><small>{m.source_title} · 映射 v{m.version}</small><code>{m.id}</code></span><Badge tone="green">已发布</Badge></label>)}</div>{!available.length&&<p className="ia-help">{search?'没有匹配的映射，请调整搜索条件。':'当前没有已发布映射。可先登记本体发现等工具，再在数据源模块完成映射审核后授予查询范围。'}</p>}
   {!mappings.length&&tools.some(t=>['ontology_query','ontology_join'].includes(t))&&<Notice warning>已选择源查询或分步关联工具，但未授权任何映射。应用目前无法执行这些查询，后续须显式补充映射范围。</Notice>}
  </fieldset>
  {!application&&<label htmlFor="ia-ttl" className="ia-ttl">初始令牌有效天数<input id="ia-ttl" aria-label="初始令牌有效天数" className="input" required type="number" min={1} max={90} step={1} value={days} onChange={ev=>{setDays(Number(ev.target.value));setConfirmed(false)}}/><small>1—90 天；到期后需要管理员重新签发。</small></label>}
  <label className="ia-confirm"><input type="checkbox" required checked={confirmed} onChange={ev=>setConfirmed(ev.target.checked)}/>已确认委托账号、业务用途及最小工具与映射授权范围。</label><InlineError error={error}/><div className="ia-form-actions"><button className="btn" type="button" onClick={onClose} disabled={busy}>取消</button><button className="btn btn-primary" disabled={busy||!confirmed||!delegate||!tools.length||!!unavailable.length}>{busy?'正在保存…':application?'保存授权范围':'创建应用并签发令牌'}</button></div>
 </form></Modal>;
}

function CredentialAction({application,action,onClose,onSaved}:{application:Application;action:'rotate'|'revoke';onClose:()=>void;onSaved:(result:Application|Issued)=>void}) {
 const [days,setDays]=useState(30),[reason,setReason]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),operate=useScopedOperation();
 async function submit(event:FormEvent){event.preventDefault();setError('');setBusy(true);try{onSaved(await operate<Application|Issued>(href(application.id)+(action==='rotate'?'/credentials':'/revoke'),{expected_version:application.version,reason:reason.trim(),...(action==='rotate'?{expires_days:days}:{})}))}catch(err:any){if(err.name!=='AbortError')setError(errorMessage(err))}finally{setBusy(false)}}
 return <Modal title={action==='rotate'?'轮换应用令牌':'撤销全部应用令牌'} onClose={()=>!busy&&onClose()}><form className="ia-form" onSubmit={submit}><Notice warning>{action==='rotate'?<>为 <strong>{application.name}</strong> 签发新令牌，原有全部有效令牌立即撤销。请同步更新调用方密钥配置。</>:<>撤销 <strong>{application.name}</strong> 的全部令牌后，现有调用方将无法接入。恢复时需重新签发令牌。</>}</Notice>{action==='rotate'&&!application.enabled&&<p className="ia-help">应用当前已停用。轮换不会自动启用应用；新令牌仅在应用启用且委托权限有效时可用。</p>}{action==='rotate'&&<label htmlFor="ia-rotate-ttl">新令牌有效天数<input id="ia-rotate-ttl" aria-label="新令牌有效天数" className="input" required type="number" min={1} max={90} step={1} value={days} onChange={ev=>setDays(Number(ev.target.value))}/><small>1—90 天</small></label>}<label htmlFor="ia-credential-reason">操作原因<textarea id="ia-credential-reason" className="input" required rows={4} minLength={2} maxLength={1000} value={reason} onChange={ev=>setReason(ev.target.value)} placeholder="记录本次轮换或撤销的业务依据。"/></label><InlineError error={error}/><div className="ia-form-actions"><button className="btn" type="button" disabled={busy} onClick={onClose}>取消</button><button className="btn btn-primary" disabled={busy||reason.trim().length<2}>{busy?'正在处理…':action==='rotate'?'确认轮换并签发':'确认撤销全部令牌'}</button></div></form></Modal>;
}

function OneTimeCredential({issued,onClose}:{issued:Issued;onClose:()=>void}) {
 const [visible,setVisible]=useState(false),[copied,setCopied]=useState(false),[error,setError]=useState('');
 const input=useRef<HTMLInputElement>(null);
 async function copy(){setError('');try{if(!navigator.clipboard?.writeText)throw new Error('clipboard_unavailable');await navigator.clipboard.writeText(issued.credential.token);setCopied(true)}catch{setError('浏览器未允许访问剪贴板。可显示令牌后手动复制，并存入调用方密钥管理器。')}}
 return <Modal title="应用令牌仅显示本次" onClose={onClose}><div className="ia-form ia-token-dialog"><div className="ia-token-heading"><span><KeyRound size={25}/></span><div><h3>{issued.application.name}</h3><p>有效期至 {date(issued.credential.expires_at)}</p></div></div><Notice warning>关闭后无法再次查看原令牌。请将令牌交付给对应调用方的密钥管理器，勿写入代码、文档或客户端公开配置。</Notice><label htmlFor="ia-one-time-token">一次性应用令牌<div className="ia-token-field"><input ref={input} id="ia-one-time-token" className="input" type={visible?'text':'password'} readOnly autoComplete="off" spellCheck={false} value={issued.credential.token}/><button type="button" className="icon-btn" aria-label={visible?'隐藏应用令牌':'显示应用令牌'} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={18}/>:<Eye size={18}/>}</button></div></label><div className="ia-token-copy"><button type="button" className="btn btn-primary" onClick={copy}>{copied?<Check size={16}/>:<Clipboard size={16}/>}{copied?'已复制令牌':'复制应用令牌'}</button><span>前缀 {issued.credential.prefix}…</span></div><InlineError error={error}/><p className="ia-help">令牌只保留在当前弹窗内存中；关闭弹窗、离开页面或切换机构后清除。已复制内容由调用方自行受控保管。网页登录会话不能替代应用令牌。</p><div className="ia-form-actions"><button className="btn" type="button" onClick={onClose}>关闭并清除页面令牌</button></div></div></Modal>;
}

function ConnectionGuide({directory}:{directory:Directory}) {
 const [copied,setCopied]=useState(false),[error,setError]=useState('');
 const endpoint=new URL(directory.endpoint,window.location.origin).href;
 const config=JSON.stringify({mcpServers:{'ontology-one':{type:'http',url:'https://ONTOLOGY_HOST'+directory.endpoint,headers:{Authorization:'Bearer <APPLICATION_TOKEN>'}}}},null,2);
 async function copy(){setError('');try{await navigator.clipboard.writeText(config);setCopied(true)}catch{setError('无法自动复制，请选中配置示例手动复制。')}}
 return <Card className="ia-guide"><div className="ia-section-head"><div><h2>MCP 调用方接入</h2><p>Streamable HTTP · 工具契约 v{directory.contract_version} · 独立应用 Bearer 身份</p></div><Badge tone="blue">仅受控只读工具</Badge></div><div className="ia-endpoint"><span>本环境服务端点</span><code>{endpoint}</code></div><div className="ia-limit-grid"><div><strong>{directory.limits.timeout_ms/1000} 秒</strong><span>工具执行时限</span></div><div><strong>{directory.limits.max_rows} 行</strong><span>单次结果上限</span></div><div><strong>{directory.limits.max_join_keys} 个</strong><span>分步关联键上限</span></div><div><strong>{Math.round(directory.limits.max_result_bytes/1024/1024)} MiB</strong><span>结果大小上限</span></div></div><details className="ia-client-example"><summary><Code2 size={15}/>客户端配置示例（仅含占位符）</summary><p>按客户端支持的 HTTP MCP 配置字段填写，将服务地址替换为内网部署地址。应用令牌从调用方密钥管理器注入，不使用网页登录令牌。</p><pre>{config}</pre><button className="btn" onClick={copy}><Clipboard size={15}/>{copied?'已复制占位符示例':'复制配置示例'}</button><InlineError error={error}/></details><div className="ia-guide-links"><Link to="/data-sources"><Layers3 size={15}/>维护源数据映射<ArrowRight size={13}/></Link><Link to="/administration"><ShieldCheck size={15}/>管理委托账号<ArrowRight size={13}/></Link></div></Card>;
}
