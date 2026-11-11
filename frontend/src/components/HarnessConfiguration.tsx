import {FormEvent, useEffect, useRef, useState} from 'react';
import {CheckCircle2, RefreshCw, ShieldCheck} from 'lucide-react';
import {api} from '../api';
import {useAuth} from '../auth';
import {Badge} from './ui';
import './harness-configuration.css';

export type HarnessStatus = {
  available?: boolean; configured?: boolean; verified?: boolean; online_verified?: boolean;
  version?: string; sdk_version?: string; reason?: string; base_url?: string; model?: string;
};
export function HarnessConfigurationPanel({status,onSaved}:{status:HarnessStatus|null;onSaved:()=>void}) {
  const {session}=useAuth();
  const canManage=!!session?.permissions.manage_users&&!session.workspace.readonly;
  const canInvoke=session?.workspace.tenant_id===session?.user.home_tenant_id;
  const [base,setBase]=useState(status?.base_url||'https://api.deepseek.com/anthropic');
  const [model,setModel]=useState(status?.model||'deepseek-flash');
  const [key,setKey]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState(''),[clearKey,setClearKey]=useState(false);
  const current=useRef(status); const live=useRef(true); const pending=useRef<AbortController|null>(null);
  useEffect(()=>{if(status&&!current.current){setBase(status.base_url||'https://api.deepseek.com/anthropic');setModel(status.model||'deepseek-flash')}current.current=status},[status]);
  useEffect(()=>{live.current=true;const clear=()=>{setKey('');pending.current?.abort()};window.addEventListener('pagehide',clear);window.addEventListener('ontology:scope-cleared',clear);return()=>{live.current=false;pending.current?.abort();window.removeEventListener('pagehide',clear);window.removeEventListener('ontology:scope-cleared',clear)}},[]);
  async function run(action:'save'|'test') {
    if(busy||!canManage||(action==='test'&&!canInvoke))return;
    const controller=new AbortController();pending.current=controller;setBusy(action);setError('');setMessage('');
    try {
      const result=await api<any>('/skills/harness/'+(action==='save'?'config':'test'),{method:'POST',signal:controller.signal,...(action==='save'?{body:JSON.stringify({base_url:base.trim(),model:model.trim(),...(clearKey?{clear_key:true}:key?{api_key:key}:{})})}:{})});
      if(!live.current)return;
      setKey('');setClearKey(false);setMessage(action==='save'?(clearKey?'已清除本机构保存的模型密钥。后续在线调用须重新配置密钥。':'统一模型配置已保存，请验证连接。'):result.message||'Harness 模型连接验证成功。');
      onSaved();window.dispatchEvent(new Event('ontology:harness-configured'));
    }catch(e:any){if(live.current&&e.name!=='AbortError')setError(e.message||'模型服务操作未完成，请稍后重试。')}
    finally{if(live.current)setBusy('');pending.current=null}
  }
  function save(e:FormEvent){e.preventDefault();void run('save')}
  const verified=status?.verified||status?.online_verified;
  return <form className="harness-config-form" onSubmit={save}>
    <div className="harness-config-state"><Badge tone={verified?'green':status?.configured?'blue':'amber'}>{verified?'连接已验证':status?.configured?'已配置，待验证':'尚未配置'}</Badge><span>{status?.model||'DeepSeek Harness'}{(status?.version||status?.sdk_version)&&' · '+(status.version||status.sdk_version)}</span></div>
    <p className="harness-config-copy">技能开发、AI 辅助和本体应用共用本机构的模型接入。所选材料、提问和获准查询结果可能发送至此服务；请按本机构数据使用要求配置。</p>
    <label className="form-field"><span>模型服务地址</span><input className="input" aria-label="Harness服务地址" type="url" required disabled={!canManage||!!busy} value={base} onChange={e=>setBase(e.target.value)}/><small>填写 Messages 兼容地址，例如 https://api.deepseek.com/anthropic。</small></label>
    <label className="form-field"><span>模型名称</span><input className="input" aria-label="Harness模型名称" required maxLength={200} disabled={!canManage||!!busy} value={model} onChange={e=>setModel(e.target.value)}/></label>
    <label className="form-field"><span>模型接口密钥</span><input className="input" aria-label="Harness接口密钥" type="password" autoComplete="new-password" disabled={!canManage||!!busy||clearKey} maxLength={4096} placeholder={status?.configured?'已保存，留空保留':'由本机构管理员填写'} value={key} onChange={e=>setKey(e.target.value)}/><small>保存后不回显。更换服务地址时须重新填写密钥。</small></label>
    {canManage&&status?.configured&&<label className="harness-config-clear"><input type="checkbox" checked={clearKey} disabled={!!busy} onChange={event=>{setClearKey(event.target.checked);setKey('')}}/><span>清除已保存的模型密钥</span></label>}
    {error&&<p className="harness-config-error" role="alert">{error}</p>}
    {message&&<p className="harness-config-success" role="status"><CheckCircle2 size={15}/>{message}</p>}
    {canManage?<div className="toolbar"><button className="btn btn-primary" disabled={!!busy||!base.trim()||!model.trim()}>{busy==='save'?'正在保存…':'保存统一配置'}</button><button type="button" className="btn" disabled={!!busy||!status?.configured||!canInvoke} onClick={()=>void run('test')}><RefreshCw size={14}/>{busy==='test'?'正在验证…':'验证 Harness 连接'}</button></div>:<p className="harness-config-copy"><ShieldCheck size={15}/>模型接入由本机构管理员管理。</p>}
    {!canInvoke&&<p className="harness-config-copy">当前为跨机构运维。模型调用须从账号所属机构发起。</p>}
    {status?.available===false&&status.reason&&<p className="harness-config-copy">{status.reason}</p>}
  </form>;
}
