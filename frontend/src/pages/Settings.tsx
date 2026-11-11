import {useState} from 'react';
import {Link} from 'react-router-dom';
import {CheckCircle2, Download, HardDrive, PlugZap, ShieldCheck, Sparkles} from 'lucide-react';
import {useAuth} from '../auth';
import {AccessButton} from '../components/AccessButton';
import {HarnessConfigurationPanel, HarnessStatus} from '../components/HarnessConfiguration';
import {request, useApi} from '../api';
import {PageHeader, Card, Badge, useToast} from '../components/ui';

export default function Settings(){
  const {session}=useAuth(),canManage=!!session?.permissions.manage_users,canPackage=!!session?.user.is_superadmin;
  const model=useApi<HarnessStatus>('/skills/harness/status'),health=useApi<any>('/health');
  const [backing,setBacking]=useState(false),[packaging,setPackaging]=useState(false);const toast=useToast();
  async function download(path:string,filename:string,kind:'backup'|'package'){
    const setBusy=kind==='backup'?setBacking:setPackaging;setBusy(true);
    try{const result=await request(path);const url=URL.createObjectURL(await result.blob());const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(kind==='backup'?'当前机构工作区备份已生成。':'程序源码包已生成。Windows 使用请下载完整便携运行包。')}
    catch(e:any){if(e.name!=='AbortError')toast(e.message||'导出未完成，请稍后重试。','error')}
    finally{setBusy(false)}
  }
  return <div className="page"><PageHeader eyebrow="运行与维护" title="系统设置" description="管理本机构的统一模型接入、运行状态与工作区备份。"/>
    <div className="settings-grid"><div className="stack">
      <Card><div className="section-title"><h2><Sparkles size={19} style={{marginRight:10,color:'var(--brand)'}}/>统一 Harness 模型接入</h2><Badge tone={model.data?.configured?'green':'amber'}>{model.data?.configured?'已配置':'待配置'}</Badge></div>
        {model.error?<div role="alert"><p className="harness-config-error">{model.error}</p><button className="btn" onClick={model.reload}>重新加载</button></div>:<HarnessConfigurationPanel status={model.data} onSaved={model.reload}/>}
      </Card>
      <Card><div className="section-title"><h2>AI 参与业务的方式</h2></div><div className="ai-capability-grid">{[
        {title:'本体智能应用',copy:'围绕业务问题，按授权查阅本体、加载已发布技能并查询数据来源。',path:'/applications'},
        {title:'Agent Skill 建设',copy:'辅助编写技能说明、脚本与合成用例，核对提案后进入治理流程。',path:'/skill-development'},
        {title:'材料整理与资产萃取',copy:'处理选定原文，提出有来源的结构与候选成果。',path:'/materials'},
        {title:'字段识别与标准补齐',copy:'建议字段含义、单位与对象标识，由建设人员确认。',path:'/datasets'},
        {title:'访谈与经验整理',copy:'根据原话追问、归纳经验，保留相应引用。',path:'/experience'},
        {title:'关联与任务建议',copy:'提出资产关联和工作任务建议，由业务人员核对后采纳。',path:'/projects'},
      ].map(item=><Link className="ai-capability-link" key={item.path} to={item.path}><Sparkles size={16}/><div><strong>{item.title}</strong><p>{item.copy}</p></div></Link>)}</div><p className="small muted" style={{marginTop:16,marginBottom:0}}>本体应用以只读方式运行。建设类 AI 输出保留为建议或草稿，保存、审核和发布沿用平台权限与流程。</p></Card>
      {canManage&&<Card className="ia-settings-link"><div><h2><PlugZap size={19}/>集成应用与 MCP 接入</h2><p>登记上层调用方，绑定本机构委托账号，管理工具、映射范围和独立应用令牌。</p></div><Link className="btn" to="/integration-applications">管理集成应用</Link></Card>}
      <Card><h2>当前运行方式</h2><dl className="meta-list"><dt>身份与授权</dt><dd>真实账号登录，按当前机构、角色及源系统授权访问</dd><dt>业务成果</dt><dd>保存在本机构数据库与文件目录，修订保留历史版本</dd><dt>智能体会话</dt><dd>提问、回答和源端查询结果在受控临时会话中处理，结束或过期后清除</dd><dt>调用记录</dt><dd>保留工具名称、耗时、状态和数据归属等审计元数据</dd></dl></Card>
    </div><div className="stack">
      <Card><h2>服务状态</h2><div className="check-row"><CheckCircle2 size={17}/><span>平台业务服务</span><Badge tone={health.data?'green':health.error?'amber':'neutral'}>{health.data?'运行中':health.error?'连接异常':'检测中'}</Badge></div><div className="check-row"><HardDrive size={17}/><span>工作区持久化</span><Badge tone="green">已启用</Badge></div><div className="check-row"><ShieldCheck size={17}/><span>权限与操作审计</span><Badge tone="green">已启用</Badge></div><div className="check-row"><PlugZap size={17}/><span>统一模型服务</span><Badge tone={model.data?.verified||model.data?.online_verified?'green':'amber'}>{model.data?.verified||model.data?.online_verified?'连接已验证':model.data?.configured?'待验证':'待配置'}</Badge></div></Card>
      <Card><h2>工作区备份</h2><p className="small muted">导出当前机构业务记录与上传原件。模型密钥、登录会话和智能体临时上下文不进入备份。</p><AccessButton className="btn btn-primary" disabled={backing||!canManage} onClick={()=>void download('/system/backup','Ontology One-工作区备份.zip','backup')}><Download size={15}/>{backing?'正在生成备份…':'导出工作区备份'}</AccessButton><p className="small muted" style={{marginTop:18}}>恢复前关闭平台并保留原目录。恢复完成后核对机构成果与版本，并重新配置模型接入。</p></Card>
      <Card><h2>Windows 便携运行</h2><p className="small muted">使用正式发行的完整 Windows 运行包，解压后通过“启动平台.bat”和“关闭平台.bat”启停。运行包自带所需组件。</p><a className="btn" href="https://github.com/min108/ontology-one/releases" target="_blank" rel="noopener noreferrer">查看平台发行包</a>{canPackage&&<><div style={{borderTop:'1px solid var(--line)',paddingTop:18,marginTop:20}}><h3>程序源码包</h3><p className="small muted">供代码维护与自行构建使用，包含程序与编译界面；完整 Windows 运行包请从上方发行入口获取。</p><button className="btn" disabled={packaging} onClick={()=>void download('/system/release','Ontology One-程序源码包.zip','package')}><Download size={15}/>{packaging?'正在生成…':'导出程序源码包'}</button></div></>}</Card>
    </div></div>
  </div>;
}
