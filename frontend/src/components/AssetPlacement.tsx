import {AccessButton} from './AccessButton';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowRight, ArrowUp, CheckCircle2, ChevronRight, FileSearch, GitBranch, Info, Network, Search, ShieldCheck, Sparkles } from 'lucide-react';
import { api, useApi } from '../api';
import {useAuth} from '../auth';
import { AiBadge, Empty, FilterTabs, Modal, useToast } from './ui';
import { AssetEntity, assetRoute, kindLabel, readableStatus, sourceLabel } from '../pages/Assets';

type Candidate = { id: string; title: string; kind: string; version: number; status: string; source_kind: string; domain: string; excerpt: string; locator?: string; chunk_id?: string; origin: string };
type Suggestion = { id: string; target_id: string; target_version: number; direction: string; relation_type: string; reason: string; evidence: string; confidence: string; reviewed?: boolean };
type Proposal = { job_id: string; mode: string; asset_snapshot: AssetEntity; candidates: Candidate[]; suggestions: Suggestion[]; note: string; confirmation?: { created: number; reused: number; reviews?: any[] } };
const relationTypes = [
  { id: 'derived_from', label: '提炼自', sources: ['data', 'rule', 'skill', 'knowledge'], targets: ['material', 'case', 'dataset'] },
  { id: 'based_on', label: '依据规则', sources: ['data', 'skill', 'knowledge'], targets: ['rule'] },
  { id: 'uses_data', label: '使用数据口径', sources: ['skill', 'rule', 'knowledge'], targets: ['data'] },
  { id: 'implements_rule', label: '落实规则', sources: ['skill'], targets: ['rule'] },
  { id: 'informs', label: '提供核查参考', sources: ['knowledge'], targets: ['rule', 'skill'] },
  { id: 'references', label: '业务引用', sources: ['data', 'rule', 'skill', 'knowledge'], targets: ['data', 'rule', 'skill', 'knowledge'] },
  { id: 'related_to', label: '同类业务参考', sources: ['data', 'rule', 'skill', 'knowledge', 'case'], targets: ['data', 'rule', 'skill', 'knowledge', 'case'] },
];
const confidenceLabel: Record<string, string> = { high: '建议匹配较高', medium: '建议匹配中等', low: '建议匹配较低', unassessed: '匹配程度未判断' };

