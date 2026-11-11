import {useEffect, useRef, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {ArrowUpRight, CheckCircle2, ChevronDown, CircleAlert, LoaderCircle, MessageSquareText, RefreshCw, ShieldCheck, Square, X} from 'lucide-react';
import {api, ApiError, request, type LegacyHarnessRun, type LegacyHarnessStarted} from '../api';
import Markdown from './Markdown';
import './legacy-harness-conversation.css';

export const LEGACY_HARNESS_KINDS=new Set(['ai_ask','authority_assist','project_plan','placement_suggest','dataset_mapping','material_reading','material_segments','experience_start','experience_turn','experience_question','experience_proposal']);
const active=(status?:string)=>['queued','running','cancelling'].includes(status||'');
const readable=(value:unknown,maximum=4000)=>typeof value==='string'?value.slice(0,maximum).replace(/Harness/g,'智能助手'):'';
const list=(value:any)=>Array.isArray(value)?value:[];
const stateName=(value?:string)=>({queued:'等待处理',running:'正在处理',cancelling:'正在停止',completed:'处理完成',failed:'本轮未完成',cancelled:'已停止'} as Record<string,string>)[value||'']||'正在读取';
const relative=(url:URL)=>url.pathname+url.search+url.hash;
function routeKey(value:string){const url=new URL(value,window.location.origin);url.searchParams.delete('harness_run');url.searchParams.sort();return url.pathname+url.search}
function businessHref(run:LegacyHarnessRun){
  if(run.kind==='experience_start'&&typeof run.result?.entity?.id==='string')return '/experience?'+new URLSearchParams({session:run.result.entity.id}).toString();
  const href=run.href||'';
  if(!/^\/(?:briefing|authority|projects|assets|datasets|materials|experience)(?:[/?#]|$)/.test(href)||/[\\\u0000-\u001f]/.test(href))return '';
  const url=new URL(href,window.location.origin);url.searchParams.delete('harness_run');return relative(url);
}

/** Only named business fields are summarized; machine objects stay out of the view. */
export function legacyBusinessSummary(run:LegacyHarnessRun):string {
  const result=run.result;if(!result||typeof result!=='object')return typeof result==='string'?readable(result):'结果已准备好，请返回业务页面核对。';
  const parts:string[]=[];
  const add=(label:string,value:unknown)=>{const text=readable(value);if(text&&!/^\s*[\[{]\s*["\]}]/.test(text))parts.push(label?'### '+label+'\n\n'+text:text)};
  if(run.kind==='ai_ask')add('',result.answer);
  add('',result.summary);add('补充说明',result.note);if(typeof result.notes==='string')add('补充说明',result.notes);
  for(const warning of list(result.warnings).slice(0,5))add('需留意',warning);
  if(run.kind==='project_plan'){
    const tasks=list(result.tasks).slice(0,12);if(tasks.length)parts.push('### 建议工作安排\n\n'+tasks.map((task:any,index:number)=>`${index+1}. **${readable(task.title,200)||'待核对的工作项'}**${readable(task.instruction)?'\n   '+readable(task.instruction):''}`).join('\n\n'));
  }else if(run.kind==='placement_suggest'){
    const candidates=new Map(list(result.candidates).map((item:any)=>[item.id,readable(item.title,200)]));
    const suggestions=list(result.suggestions).slice(0,12);if(suggestions.length)parts.push('### 建议关联\n\n'+suggestions.map((item:any,index:number)=>`${index+1}. **${readable(item.target_title,200)||candidates.get(item.target_id)||'待核对的业务对象'}**${readable(item.reason)?'\n   '+readable(item.reason):''}`).join('\n\n'));
  }else if(run.kind==='dataset_mapping'){
    const items=list(result.items).slice(0,12);if(items.length)parts.push('### 字段对应建议\n\n'+items.map((item:any,index:number)=>`${index+1}. **${readable(item.source_header,200)||readable(item.source_column,200)||`第${index+1}项字段`}**${readable(item.standard_title,200)?' → '+readable(item.standard_title,200):''}${readable(item.reason)?'\n   '+readable(item.reason):''}`).join('\n\n'));
  }else if(run.kind==='material_reading'||run.kind==='material_segments'){
    const segments=list(result.segments||result.sections).slice(0,12);if(segments.length)parts.push('### 材料整理要点\n\n'+segments.map((item:any,index:number)=>`${index+1}. **${readable(item.heading,200)||readable(item.title,200)||`第${index+1}部分`}**${readable(item.content||item.text,700)?'\n   '+readable(item.content||item.text,700):''}`).join('\n\n'));
    if(typeof result.characters==='number')parts.push(`本次原文范围：${result.characters.toLocaleString()}字。`);
  }else if(run.kind==='authority_assist'){
    const spec=result.spec||result.authority_spec||{};
    add('规范主题',spec.title);add('适用范围',spec.scope);add('办理依据',spec.basis);
    const nodes=list(spec.nodes).slice(0,15);if(nodes.length)parts.push('### 办理要素\n\n'+nodes.map((item:any,index:number)=>`${index+1}. **${readable(item.title||item.label||item.name,200)||'待核对的办理环节'}**${readable(item.description||item.instruction,600)?'\n   '+readable(item.description||item.instruction,600):''}`).join('\n\n'));
  }else if(run.kind.startsWith('experience_')){
    add('经验主题',result.entity?.title);add('内容摘要',result.entity?.summary);
    const turns=list(result.entity?.payload?.turns);const question=[...turns].reverse().find((turn:any)=>turn.role==='assistant');
    add('接下来可以补充',result.question||result.next_question||question?.content);
  }
  const citations=list(result.citations||result.sources).slice(0,8);if(citations.length){const titles=citations.map((item:any)=>readable(item.title||item.source_title||item.entity_title,200)).filter(Boolean);if(titles.length)parts.push('### 本轮参考\n\n'+titles.map((title:string)=>'- '+title).join('\n'))}
  if(!parts.length)parts.push('本轮处理已完成，具体内容已准备好。请返回对应业务页面，继续查看与核对。');
  return parts.join('\n\n').slice(0,26000);
}

/** Read-only opt-in restoration for existing business proposal views. */
export function useHarnessResultRestore(operation:string,onResult:(result:any,run:LegacyHarnessRun)=>void,entityId=''){
  const location=useLocation(),callback=useRef(onResult),consumed=useRef(new Set<string>());callback.current=onResult;
  useEffect(()=>{
    const ident=new URLSearchParams(location.search).get('harness_run');if(!ident||!LEGACY_HARNESS_KINDS.has(operation)||consumed.current.has(ident))return;
    const controller=new AbortController();let disposed=false,timer:ReturnType<typeof setTimeout>|undefined;
    const clear=()=>{disposed=true;controller.abort();clearTimeout(timer);consumed.current.clear()};window.addEventListener('ontology:scope-cleared',clear);
    async function restore(){try{const run=await api<LegacyHarnessRun>('/harness/runs/'+encodeURIComponent(ident!),{signal:controller.signal});if(disposed||run.kind!==operation||(run.entity_id||'')!==entityId)return;if(run.status==='completed'&&Object.prototype.hasOwnProperty.call(run,'result')){consumed.current.add(ident!);callback.current(run.result,run)}else if(active(run.status))timer=setTimeout(restore,1500)}catch{/* A stale or withdrawn task never populates an adoption form. */}}
    void restore();return()=>{disposed=true;controller.abort();clearTimeout(timer);window.removeEventListener('ontology:scope-cleared',clear)};
  },[location.search,operation,entityId]);
}

export default function LegacyHarnessConversation(){
  const location=useLocation(),navigate=useNavigate(),locationRef=useRef(location);locationRef.current=location;
  const [remembered,setRemembered]=useState<LegacyHarnessStarted|null>(null),[run,setRun]=useState<LegacyHarnessRun|null>(null),[error,setError]=useState(''),[connection,setConnection]=useState(''),[revision,setRevision]=useState(0),[busy,setBusy]=useState(false);
  const queryId=new URLSearchParams(location.search).get('harness_run');
  const ident=queryId||(remembered&&routeKey(remembered.origin)===routeKey(location.pathname+location.search)?remembered.run.id:'');
  const shown=run?.id===ident?run:null;
  useEffect(()=>{
    const started=(event:Event)=>{const detail=(event as CustomEvent<LegacyHarnessStarted>).detail;if(!detail?.run||!LEGACY_HARNESS_KINDS.has(detail.operation))return;setRemembered(detail);const current=locationRef.current;if(routeKey(detail.origin)===routeKey(current.pathname+current.search)){const url=new URL(current.pathname+current.search+current.hash,window.location.origin);url.searchParams.set('harness_run',detail.run.id);navigate(relative(url),{replace:true})}};
    window.addEventListener('ontology:harness-run-started',started);return()=>window.removeEventListener('ontology:harness-run-started',started);
  },[navigate]);
  useEffect(()=>{
    if(!ident){setRun(null);setRemembered(null);setError('');setConnection('');return}let disposed=false,timer:ReturnType<typeof setTimeout>|undefined,cursor=0,reader:ReadableStreamDefaultReader<Uint8Array>|undefined;const controller=new AbortController();
    setRun(null);setError('');setConnection('');
    const clear=()=>{disposed=true;controller.abort();void reader?.cancel().catch(()=>{});clearTimeout(timer);setRun(null);setRemembered(null);setError('');setConnection('')};
    const clearScope=()=>{clear();const current=locationRef.current,url=new URL(current.pathname+current.search+current.hash,window.location.origin);url.searchParams.delete('harness_run');navigate(relative(url),{replace:true})};window.addEventListener('ontology:scope-cleared',clearScope);window.addEventListener('pagehide',clear);
    function accept(value:LegacyHarnessRun){if(disposed)return;if(!LEGACY_HARNESS_KINDS.has(value.kind))throw new ApiError('请从顶部后台任务打开对应处理页面。',404);const page=locationRef.current.pathname.match(/^\/(?:authority|projects|assets|datasets|materials)\/([^/]+)$/);if(page&&value.entity_id&&decodeURIComponent(page[1])!==value.entity_id)throw new ApiError('本轮任务属于其他业务对象。',404);setRun(value);for(const event of value.events||[])cursor=Math.max(cursor,event.seq||0);setConnection('');if(!active(value.status))window.dispatchEvent(new Event('ontology:harness-runs-changed'))}
    async function subscribe(){
      try{
        const initial=await api<LegacyHarnessRun>('/harness/runs/'+encodeURIComponent(ident),{signal:controller.signal});if(disposed)return;accept(initial);
        if(!active(initial.status)){timer=setTimeout(subscribe,10000);return}
        const response=await request('/harness/runs/'+encodeURIComponent(ident)+'/events?after='+cursor,{signal:controller.signal});
        if(disposed){await response.body?.cancel();return}if(!response.body||!response.headers.get('content-type')?.includes('text/event-stream'))throw new Error('公开进度暂时不可用。');
        reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';let terminal=false;
        while(!disposed){const chunk=await reader.read();if(chunk.done)break;buffer=(buffer+decoder.decode(chunk.value,{stream:true})).replace(/\r\n/g,'\n');if(buffer.length>4*1024*1024)throw new Error('公开进度超过可显示范围。');let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const event=frame.split('\n').find(line=>line.startsWith('event:'))?.slice(6).trim();const data=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(!data)continue;if(event==='unavailable')throw new ApiError('任务、来源或授权已变化，请回到业务页面重新核对。',409);const value=JSON.parse(data);if(event==='progress'){if(typeof value.seq==='number'&&value.seq>cursor){cursor=value.seq;setRun(previous=>previous?.id===ident?{...previous,stage:value.stage,message:value.message,events:[...(previous.events||[]),value].slice(-256)}:previous)}}else if(event==='state'){accept(value);terminal=!active(value.status)}}}
        if(!disposed)timer=setTimeout(subscribe,terminal?10000:1200);
      }catch(problem:any){if(disposed||problem.name==='AbortError')return;if([401,403,404,409,422].includes(problem.status)){setRun(null);setError('本轮任务已不可用，或相关内容与权限已有变化。请回到业务页面重新核对。');return}setConnection('进度连接暂时中断，任务仍在后台处理，正在重新连接…');timer=setTimeout(subscribe,2000)}
      finally{if(reader){try{await reader.cancel()}catch{}try{reader.releaseLock()}catch{}reader=undefined}}
    }
    void subscribe();return()=>{disposed=true;controller.abort();void reader?.cancel().catch(()=>{});clearTimeout(timer);window.removeEventListener('ontology:scope-cleared',clearScope);window.removeEventListener('pagehide',clear)};
  },[ident,revision,navigate]);
  function close(){setRemembered(null);setRun(null);setError('');const url=new URL(location.pathname+location.search+location.hash,window.location.origin);url.searchParams.delete('harness_run');navigate(relative(url),{replace:true})}
  async function cancel(){if(!shown||!active(shown.status)||busy)return;setBusy(true);try{const value=await api<LegacyHarnessRun>('/harness/runs/'+encodeURIComponent(shown.id)+'/cancel',{method:'POST'});setRun(value);window.dispatchEvent(new Event('ontology:harness-runs-changed'))}catch{setError('停止请求未完成，请刷新任务状态。')}finally{setBusy(false)}}
  if(!ident)return null;
  const instruction=shown?.instruction||remembered?.run.id===ident&&remembered.instruction||'',href=shown?businessHref(shown):'';
  return <section className="legacy-conversation" aria-label="智能助手处理对话">
    <header><div className="legacy-conversation-icon"><MessageSquareText size={18}/></div><div><strong>{shown?.title||(remembered?.run.id===ident?remembered.run.title:'智能助手')}</strong><small>{stateName(shown?.status)}</small></div><button className="icon-btn" type="button" aria-label="刷新智能处理结果" onClick={()=>setRevision(value=>value+1)}><RefreshCw size={15}/></button><button className="icon-btn" type="button" aria-label="收起智能处理对话" onClick={close}><X size={17}/></button></header>
    {!!instruction&&<div className="legacy-user-message"><span>本轮要求</span><p>{instruction}</p></div>}
    {!shown&&!error&&<div className="legacy-loading"><LoaderCircle size={17} className="spin"/>正在读取本轮处理…</div>}
    {shown&&<details className="legacy-progress" open={active(shown.status)}><summary>{active(shown.status)?<LoaderCircle size={15} className="spin"/>:shown.status==='completed'?<CheckCircle2 size={15}/>:<CircleAlert size={15}/>}<span>{active(shown.status)?'正在处理，可以离开当前页面':'查看处理过程'}</span><ChevronDown size={14}/></summary><ol>{(shown.events||[]).map(event=><li key={event.seq}><i/><span>{readable(event.message,300)||'正在处理任务。'}</span></li>)}</ol></details>}
    {connection&&<p className="legacy-connection" role="status">{connection}</p>}
    {error&&<p className="legacy-conversation-error" role="alert">{error}</p>}
    {shown?.status==='completed'&&<article className="legacy-assistant-message"><div><CheckCircle2 size={16}/><strong>本轮结果</strong></div><Markdown content={legacyBusinessSummary(shown)}/><p className="legacy-result-note"><ShieldCheck size={14}/>请结合原始材料核对，具体采用操作仍在对应业务页面完成。</p></article>}
    {shown?.status==='failed'&&<p className="legacy-conversation-error" role="alert">{readable(shown.error?.message||shown.message,700)||'本轮处理未完成，请核对后重试。'}</p>}
    {shown?.status==='cancelled'&&<p className="legacy-connection">本轮处理已停止。</p>}
    <footer><span>{shown?.retention_notice||'离开页面后继续处理，近期任务可从顶部后台任务重新打开。'}</span>{shown&&active(shown.status)&&<button type="button" className="btn" disabled={busy} onClick={()=>void cancel()}><Square size={13}/>{busy?'正在停止…':'停止本轮处理'}</button>}{href&&<Link className="btn" to={href} onClick={()=>{setRemembered(null);setRun(null)}}>返回业务页面核对<ArrowUpRight size={14}/></Link>}</footer>
  </section>;
}
