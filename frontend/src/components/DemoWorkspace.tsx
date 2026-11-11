import {useState} from 'react';
import {Link} from 'react-router-dom';
import {Archive, ArrowRight, CheckCircle2, Download, FlaskConical, RefreshCw, Sparkles} from 'lucide-react';
import {api, useApi} from '../api';
import {useAuth} from '../auth';
import {Badge, Card, Modal, useToast} from './ui';
import './demo-workspace.css';

type DemoItem={id:string;title:string;kind:string;status:string;version:number};
type DemoStatus={loaded:boolean;archived?:boolean;can_clear?:boolean;can_restore?:boolean;protected?:boolean;notice:string;clear_notice?:string;items:DemoItem[];source_id:string;data_standards_published?:boolean;mappings?:{id:string;title:string;status:string}[]};
const exampleQuestions=[
  '查找合同金额大于十万元的模拟合同。十万元整不应纳入；请列出合同编号、金额和核实事项。',
  '按合同编号汇总模拟付款明细，与合同台账的累计付款核对，说明你用的口径和还缺哪些材料。',
  '参考已发布的演示核对说明，解释附件信息为空代表什么，不要把未核实直接当成违规。',
];
const kinds:Record<string,string>={data:'数据标准',rule:'核对要求',skill:'业务技能',knowledge:'工作方法',material:'原始材料',dataset:'模拟台账',project:'验证专题',task:'工作任务'};

