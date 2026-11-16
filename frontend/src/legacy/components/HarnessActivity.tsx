import {useCallback, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {ArrowUpRight, CheckCircle2, ChevronDown, CircleAlert, Clock3, ListTodo, LoaderCircle, RefreshCw, Square} from 'lucide-react';
import {api} from '../api';
import {useAuth} from '../auth';
import {LEGACY_HARNESS_KINDS} from './LegacyHarnessConversation';
import './harness-activity.css';

export type HarnessActivityItem = {
  id: string; run_id?: string; kind: string; title: string; status: string;
  entity_id?: string; session_id?: string; href?: string; created_at?: string; updated_at?: string;
  stage?: string; message?: string;
};
type ActivityResponse = {items: HarnessActivityItem[]};
const running = (status: string) => ['queued', 'running', 'retry', 'cancelling'].includes(status);
const labels: Record<string, string> = {queued:'等待处理', running:'正在处理', retry:'等待处理', cancelling:'正在停止', completed:'已完成', succeeded:'已完成', success:'已完成', failed:'未完成', cancelled:'已停止'};
const kindName = (kind:string) => kind==='skill_author'?'技能建设':kind==='application'?'本体应用':'智能辅助';
const stamp = (value?:string) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit'}) : '';

export function activityHref(item:HarnessActivityItem) {
  // Navigation targets come from the fixed task adapters, never external URLs.
  const href=item.href||'';
  if (/^\/(?:skill-development|applications|briefing|materials|experience|projects|tasks|authority|assets|datasets)(?:[/?#]|$)/.test(href) && !/[\\\u0000-\u001f]/.test(href)) {
    if(!LEGACY_HARNESS_KINDS.has(item.kind))return href;
    const url=new URL(href,window.location.origin);url.searchParams.set('harness_run',item.run_id||item.id);if(item.kind==='placement_suggest')url.searchParams.set('placement','1');return url.pathname+url.search+url.hash;
  }
  if(item.kind==='application'&&item.session_id) return '/applications?'+new URLSearchParams({session:item.session_id,...(item.run_id||item.id?{run:item.run_id||item.id}:{})}).toString();
  if(item.kind==='skill_author'&&item.entity_id) return '/skill-development/'+encodeURIComponent(item.entity_id)+'?'+new URLSearchParams({run:item.run_id||item.id}).toString();
  return '';
}

/** Kept in the shell: page navigation only closes the view, not server work. */
export function useHarnessActivity() {
  const {session}=useAuth();
  const scope=session?session.user.id+':'+session.workspace.tenant_id:'';
  const readOnlyForeign=!!session?.workspace.readonly&&session.workspace.tenant_id!==session.user.home_tenant_id;
  const [items,setItems]=useState<HarnessActivityItem[]>([]),[loading,setLoading]=useState(true),[unavailable,setUnavailable]=useState(false),[revision,setRevision]=useState(0);
  const refresh=useCallback(()=>setRevision(value=>value+1),[]);
  useEffect(()=>{
    let disposed=false, timer:ReturnType<typeof setTimeout>|undefined;
    const controller=new AbortController();
    const clear=()=>{disposed=true;controller.abort();clearTimeout(timer);setItems([]);setUnavailable(false);setLoading(false)};
    async function load() {
      const responses=await Promise.allSettled([
        api<ActivityResponse>('/harness/runs?limit=20',{signal:controller.signal}),
        api<ActivityResponse>('/agent/runs',{signal:controller.signal}),
      ]);
      if(disposed)return;
      const next:HarnessActivityItem[]=[];
      let failures=0;
      for(const response of responses) {
        if(response.status==='rejected'){failures++;continue}
        for(const item of Array.isArray(response.value?.items)?response.value.items:[]) if(item&&typeof item.id==='string'&&typeof item.status==='string')next.push(item);
      }
      const unique=[...new Map(next.map(item=>[item.kind+':'+item.id,item])).values()];
      unique.sort((left,right)=>Number(running(right.status))-Number(running(left.status)) || (Date.parse(right.updated_at||right.created_at||'')||0)-(Date.parse(left.updated_at||left.created_at||'')||0));
      setItems(unique.slice(0,30));setUnavailable(!!failures);setLoading(false);
      timer=setTimeout(()=>void load(), document.hidden?5000:unique.some(item=>running(item.status))?1500:5000);
    }
    if(readOnlyForeign){setItems([]);setUnavailable(false);setLoading(false)}else if(scope)void load();else setLoading(false);
    window.addEventListener('ontology:scope-cleared',clear);
    return()=>{disposed=true;controller.abort();clearTimeout(timer);window.removeEventListener('ontology:scope-cleared',clear)};
  },[scope,revision,readOnlyForeign]);
  useEffect(()=>{
    const events=['ontology:harness-runs-changed','ontology:harness-runs-updated','ontology:harness-run-started'];
    for(const event of events)window.addEventListener(event,refresh);
    return()=>{for(const event of events)window.removeEventListener(event,refresh)};
  },[refresh]);
  return {items:readOnlyForeign?[]:items,loading:!readOnlyForeign&&loading,unavailable:!readOnlyForeign&&unavailable,refresh,readOnlyForeign,activeCount:readOnlyForeign?0:items.filter(item=>running(item.status)).length};
}

function StateIcon({status}:{status:string}) {
  if(['running','retry','cancelling'].includes(status))return <LoaderCircle size={16} className="spin"/>;
  if(status==='queued')return <Clock3 size={16}/>;
  if(['completed','succeeded','success'].includes(status))return <CheckCircle2 size={16}/>;
  if(status==='cancelled')return <Square size={15}/>;
  return <CircleAlert size={16}/>;
}

export default function HarnessActivity() {
  const {items,loading,unavailable,refresh,activeCount,readOnlyForeign}=useHarnessActivity();
  const panel=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{
    const close=(event:MouseEvent)=>{if(panel.current&&!panel.current.contains(event.target as Node))panel.current.open=false};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&panel.current?.open){panel.current.open=false;panel.current.querySelector('summary')?.focus()}};
    document.addEventListener('click',close);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('click',close);document.removeEventListener('keydown',escape)};
  },[]);
  return <details className="harness-activity" ref={panel}>
    <summary className={activeCount?'has-active':''} aria-label={activeCount?`后台任务，${activeCount}项正在处理`:'后台任务'} title={activeCount?`${activeCount}项任务正在后台处理`:'查看后台任务'}>
      {activeCount?<LoaderCircle size={16} className="spin"/>:<ListTodo size={17}/>}<span>后台任务</span>{activeCount>0&&<b className="harness-activity-count">{activeCount}</b>}<ChevronDown size={13} className="harness-activity-chevron"/>
    </summary>
    <div className="harness-activity-panel">
      <div className="harness-activity-heading"><div><strong>我的后台任务</strong><small>{readOnlyForeign?'当前为其他机构的只读工作区':activeCount?`${activeCount}项正在处理`:'当前没有正在处理的任务'}</small></div><button className="icon-btn" type="button" aria-label="刷新后台任务" disabled={readOnlyForeign} onClick={refresh}><RefreshCw size={15}/></button></div>
      <p className="harness-activity-copy">{readOnlyForeign?'切回所属机构后，可查看和继续本人的后台任务。':'离开页面后仍会继续处理，点击任务可回到对应页面。'}</p>
      {unavailable&&<div className="harness-activity-warning" role="status">部分任务状态暂未更新。<button type="button" onClick={refresh}>重新读取</button></div>}
      <div className="harness-activity-list">
        {loading&&!items.length?<div className="harness-activity-empty"><LoaderCircle size={18} className="spin"/><span>正在读取任务…</span></div>:!items.length?<div className="harness-activity-empty"><ListTodo size={22}/><span>{readOnlyForeign?'切回所属机构查看相应任务':'还没有可查看的任务'}</span></div>:items.map(item=>{
          const href=activityHref(item);
          const content=<><span className={'harness-activity-state '+(running(item.status)?'active':['completed','succeeded','success'].includes(item.status)?'done':item.status==='failed'?'failed':'')}><StateIcon status={item.status}/></span><span className="harness-activity-task"><strong>{item.title||kindName(item.kind)}</strong><span>{kindName(item.kind)}<i>·</i>{labels[item.status]||'状态更新中'}{stamp(item.updated_at||item.created_at)&&<time>{stamp(item.updated_at||item.created_at)}</time>}</span>{item.message&&<small>{item.message}</small>}</span>{href&&<ArrowUpRight size={14}/>}</>;
          return href?<Link className="harness-activity-item" key={item.kind+':'+item.id} to={href} onClick={()=>{if(panel.current)panel.current.open=false}}>{content}</Link>:<div className="harness-activity-item" key={item.kind+':'+item.id}>{content}</div>;
        })}
      </div>
      <div className="harness-activity-foot">仅显示当前账号、当前机构的近期任务。</div>
    </div>
  </details>;
}
