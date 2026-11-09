import {Link} from 'react-router-dom';
import {ArrowUpRight,Building2,UserRound,Workflow} from 'lucide-react';
import {useApi} from '../api';
import {ParagraphText} from './ui';
import {AuthorityFlowEditor} from './AuthorityFlowEditor';

/** A rule keeps one identity in the specialist workspace and asset library. */
export function AuthorityRuleSummary({entity}:{entity:any}){
 const q=useApi<any>('/authority/catalog');const spec=entity.payload.authority_spec;
 const organizations=q.data?.organizations||[],people=q.data?.people||[];
 return <section className="asset-section"><div className="asset-section-heading"><div><h2><Workflow size={17}/>{spec.type==='workflow'?'业务审批流程':'岗位权责规范'}</h2><p>本卡片属于规则资产，编排、审核及历史版本共用同一编号。</p></div></div><div className="asset-section-content">
 <div className="asset-pill-row"><Link className="btn btn-primary" to={'/authority/'+encodeURIComponent(entity.id)}><Workflow size={15}/>{entity.payload.snapshot_readonly?'打开现行权责卡片':'进入权责规范工作页'}<ArrowUpRight size={14}/></Link></div>
 <dl className="asset-info-grid"><div><dt>适用范围</dt><dd><ParagraphText text={spec.scope||entity.summary||'待补充'}/></dd></div><div><dt>依据与行权边界</dt><dd><ParagraphText text={spec.basis||'待核对依据'}/></dd></div><div><dt>适用起止</dt><dd>{spec.effective_from||'待明确'} — {spec.effective_to||'未设终止日'}</dd></div><div><dt>管理目标</dt><dd><ParagraphText text={spec.purpose||'待补充'}/></dd></div></dl>
 <div className="asset-pill-row">{(spec.org_ids||[]).map((id:string)=><Link className="btn btn-ghost" key={id} to={'/assets/'+encodeURIComponent(id)}><Building2 size={14}/>{organizations.find((x:any)=>x.id===id)?.title||id}</Link>)}{(spec.person_ids||[]).map((id:string)=><Link className="btn btn-ghost" key={id} to={'/authority?person='+encodeURIComponent(id)}><UserRound size={14}/>{people.find((x:any)=>x.id===id)?.title||id}</Link>)}</div>
 {spec.type==='workflow'&&<AuthorityFlowEditor spec={spec} onChange={()=>{}} organizations={organizations} people={people} readOnly/>}
 {(spec.sections||[]).map((s:any)=><div className="knowledge-article-section" key={s.id}><h3>{s.title}</h3><ParagraphText text={s.content}/></div>)}
 {!!spec.source_refs?.length&&<details className="knowledge-chapter-sources"><summary>引用依据 · {spec.source_refs.length} 处</summary>{spec.source_refs.map((r:any,i:number)=><div key={i}><blockquote>{r.quote}</blockquote><Link to={'/materials/'+encodeURIComponent(r.material_id)+'?'+new URLSearchParams({quote:r.quote,...(r.source_version?{source_version:r.source_version}:{})})}>核对原始材料<ArrowUpRight size={13}/></Link></div>)}</details>}
 </div></section>;
}