export default function DemoWorkspace(){
  const {session}=useAuth(),toast=useToast();
  const q=useApi<DemoStatus>('/demo/status');
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[confirm,setConfirm]=useState<'load'|'clear'|''>('');
  const canManage=!!session?.permissions.manage_users&&!session.workspace.readonly&&session.user.home_tenant_id===session.workspace.tenant_id;
  const state=q.data;
  async function act(action:'load'|'clear'|'prepare-mappings'){
    if(busy||!canManage)return;setBusy(action);setError('');
    try{const result=await api<DemoStatus&{restored?:boolean}>('/demo/'+action,{method:'POST',body:JSON.stringify({confirm:true})});q.reload();setConfirm('');toast(action==='clear'?'演示资料已归档，原件与历史保留。':action==='prepare-mappings'?'台账查询草稿已准备，请完成正常审核。':result.restored?'原有演示资料已恢复。':'模拟资料已加载，未调用模型。')}
    catch(e:any){if(e.name!=='AbortError')setError(e.message||'演示资料操作未完成，请稍后重试。')}
    finally{setBusy('')}
  }
  return <Card className="demo-workspace" id="demo">
    <div className="demo-heading"><div><h2><FlaskConical size={19}/>用模拟资料验证效果</h2><p>少量固定样本，帮助走通资料整理、技能建设、审核和本体对话。</p></div><Badge tone={state?.loaded?'green':state?.archived?'neutral':'blue'}>{state?.loaded?'已加载':state?.archived?'已归档':'按需加载'}</Badge></div>
    <div className="demo-facts"><span>4 份模拟合同</span><span>5 笔模拟付款</span><span>2 份模拟原文</span><span>1 个可测试技能</span><span>6 类工作任务</span></div>
    <p className="demo-note">所有记录均带“演示”标记，不连接真实业务数据库。加载本身不调用模型；后续点击智能处理或发起对话时，才使用本机构已配置的模型服务。</p>
    {(q.error||error)&&<p className="demo-error" role="alert">{error||q.error}</p>}
    {state?.clear_notice&&<p className="demo-note">{state.clear_notice}</p>}
    <div className="demo-actions">
      {!state?.loaded&&canManage&&<button className="btn btn-primary" disabled={!!busy||q.loading||!!state?.archived&&!state.can_restore} onClick={()=>setConfirm('load')}><FlaskConical size={15}/>{busy==='load'?'正在准备…':state?.archived?'恢复原有演示资料':'加载模拟资料'}</button>}
      {state?.loaded&&<Link className="btn btn-primary" to="/assets?q=%E6%BC%94%E7%A4%BA">查看演示资产<ArrowRight size={15}/></Link>}
      {state?.loaded&&canManage&&<button className="btn" disabled={!!busy||!state.can_clear} onClick={()=>setConfirm('clear')}><Archive size={15}/>归档未使用的演示资料</button>}
      <button className="btn btn-ghost" disabled={!!busy||q.loading} onClick={q.reload}><RefreshCw size={14}/>刷新状态</button>
    </div>
    {!canManage&&<p className="demo-note">请由管理员切回所属机构加载或恢复演示资料。</p>}
    <details className="demo-guide" open={state?.loaded||undefined}><summary>按这条路线验证</summary><ol>
      <li><strong>先看素材与台账。</strong>在素材整理查看两份模拟原文，练习阅读分段与有出处的成果提取；在数据台账导入核对两份台账的字段、单位和粒度。</li>
      <li><strong>准备独立审核账号。</strong>首次安装通常只有总部管理员。请在<Link to="/administration">账号与权限</Link>为同一机构创建一个独立的审核账号，由另一位实际使用人设置并保管密码。编辑或提交技能的人不能代替另一人审核。</li>
      <li><strong>完成正常审核。</strong>在业务资产审核发布六项演示数据标准及核对说明；在技能开发打开“演示合同付款余额核对”，运行样例测试，提交后由独立审核账号审核发布。</li>
      <li><strong>准备台账查询。</strong>数据标准发布后，点击下面按钮准备查询草稿。在数据源管理为审核账号授予发现权限，再由另一账号审核发布查询口径；这仍是内置模拟记录，不代表连接了真实数据库。</li>
      <li><strong>开始自由对话。</strong>到<Link to="/applications">本体应用</Link>选择已发布的演示技能、资产和台账查询，提出业务问题。依据、查询来源及处理过程会随任务展示。</li>
    </ol>
      {state?.loaded&&canManage&&<div className="demo-mapping-action"><button className="btn" disabled={!!busy||!state.data_standards_published} onClick={()=>void act('prepare-mappings')}><CheckCircle2 size={15}/>{busy==='prepare-mappings'?'正在准备…':state.mappings?.length?'检查并补齐查询草稿':'准备台账查询草稿'}</button>{!state.data_standards_published&&<small>六项演示数据标准完成审核发布后可用。</small>}{state.source_id&&<Link className="btn btn-ghost" to={'/data-sources/'+encodeURIComponent(state.source_id)}>查看模拟台账<ArrowRight size={14}/></Link>}</div>}
      <details className="demo-questions"><summary>可以直接试问的问题</summary>{exampleQuestions.map(question=><div key={question}><Sparkles size={14}/><p>{question}</p><button className="btn btn-ghost" onClick={()=>navigator.clipboard.writeText(question).then(()=>toast('问题已复制。')).catch(()=>toast('请选中问题文字手动复制。','error'))}>复制</button></div>)}<p className="demo-note">核对基准：严格“大于十万元”只应包含 DEMO-C002、DEMO-C004；付款合计分别为 40000、150000、0、80000 元。附件空值表示未核实。</p></details>
    </details>
    <details className="demo-guide"><summary>下载小样本，自己练习上传</summary><p>这些文件也可在加载前下载。基础样本用于素材整理、台账导入与经验萃取；另外两份扩展样本可按需用于权责流程和专项事项梳理。</p><div className="demo-downloads">{[['payment-rules.md','模拟核对说明'],['interview.md','模拟访谈记录'],['contracts.csv','模拟合同台账'],['payments.csv','模拟付款明细'],['authority-review.md','模拟权责流程'],['special-project.md','模拟专项时间线']].map(([name,title])=><a className="btn" href={'/api/demo/files/'+name} key={name}><Download size={14}/>{title}</a>)}</div></details>
    {state?.loaded&&<details className="demo-guide"><summary>本轮演示资料清单（{state.items.length} 项）</summary><div className="demo-items">{state.items.map(item=><span key={item.id}><small>{kinds[item.kind]||'业务资料'}</small>{item.title}</span>)}</div></details>}
    {confirm&&<Modal title={confirm==='clear'?'归档本轮未使用的演示资料':'在当前机构准备模拟资料'} onClose={()=>{if(!busy)setConfirm('')}}><p>{confirm==='clear'?'仅当本轮演示内容保持原样且未被外部成果引用时，才会整批归档。编号、原件和历史保留，不删除真实业务资料。':'只在当前机构创建带“演示”标记的少量模拟草稿。不会覆盖已有业务资料，也不会自动调用模型或代替人工审核。'}</p><p><strong>当前机构：{session?.workspace.name}</strong></p>{error&&<p className="demo-error" role="alert">{error}</p>}<div className="demo-actions"><button className="btn" disabled={!!busy} onClick={()=>setConfirm('')}>返回</button><button className="btn btn-primary" disabled={!!busy} onClick={()=>void act(confirm)}>{busy?'正在处理…':confirm==='clear'?'确认归档':'确认准备'}</button></div></Modal>}
  </Card>;
}
