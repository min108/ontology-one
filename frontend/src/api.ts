import {useCallback,useEffect,useRef,useState} from 'react';
import {eventLabel,OBJECT_NAMES} from './domain';
export async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{
 const headers:any={...(init.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(init.headers||{})};
 const r=await fetch(path.startsWith('/api')?path:'/api'+path,{...init,headers});
 if(!r.ok){let d:any;try{d=await r.json()}catch{};throw new Error(typeof d?.detail==='string'?d.detail:`请求未完成（${r.status}），请稍后重试。`)}
 return r.json();
}
export function useApi<T=any>(path:string,deps:any[]=[]){const [data,setData]=useState<T|null>(null),[loading,setLoading]=useState(!!path),[refreshing,setRefreshing]=useState(false),[error,setError]=useState('');const [tick,setTick]=useState(0);const last=useRef('');const loaded=useRef(false);const reload=useCallback(()=>setTick(n=>n+1),[]);
 useEffect(()=>{let live=true;if(!path){setLoading(false);setRefreshing(false);setData(null);return}const changed=last.current!==path;last.current=path;if(changed){loaded.current=false;setData(null)}setLoading(!loaded.current);setRefreshing(loaded.current);setError('');api<T>(path).then(d=>{if(live){setData(d);loaded.current=true}}).catch(e=>{if(live)setError(e.message)}).finally(()=>{if(live){setLoading(false);setRefreshing(false)}});return()=>{live=false}},[path,tick,...deps]);return{data,loading,refreshing,error,reload};}
export const statusLabel=(s:string)=>({draft:'整理中',review:'待审核',approved:'已审核',published:'已发布',returned:'待补充',archived:'已归档',completed:'已完成',candidate:'待核关联',confirmed:'已确认',source_verified:'来源文本一致',demo:'演示关联',rejected:'已排除',running:'处理中',pending:'待处理',failed:'处理失败',queued:'等待处理',ready:'可使用',active:'进行中'} as any)[s]||s;
export const kindLabel=(s:string)=>OBJECT_NAMES[s]||({analysis:'分析记录'} as any)[s]||s;
export {eventLabel};
export function formatDate(s:string){if(!s)return'—';try{return new Date(s).toLocaleDateString('zh-CN')}catch{return s}}
