import {useCallback,useEffect,useRef,useState} from 'react';
import {eventLabel,OBJECT_NAMES} from './domain';
let csrfToken='';
let workspaceId='';
let businessBlocked=false;
const externalPending=new Set<()=>void>();
export function setWorkspaceId(id:string){workspaceId=id||''}
export function getWorkspaceId(){return workspaceId}
export function setBusinessBlocked(value:boolean){businessBlocked=value}
export function assertBusinessReady(){if(businessBlocked)throw new DOMException('机构上下文正在切换','AbortError')}
export function registerPendingUpload(abort:()=>void){externalPending.add(abort);return()=>externalPending.delete(abort)}
export function workspaceHeaders(){return workspaceId?{'X-Workspace-Id':workspaceId}:{}}
let scopeGeneration=0;
const pending=new Set<AbortController>();
export class ApiError extends Error{constructor(message:string,public status:number,public detail:any=null){super(message);this.name='ApiError'}}
export function setCsrfToken(token:string){csrfToken=token||''}
export function csrfHeaders(){return csrfToken?{'X-CSRF-Token':csrfToken}:{}}
export function invalidateRequests(){scopeGeneration++;for(const controller of pending)controller.abort();pending.clear();for(const abort of externalPending)abort();externalPending.clear()}
export function notifyUnauthorized(){window.dispatchEvent(new Event('ontology:unauthorized'))}
export async function request(path:string,init:RequestInit={}):Promise<Response>{
 const normalized=path.startsWith('/api')?path.slice(4):path;
 if(!normalized.startsWith('/auth/'))assertBusinessReady();
 const controller=new AbortController(),generation=scopeGeneration;
 const onAbort=()=>controller.abort();init.signal?.addEventListener('abort',onAbort,{once:true});
 pending.add(controller);
 const headers=new Headers(init.headers);
 if(workspaceId&&!normalized.startsWith('/auth/'))headers.set('X-Workspace-Id',workspaceId);
 if(!(init.body instanceof FormData)&&init.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
 if(!['GET','HEAD','OPTIONS'].includes((init.method||'GET').toUpperCase())&&csrfToken)headers.set('X-CSRF-Token',csrfToken);
 try{
  const r=await fetch(path.startsWith('/api')?path:'/api'+path,{...init,headers,credentials:'include',signal:controller.signal});
  if(generation!==scopeGeneration)throw new DOMException('机构上下文已切换','AbortError');
  if(r.status===401){notifyUnauthorized();throw new ApiError('登录已失效，请重新登录。',401)}
  if(!r.ok){let d:any;try{d=await r.json()}catch{};if(r.status===409&&(d?.code==='workspace_changed'||d?.detail?.code==='workspace_changed')){setBusinessBlocked(true);invalidateRequests();window.location.replace('/');throw new ApiError('机构已在另一窗口切换，正在重新载入。',409,d)}const msg=typeof d?.detail==='string'?d.detail:typeof d?.detail?.message==='string'?d.detail.message:`请求未完成（${r.status}），请稍后重试。`;throw new ApiError(msg,r.status,d?.detail)}
  return r;
 }finally{pending.delete(controller);init.signal?.removeEventListener('abort',onAbort)}
}
export type LegacyHarnessTask={operation:string;entity_id:string;body:Record<string,unknown>};
export type LegacyHarnessRun={id:string;run_id?:string;kind:string;title:string;status:string;entity_id?:string;href?:string;stage?:string;message?:string;instruction?:string;events?:{seq:number;type?:string;kind?:string;stage?:string;message?:string;created_at?:string}[];result?:any;error?:{code?:string;message?:string};retention_notice?:string;created_at?:string;updated_at?:string};
export type LegacyHarnessStarted={run:LegacyHarnessRun;operation:string;instruction:string;origin:string};

/** Only reviewed online generation routes are detached; saves/uploads stay direct. */
export function legacyHarnessTask(path:string,init:RequestInit={}):LegacyHarnessTask|null {
 if((init.method||'GET').toUpperCase()!=='POST')return null;
 const contentType=new Headers(init.headers).get('content-type');
 if(contentType&&contentType.split(';',1)[0].toLowerCase()!=='application/json')return null;
 let body:Record<string,unknown>={};
 if(init.body!==undefined&&init.body!==null){if(typeof init.body!=='string')return null;try{const parsed=JSON.parse(init.body);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return null;body=parsed}catch{return null}}
 const normalized=path.startsWith('/api/')?path.slice(4):path;
 const online=body.mode===undefined||body.mode==='online'||body.mode==='auto';
 if(normalized==='/ai/ask'&&body.mode!=='retrieval')return {operation:'ai_ask',entity_id:'',body};
 if(normalized==='/authority/assist')return {operation:'authority_assist',entity_id:'',body};
 if(normalized==='/experience/sessions'&&online)return {operation:'experience_start',entity_id:'',body};
 const rules:[RegExp,string,boolean][]=[
  [/^\/projects\/([A-Za-z0-9_-]{1,191})\/plan$/,'project_plan',true],
  [/^\/placement\/([A-Za-z0-9_-]{1,191})\/suggest$/,'placement_suggest',body.mode==='online'],
  [/^\/datasets\/([A-Za-z0-9_-]{1,191})\/mapping-recognize$/,'dataset_mapping',true],
  [/^\/materials\/([A-Za-z0-9_-]{1,191})\/reading\/ai$/,'material_reading',body.mode==='online'],
  [/^\/materials\/([A-Za-z0-9_-]{1,191})\/segment-preview$/,'material_segments',body.mode==='online'],
  [/^\/experience\/([A-Za-z0-9_-]{1,191})\/turns$/,'experience_turn',online],
  [/^\/experience\/([A-Za-z0-9_-]{1,191})\/question$/,'experience_question',online],
  [/^\/experience\/([A-Za-z0-9_-]{1,191})\/proposals$/,'experience_proposal',true],
 ];
 for(const [pattern,operation,enabled]of rules){const match=enabled&&normalized.match(pattern);if(match)return {operation,entity_id:match[1],body}}
 return null;
}

async function directApi<T=any>(path:string,init:RequestInit={}):Promise<T>{const r=await request(path,init);return r.status===204?null as T:r.json()}
const abortError=()=>new DOMException('已关闭当前页面的等待，任务仍可在后台查看。','AbortError');
function abortable<T>(promise:Promise<T>,signal?:AbortSignal|null):Promise<T>{
 if(!signal)return promise;
 return new Promise((resolve,reject)=>{const abort=()=>reject(abortError());signal.addEventListener('abort',abort,{once:true});promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));if(signal.aborted)abort()});
}
function sleepForRun(ms:number,signal:AbortSignal):Promise<void>{return new Promise((resolve,reject)=>{if(signal.aborted){reject(abortError());return}const stop=()=>{clearTimeout(timer);signal.removeEventListener('abort',stop);reject(abortError())};const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve()},ms);signal.addEventListener('abort',stop,{once:true})})}
async function awaitLegacyResult<T>(run:LegacyHarnessRun,init:RequestInit,generation:number,task:LegacyHarnessTask,origin:string):Promise<T>{
 const controller=new AbortController(),stop=()=>controller.abort();
 init.signal?.addEventListener('abort',stop,{once:true});externalPending.add(stop);window.addEventListener('pagehide',stop);window.addEventListener('ontology:scope-cleared',stop);
 if(init.signal?.aborted)controller.abort();
 try{
  let failures=0;
  while(true){
   if(controller.signal.aborted||generation!==scopeGeneration)throw abortError();
   let state:LegacyHarnessRun;
   try{state=await directApi<LegacyHarnessRun>('/harness/runs/'+encodeURIComponent(run.id),{signal:controller.signal});failures=0}
   catch(error:any){if(error.name==='AbortError'||[401,403,404,409,422].includes(error.status))throw error;if(++failures>=3)throw new ApiError('进度连接暂时中断，任务仍可从顶部后台任务查看。',503);await sleepForRun(1500,controller.signal);continue}
   if(controller.signal.aborted||generation!==scopeGeneration)throw abortError();
   if(!['queued','running','cancelling'].includes(state.status)){
    window.dispatchEvent(new Event('ontology:harness-runs-changed'));
    window.dispatchEvent(new CustomEvent('ontology:legacy-harness-completed',{detail:{run_id:state.id,operation:task.operation,entity_id:task.entity_id,origin}}));
    if(state.status==='completed'&&Object.prototype.hasOwnProperty.call(state,'result'))return state.result as T;
    if(state.status==='cancelled')throw new ApiError('本轮处理已停止。',409,{code:'harness_cancelled'});
    const message=state.error?.message||state.message||'本轮处理未完成，请回到业务页面核对后重试。';
    throw new ApiError(message.replace(/Harness/g,'智能助手'),502,{code:state.error?.code||'harness_failed'});
   }
   await sleepForRun(1000,controller.signal);
  }
 }finally{init.signal?.removeEventListener('abort',stop);externalPending.delete(stop);window.removeEventListener('pagehide',stop);window.removeEventListener('ontology:scope-cleared',stop)}
}
export async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{
 const task=legacyHarnessTask(path,init);
 if(!task)return directApi<T>(path,init);
 if(task.operation==='authority_assist'){const target=window.location.pathname.match(/^\/authority\/([A-Za-z0-9_-]{1,191})$/);if(target)task.entity_id=target[1]}
 if(init.signal?.aborted)throw abortError();
 const generation=scopeGeneration,origin=window.location.pathname+window.location.search+window.location.hash;
 let scopeCleared=false;const clear=()=>{scopeCleared=true};window.addEventListener('ontology:scope-cleared',clear,{once:true});
 // Client cancellation only stops waiting. The accepted server task owns its
 // lifetime; scope invalidation still aborts this authenticated submission.
 const submission=directApi<LegacyHarnessRun>('/harness/tasks',{...init,signal:undefined,body:JSON.stringify(task)}).then(run=>{
  if(scopeCleared||generation!==scopeGeneration)throw abortError();
  if(!run||!/^([a-f0-9]{32})$/.test(run.id))throw new ApiError('后台任务未正确建立，请稍后重试。',502);
  const text=['question','instruction','text','content','context','query'].map(key=>task.body[key]).find(value=>typeof value==='string'&&value.trim());
  window.dispatchEvent(new CustomEvent<LegacyHarnessStarted>('ontology:harness-run-started',{detail:{run,operation:task.operation,instruction:typeof text==='string'?text.slice(0,20000):run.title,origin}}));
  return run;
 }).finally(()=>window.removeEventListener('ontology:scope-cleared',clear));
 const run=await abortable(submission,init.signal);
 return awaitLegacyResult<T>(run,init,generation,task,origin);
}
export function useApi<T=any>(path:string,deps:any[]=[]){const [data,setData]=useState<T|null>(null),[loading,setLoading]=useState(!!path),[refreshing,setRefreshing]=useState(false),[error,setError]=useState('');const [tick,setTick]=useState(0);const last=useRef('');const loaded=useRef(false);const reload=useCallback(()=>setTick(n=>n+1),[]);
 useEffect(()=>{let live=true;const controller=new AbortController();if(!path){setLoading(false);setRefreshing(false);setData(null);setError('');return}const changed=last.current!==path;last.current=path;if(changed){loaded.current=false;setData(null)}setLoading(!loaded.current);setRefreshing(loaded.current);setError('');api<T>(path,{signal:controller.signal}).then(d=>{if(live){setData(d);loaded.current=true}}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message)}).finally(()=>{if(live){setLoading(false);setRefreshing(false)}});return()=>{live=false;controller.abort()}},[path,tick,...deps]);return{data,loading,refreshing,error,reload};}
export const statusLabel=(s:string)=>({draft:'整理中',review:'待审核',approved:'已审核',published:'已发布',returned:'待补充',archived:'已归档',completed:'已完成',candidate:'待核关联',confirmed:'已确认',source_verified:'来源文本一致',demo:'演示关联',rejected:'已排除',running:'处理中',pending:'待处理',failed:'处理失败',queued:'等待处理',ready:'可使用',active:'进行中'} as any)[s]||s;
export const kindLabel=(s:string)=>OBJECT_NAMES[s]||({analysis:'分析记录'} as any)[s]||s;
export {eventLabel};
export function formatDate(s:string){if(!s)return'—';try{return new Date(s).toLocaleDateString('zh-CN')}catch{return s}}
