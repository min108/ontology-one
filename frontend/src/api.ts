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
export async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{const r=await request(path,init);return r.status===204?null as T:r.json()}
export function useApi<T=any>(path:string,deps:any[]=[]){const [data,setData]=useState<T|null>(null),[loading,setLoading]=useState(!!path),[refreshing,setRefreshing]=useState(false),[error,setError]=useState('');const [tick,setTick]=useState(0);const last=useRef('');const loaded=useRef(false);const reload=useCallback(()=>setTick(n=>n+1),[]);
 useEffect(()=>{let live=true;const controller=new AbortController();if(!path){setLoading(false);setRefreshing(false);setData(null);setError('');return}const changed=last.current!==path;last.current=path;if(changed){loaded.current=false;setData(null)}setLoading(!loaded.current);setRefreshing(loaded.current);setError('');api<T>(path,{signal:controller.signal}).then(d=>{if(live){setData(d);loaded.current=true}}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message)}).finally(()=>{if(live){setLoading(false);setRefreshing(false)}});return()=>{live=false;controller.abort()}},[path,tick,...deps]);return{data,loading,refreshing,error,reload};}
export const statusLabel=(s:string)=>({draft:'整理中',review:'待审核',approved:'已审核',published:'已发布',returned:'待补充',archived:'已归档',completed:'已完成',candidate:'待核关联',confirmed:'已确认',source_verified:'来源文本一致',demo:'演示关联',rejected:'已排除',running:'处理中',pending:'待处理',failed:'处理失败',queued:'等待处理',ready:'可使用',active:'进行中'} as any)[s]||s;
export const kindLabel=(s:string)=>OBJECT_NAMES[s]||({analysis:'分析记录'} as any)[s]||s;
export {eventLabel};
export function formatDate(s:string){if(!s)return'—';try{return new Date(s).toLocaleDateString('zh-CN')}catch{return s}}
