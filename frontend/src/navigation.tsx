import {createContext,useContext,useEffect,useLayoutEffect,useRef,useState,useCallback,ReactNode} from 'react';
import {Link,useLocation,useNavigate} from 'react-router-dom';
import {ArrowLeft,ChevronRight,CornerUpLeft,FolderOpen} from 'lucide-react';
import {useApi} from './api';
import {entityHref,routeLabel} from './domain';
import './navigation.css';

type Visit={id:string;path:string;label:string;parent?:string;scroll:number;at:number;entered?:boolean};
const KEY='ontology-one-navigation';
const TOKEN='via';
const cache:Record<string,Visit>={};
let navigationScope=0;
window.addEventListener('ontology:scope-cleared',()=>{navigationScope++;for(const key of Object.keys(cache))delete cache[key]});
function persist(){try{const records=Object.values(cache).sort((a,b)=>b.at-a.at).slice(0,250);localStorage.setItem(KEY,JSON.stringify(Object.fromEntries(records.map(r=>[r.id,r]))))}catch{}}
function uid(){return 'v'+Date.now().toString(36)+Math.random().toString(36).slice(2,8)}
function clean(path:string){const u=new URL(path,window.location.origin);u.searchParams.delete(TOKEN);return u.pathname+u.search+u.hash}
function href(v:Visit){const u=new URL(v.path,window.location.origin);u.searchParams.set(TOKEN,v.id);return u.pathname+u.search+u.hash}
function title(){return document.querySelector('main h1')?.textContent?.trim()||''}
function entityId(path:string){const u=new URL(path,window.location.origin);const m=u.pathname.match(/^\/(?:assets|authority|materials|datasets|projects|tasks)\/([^/]+)$/)||u.pathname.match(/^\/knowledge\/collections\/([^/]+)$/);return m?decodeURIComponent(m[1]):u.pathname==='/experience'?(u.searchParams.get('record')||u.searchParams.get('session')||u.searchParams.get('id')||''):''}
const NavigationContext=createContext<{visit:Visit|null;ancestors:Visit[];focusInitialElement:(id:string,block?:ScrollLogicalPosition)=>()=>void}>({visit:null,ancestors:[],focusInitialElement:()=>()=>{}});
export function useInitialDocumentFocus(){return useContext(NavigationContext).focusInitialElement}

/** URL state survives contextual returns, reloads, and sharing a work view. */
export function usePageState<T extends Record<string,string>>(defaults:T):[T,(changes:Partial<T>,replace?:boolean)=>void]{
 const location=useLocation(),navigate=useNavigate();const current=useRef(location.search);const last=useRef(location.search);
 if(last.current!==location.search){current.current=location.search;last.current=location.search}
 const p=new URLSearchParams(location.search);const values={...defaults};for(const key of Object.keys(defaults))values[key as keyof T]=(p.get(key)??defaults[key]) as T[keyof T];
 const update=(changes:Partial<T>,replace=true)=>{const next=new URLSearchParams(current.current);Object.entries(changes).forEach(([key,value])=>value?next.set(key,String(value)):next.delete(key));current.current='?'+next.toString();navigate({pathname:location.pathname,search:current.current,hash:location.hash},{replace})};return [values,update];
}

export function useSessionValue<T>(key:string,initial:T):[T,React.Dispatch<React.SetStateAction<T>>]{
 const storageKey='ontology-one-draft-'+key;
 const [value,setValue]=useState<T>(()=>{try{const stored=sessionStorage.getItem(storageKey);return stored?JSON.parse(stored):initial}catch{return initial}});
 useEffect(()=>{try{sessionStorage.setItem(storageKey,JSON.stringify(value))}catch{}},[storageKey,value]);return [value,setValue];
}