export function AssetPlacement({ entity, onClose, onSaved }: { entity: AssetEntity; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const aiStatus = useApi<{ configured: boolean }>('/ai/status');
  const history = useApi<{ items: any[] }>(`/placement/${encodeURIComponent(entity.id)}/history`);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [links, setLinks] = useState<Suggestion[]>([]);
  const [mode, setMode] = useState('online');
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [tab, setTab] = useState('review');
  const {session}=useAuth();
  const reviewer=session!.user.display_name||session!.user.username;
  const [reviewNote, setReviewNote] = useState('');
  const [search, setSearch] = useState('');
  const [extra, setExtra] = useState<Candidate[]>([]);
  const allCandidates = [...(proposal?.candidates || []), ...extra.filter(item => !proposal?.candidates.some(prior => prior.id === item.id))];
  const selected = links.filter(item => item.reviewed);
  const isDraft = ['draft', 'returned'].includes(entity.status || '');
  const load = (value: Proposal) => { setProposal(value); const audits = value.confirmation?.reviews || []; setLinks(value.suggestions.map(item => { const audit = audits.find(v => v.original_suggestion?.id === item.id); return { ...item, ...(audit?.reviewed_link || {}), id: item.id, reviewed: !!audit }; })); setExtra(audits.flatMap(v => [v.source_snapshot, v.target_snapshot]).filter(v => v?.id && v.id !== entity.id).map(v => ({ ...v, excerpt: typeof v.payload?.content === 'string' ? v.payload.content : v.summary || '', origin: '人工审定时的对象快照' }))); setTab('review'); setError(''); };
  const generate = async () => {
    setBusy('generate'); setError('');
    try { load(await api(`/placement/${encodeURIComponent(entity.id)}/suggest`, { method: 'POST', body: JSON.stringify({ mode, query: focus.trim(), expected_version: entity.version }) })); history.reload(); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(''); }
  };
  const update = (id: string, patch: Partial<Suggestion>) => setLinks(items => items.map(item => item.id === id ? { ...item, ...patch, reviewed: false } : item));
  const searchTargets = async () => {
    if (!search.trim()) return;
    setBusy('search'); setError('');
    try { const response = await api<{ items: Candidate[] }>(`/placement/${encodeURIComponent(entity.id)}/candidates?q=${encodeURIComponent(search.trim())}`); setExtra(response.items); if (!response.items.length) toast('没有匹配的对象，请换用编码或具体业务词'); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(''); }
  };
  const confirm = async () => {
    if (!proposal || !selected.length) return;
    setBusy('confirm'); setError('');
    try {
      const response = await api(`/placement/${encodeURIComponent(entity.id)}/confirm`, { method: 'POST', body: JSON.stringify({ job_id: proposal.job_id, expected_version: proposal.asset_snapshot.version, review_note: reviewNote.trim(), links: selected.map(item => ({ suggestion_id: item.id, target_id: item.target_id, target_version: item.target_version, direction: item.direction, relation_type: item.relation_type, reason: item.reason.trim(), reviewed: true })) }) });
      toast(`已审定：新增 ${response.created} 条关联，复用已确认 ${response.reused} 条`); onSaved(); onClose();
    } catch (e: any) { setError(e.message); } finally { setBusy(''); }
  };
  return <Modal title="资产关联定位与人工审定" onClose={() => { if (!busy) onClose(); }} wide>
    <div className="placement-intro"><div className="placement-symbol"><Network size={25} /></div><div><h3>{entity.title}</h3><p>围绕当前内容 V{entity.version || 1}，寻找制度依据、所需数据、分析方法与经验出处的合适连接。</p></div><AiBadge>AI 关联定位</AiBadge></div>
    <div className="placement-workflow"><span><Search size={14} />检索相关对象</span><ChevronRight size={13} /><span><Sparkles size={14} />提出关联方案</span><ChevronRight size={13} /><span><ShieldCheck size={14} />逐项人工审定</span><ChevronRight size={13} /><span><GitBranch size={14} />写入图谱留痕</span></div>
    {!isDraft && <div className="placement-notice"><Info size={15} />当前可预览建议；请先发起修订为草稿，再审定新增关联。</div>}
    <div className="placement-controls"><FilterTabs label="定位方式" value={mode} onChange={setMode} options={[{ value: 'online', label: 'AI 关联定位', icon: <Sparkles size={13} color="#a51c30" /> }, { value: 'local', label: '本地关联检索' }]} /><div className="placement-query"><input aria-label="关联定位关注点" value={focus} onChange={e => setFocus(e.target.value)} maxLength={500} placeholder="可补充关注点，如：合同履约的校验规则、所需字段与经验出处" /><AccessButton className={`btn ${mode === 'online' ? 'btn-ai' : 'btn-ghost'}`} disabled={!!busy} onClick={generate}>{mode === 'online' ? <Sparkles size={15} /> : <Search size={15} />}{busy === 'generate' ? '正在分析…' : mode === 'online' ? '生成 AI 关联建议' : '检索关联候选'}</AccessButton></div><p>{mode === 'online' ? 'AI 在实际检索候选中比较关联价值，并核对逐字出处；建议不代表事实认定或全图唯一最优解。' : '按本地业务文字检索并提示可用关系类型，不调用在线模型，不判断最佳嵌入位置。'}{mode === 'online' && aiStatus.data && !aiStatus.data.configured && <> <Link to="/settings">尚未配置模型，前往系统设置</Link></>}</p></div>
    {error && <p className="asset-inline-error" role="alert">{error}</p>}
    {!proposal && (history.data?.items || []).length > 0 && <div className="placement-history"><h4>已有定位记录</h4>{history.data!.items.slice(0, 3).map(item => <AccessButton key={item.id} className="placement-history-item" onClick={() => load({ job_id: item.id, ...item.result })}><span>{item.result.mode === 'online' ? 'AI 建议' : '本地检索'} · V{item.result.asset_snapshot.version} · {item.result.suggestions.length} 项</span><span>{item.status === 'completed' ? '已审定 · 查看记录' : '待审定 · 继续复核'} <ArrowRight size={13} /></span></AccessButton>)}</div>}
    {proposal && <>
      <FilterTabs label="本次定位" value={tab} onChange={setTab} options={[{ value: 'review', label: '关联建议', count: links.length }, { value: 'sources', label: '检索依据', count: proposal.candidates.length }]} />
      {proposal.confirmation && <div className="placement-notice"><CheckCircle2 size={15} />该记录已完成审定。新增 {proposal.confirmation.created} 条、复用 {proposal.confirmation.reused} 条；可重新定位生成新一轮建议。</div>}
      {tab === 'sources' ? <div className="placement-source-list">{proposal.candidates.map(item => <article key={item.id}><div><span className="asset-topic-label">{kindLabel(item.kind)}</span><span className="asset-source">{sourceLabel(item.source_kind)}</span><span className="asset-state">{readableStatus(item.status)}</span><span className="asset-code">V{item.version}</span></div><Link to={assetRoute(item.id, item.kind)} target="_blank">{item.title} ↗</Link><small>{item.origin}{item.locator ? ` · ${item.locator}` : ''}</small><p>{item.excerpt || '未提供正文摘录，请打开对象核对。'}</p>{item.chunk_id && <Link to={`/materials/${encodeURIComponent(item.id)}?chunk=${encodeURIComponent(item.chunk_id)}`} target="_blank">定位原文片段 ↗</Link>}</article>)}</div> : <>
        {!!links.length && <div className="placement-map"><div className="placement-map-center"><Network size={18} /><strong>{entity.title}</strong><span>{kindLabel(entity.kind)} · V{entity.version}</span></div><div className="placement-map-branches" style={{ '--placement-cols': Math.min(links.length, 4) } as any}>{links.slice(0, 8).map(item => { const target = allCandidates.find(v => v.id === item.target_id); return <div className={item.reviewed ? 'is-reviewed' : ''} key={item.id}><span>{item.direction === 'incoming' ? <ArrowUp size={12} /> : <ArrowDown size={12} />}{relationTypes.find(t => t.id === item.relation_type)?.label}</span><strong>{target?.title || item.target_id}</strong><small>{item.reviewed ? proposal.confirmation ? '已完成审定' : '已勾选，待提交审定' : '建议连接'}</small></div>; })}</div></div>}
        <p className="placement-boundary"><Info size={14} />{proposal.note} 原有来源关系及候选关系保留原状态。</p>
        {!links.length ? <Empty title="未找到可推荐的关联" description="可补充具体业务关键词，或先完善资产的适用条件和材料出处。" /> : <>
          <div className="placement-search-targets"><Search size={15} /><input aria-label="补充检索关联对象" value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') searchTargets(); }} placeholder="需要调整对象？按资产编码或业务名称补充检索" /><AccessButton className="btn btn-ghost" disabled={!!busy} onClick={searchTargets}>{busy === 'search' ? '检索中…' : '补充检索'}</AccessButton></div>{extra.length > 0 && <p className="placement-search-feedback">已找到 {extra.length} 个对象，可在下方“关联对象”中选择。</p>}
          <div className="placement-review-list">{links.map((item, index) => {
            const target = allCandidates.find(v => v.id === item.target_id);
            const startKind = item.direction === 'outgoing' ? entity.kind : target?.kind || '';
            const endKind = item.direction === 'outgoing' ? target?.kind || '' : entity.kind;
            const types = relationTypes.filter(v => v.sources.includes(startKind) && v.targets.includes(endKind));
            const compatible = types.some(v => v.id === item.relation_type);
            return <article className={`placement-review-item ${item.reviewed ? 'is-reviewed' : ''}`} key={item.id}><header><span className="placement-number">{String(index + 1).padStart(2, '0')}</span><strong>{target?.title || item.target_id}</strong><span className="placement-confidence">{confidenceLabel[item.confidence] || '待判断'}</span></header><div className="placement-review-fields"><label className="placement-endpoint">关联对象<select aria-label={`第${index + 1}项关联对象`} value={item.target_id} disabled={!!proposal.confirmation} onChange={e => { const chosen = allCandidates.find(v => v.id === e.target.value)!; update(item.id, { target_id: chosen.id, target_version: chosen.version }); }}>{allCandidates.map(v => <option key={v.id} value={v.id}>{kindLabel(v.kind)} · {v.title} · {v.id}</option>)}</select></label><label>关系方向<select aria-label={`第${index + 1}项关系方向`} value={item.direction} disabled={!!proposal.confirmation} onChange={e => update(item.id, { direction: e.target.value })}><option value="outgoing">本项 → 关联对象</option><option value="incoming">关联对象 → 本项</option></select></label><label>业务关系<select aria-label={`第${index + 1}项业务关系`} value={item.relation_type} disabled={!!proposal.confirmation} onChange={e => update(item.id, { relation_type: e.target.value })}>{!compatible && <option value={item.relation_type}>请选择相容关系</option>}{types.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}</select></label><label className="placement-reason">复核后的关联理由<textarea aria-label={`第${index + 1}项关联理由`} rows={3} value={item.reason} disabled={!!proposal.confirmation} onChange={e => update(item.id, { reason: e.target.value })} maxLength={2000} /></label></div><details className="placement-evidence"><summary><FileSearch size={14} />查看建议依据与对象内容</summary><p><b>原建议摘录</b>{item.evidence || '原建议未附摘录。'}</p>{target && <><p><b>当前所选对象 · V{target.version} · {sourceLabel(target.source_kind)} · {readableStatus(target.status)}</b>{target.excerpt}</p><Link to={assetRoute(target.id, target.kind)} target="_blank">打开对象核对完整内容 ↗</Link></>}</details><label className="placement-reviewed"><input type="checkbox" checked={!!item.reviewed} disabled={!compatible || !isDraft || !!proposal.confirmation} onChange={e => setLinks(old => old.map(v => v.id === item.id ? { ...v, reviewed: e.target.checked } : v))} /><span>已核对两端内容、方向、适用条件及出处，同意建立此关联</span></label>{!compatible && <p className="asset-inline-error">当前关系类型与对象种类不相容，请调整方向或业务关系。</p>}</article>;
          })}</div>
          {!proposal.confirmation && <div className="placement-final-review"><div><ShieldCheck size={18} /><h4>最终审定</h4><span>已选 {selected.length} / {links.length} 项</span></div><label>审定人<input aria-label="关联审定人" value={reviewer} readOnly /></label><label>审定意见<textarea aria-label="关联审定意见" value={reviewNote} onChange={e => setReviewNote(e.target.value)} rows={3} maxLength={2000} placeholder="说明核对的依据、适用边界及本次同意建立关联的理由（至少6字）" /></label><p>确认后写入已审定关联，并保留原建议、人工调整、两端版本及出处快照。资产内容仍按原审核流程发布。</p><AccessButton className="btn btn-primary" permission="review" onClick={confirm} disabled={!!busy || !selected.length || !isDraft || !reviewer.trim() || reviewNote.trim().length < 6}><ShieldCheck size={15} />{busy === 'confirm' ? '正在审定…' : `审定并建立 ${selected.length} 条关联`}</AccessButton></div>}
        </>}
      </>}
    </>}
  </Modal>;
}