/** Capture where a visit came from without interrupting its business handlers. */
export function NavigationProvider({children}:{children:ReactNode}){
 const location=useLocation(),navigate=useNavigate();
 const enteredScope=useRef(navigationScope).current;
 const active=useRef<Visit|null>(null),pending=useRef<{target:string;parent?:string}|null>(null),restoring=useRef(false);
 const scrollVisit=useRef<{id:string;mode:'new'|'return';interacted:boolean;focused:Set<string>;cancel:Set<()=>void>}|null>(null);
 const [visit,setVisit]=useState<Visit|null>(null);
 const mounted=useRef(false);
 const stopAutomatic=useCallback(()=>{
  const session=scrollVisit.current;if(!session)return;
  session.interacted=true;for(const stop of [...session.cancel])stop();restoring.current=false;
 },[]);
 // Observe asynchronous content until it is usable, then move once. Any user
 // scroll intent cancels the pending move; data loading never owns the scroll.
 const schedule=useCallback((attempt:(final:boolean)=>boolean)=>{
  const session=scrollVisit.current;if(!session||session.interacted)return ()=>{};
  let stopped=false,frame=0,settleFrame=0;
  const body=document.querySelector('main')||document.body;
  const stop=()=>{if(stopped)return;stopped=true;cancelAnimationFrame(frame);cancelAnimationFrame(settleFrame);window.clearTimeout(deadline);mutation.disconnect();resize.disconnect();session.cancel.delete(stop);restoring.current=false};
  const tryMove=(final=false)=>{
   if(stopped)return;if(scrollVisit.current!==session||session.interacted){stop();return}
   if(!final&&body.querySelector('.loading-page,.loading-region,[aria-busy="true"]'))return;
   if(attempt(final)||final)stop();
  };
  const queue=()=>{cancelAnimationFrame(frame);cancelAnimationFrame(settleFrame);frame=requestAnimationFrame(()=>{settleFrame=requestAnimationFrame(()=>tryMove())})};
  const mutation=new MutationObserver(queue),resize=new ResizeObserver(queue);
  mutation.observe(body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','aria-busy']});resize.observe(body);
  const deadline=window.setTimeout(()=>tryMove(true),8000);
  session.cancel.add(stop);queue();return stop;
 },[]);
 const focusInitialElement=useCallback((id:string,block:ScrollLogicalPosition='center')=>{
  const session=scrollVisit.current;
  if(!session||session.mode==='return'||session.interacted||session.focused.has(id))return ()=>{};
  return schedule(()=>{if(session.focused.has(id))return true;const element=document.getElementById(id);if(!element)return false;session.focused.add(id);element.scrollIntoView({block,behavior:'instant'});return true});
 },[schedule]);
 useEffect(()=>{
  mounted.current=true;
  const previousRestoration=window.history.scrollRestoration;window.history.scrollRestoration='manual';
  const remember=()=>{if(enteredScope!==navigationScope)return;const v=active.current;if(!v||restoring.current)return;v.scroll=window.scrollY;v.label=title()||v.label;v.at=Date.now();cache[v.id]=v;persist()};
  let tick:number|undefined;
  const onScroll=()=>{const v=active.current;if(v&&!restoring.current)v.scroll=window.scrollY;window.clearTimeout(tick);tick=window.setTimeout(remember,120)};
  const interrupt=(e:Event)=>{if(e instanceof KeyboardEvent&&!['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))return;stopAutomatic()};
  const onClick=(e:MouseEvent)=>{
   const anchor=(e.target as Element)?.closest?.('a[href]') as HTMLAnchorElement|null;
   if(!anchor||anchor.hasAttribute('download')||anchor.dataset.navigation==='off')return;
   const u=new URL(anchor.href,window.location.href);if(u.origin!==window.location.origin||!u.pathname.startsWith('/')||u.pathname.startsWith('/api/'))return;
   const target=clean(u.href),current=active.current;
   if(!current||u.pathname===new URL(current.path,window.location.origin).pathname)return;
   remember();
   if(u.searchParams.has(TOKEN)&&cache[u.searchParams.get(TOKEN)!]){pending.current=null;return}
   const parent=anchor.closest('.sidebar')?undefined:current.id;
   if(e.ctrlKey||e.metaKey||e.shiftKey||anchor.target==='_blank'){
    const next:Visit={id:uid(),path:target,label:routeLabel(target),parent,scroll:0,at:Date.now(),entered:false};cache[next.id]=next;persist();anchor.href=href(next);return;
   }
   pending.current={target,parent};
  };
  const prepareNative=(e:MouseEvent)=>{
   if(e.type==='pointerdown'&&e.button!==1)return;
   const anchor=(e.target as Element)?.closest?.('a[href]') as HTMLAnchorElement|null;
   if(!anchor||anchor.hasAttribute('download')||anchor.dataset.navigation==='off')return;
   const u=new URL(anchor.href,window.location.href),current=active.current;
   if(!current||u.origin!==window.location.origin||u.pathname.startsWith('/api/')||clean(u.href)===current.path)return;
   remember();if(u.searchParams.get(TOKEN)&&cache[u.searchParams.get(TOKEN)!])return;
   const next:Visit={id:uid(),path:clean(u.href),label:routeLabel(u.pathname),parent:anchor.closest('.sidebar')?undefined:current.id,scroll:0,at:Date.now(),entered:false};cache[next.id]=next;persist();anchor.href=href(next);
  };
  document.addEventListener('click',onClick,true);document.addEventListener('pointerdown',prepareNative,true);document.addEventListener('contextmenu',prepareNative,true);window.addEventListener('scroll',onScroll,{passive:true});window.addEventListener('pagehide',remember);
  for(const type of ['wheel','touchstart','pointerdown','keydown'])window.addEventListener(type,interrupt,{capture:true,passive:true});
  return()=>{document.removeEventListener('click',onClick,true);document.removeEventListener('pointerdown',prepareNative,true);document.removeEventListener('contextmenu',prepareNative,true);window.removeEventListener('scroll',onScroll);window.removeEventListener('pagehide',remember);window.clearTimeout(tick);for(const type of ['wheel','touchstart','pointerdown','keydown'])window.removeEventListener(type,interrupt,true);mounted.current=false;queueMicrotask(()=>{if(!mounted.current)stopAutomatic()});window.history.scrollRestoration=previousRestoration};
 },[stopAutomatic]);
 useLayoutEffect(()=>{
  const currentPath=clean(location.pathname+location.search+location.hash),params=new URLSearchParams(location.search),token=params.get(TOKEN),old=active.current;
  if(old&&href(old)===location.pathname+location.search+location.hash){setVisit({...old});return}
  const intended=pending.current,explicit=token?cache[token]:undefined;
  const isNew=!!intended&&clean(intended.target)===currentPath;
  let next:Visit;let restore=false;
  if(explicit&&!isNew){next={...explicit,path:currentPath,entered:true};restore=explicit.entered!==false&&old?.id!==next.id}
  else if(!isNew&&old&&new URL(old.path,window.location.origin).pathname===location.pathname){next={...old,path:currentPath,at:Date.now()}}
  else{next={id:uid(),path:currentPath,label:routeLabel(currentPath),parent:isNew?intended?.parent:old?.id,scroll:0,at:Date.now(),entered:true}}
  pending.current=null;active.current=next;cache[next.id]=next;persist();setVisit({...next});
  if(old?.id!==next.id){
   stopAutomatic();scrollVisit.current={id:next.id,mode:restore?'return':'new',interacted:false,focused:new Set(),cancel:new Set()};
   const target=restore?next.scroll:0,hash=new URL(next.path,window.location.origin).hash;
   // Reset a new route only once, before paint; never replay timed scrollTo(0).
   restoring.current=true;window.scrollTo({left:0,top:0,behavior:'instant'});
   if(restore&&target>0){
    schedule(final=>{const maximum=Math.max(0,document.documentElement.scrollHeight-window.innerHeight);if(maximum<target&&!final)return false;window.scrollTo({left:0,top:Math.min(target,maximum),behavior:'instant'});return true});
   }else{
    restoring.current=false;
    if(!restore&&hash){try{focusInitialElement(decodeURIComponent(hash.slice(1)),'start')}catch{}}
   }
  }
  if(token!==next.id)navigate(href(next),{replace:true,state:location.state});
 },[location.pathname,location.search,location.hash,stopAutomatic,schedule,focusInitialElement]);
 // Updating labels is independent of scroll restoration and cannot steal focus.
 useEffect(()=>{
  const current=active.current;if(!current)return;
  const updateTitle=()=>{if(active.current?.id!==current.id)return;const actual=title();if(actual&&actual!==active.current.label){active.current.label=actual;cache[current.id]=active.current;persist();setVisit({...active.current})}};
  updateTitle();const observer=new MutationObserver(updateTitle);const main=document.querySelector('main');if(main)observer.observe(main,{childList:true,subtree:true,characterData:true});return()=>observer.disconnect();
 },[location.pathname,location.search]);
 const ancestors:Visit[]=[];let p=visit?.parent;const seen=new Set<string>();while(p&&cache[p]&&!seen.has(p)&&ancestors.length<12){seen.add(p);ancestors.unshift(cache[p]);p=cache[p].parent}
 return <NavigationContext.Provider value={{visit,ancestors,focusInitialElement}}>{children}</NavigationContext.Provider>;
}

export function ContextTrail(){
 const {visit,ancestors}=useContext(NavigationContext);const location=useLocation();const id=entityId(location.pathname+location.search);const q=useApi<any>(id?'/navigation/context?entity_id='+encodeURIComponent(id):'');
 const parents=q.data?.parents||[];const recent=ancestors[ancestors.length-1];
 if(!recent&&!parents.length)return null;
 return <div className="context-trail" aria-label="工作来路与业务归属"><div className="context-path">{recent&&<Link className="context-back" to={href(recent)}><ArrowLeft size={15}/><span>返回{recent.label}</span></Link>}{ancestors.length>1&&<div className="context-crumbs">{ancestors.slice(-4,-1).map(v=><span key={v.id}><Link to={href(v)} title={v.label}>{v.label}</Link><ChevronRight size={12}/></span>)}</div>}{visit&&recent&&<span className="context-current" title={visit.label}>{visit.label}</span>}</div>{parents.length>0&&<details className="context-belonging"><summary><FolderOpen size={14}/>所属工作与专栏<ChevronRight size={12}/></summary><div>{parents.map((p:any)=><Link key={p.id} to={entityHref(p.id,p.kind)}><CornerUpLeft size={13}/><span><small>{p.role_label}</small>{p.title}</span></Link>)}</div></details>}</div>;
}
