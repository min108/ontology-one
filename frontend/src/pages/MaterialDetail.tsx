import {AccessButton} from '../components/AccessButton';
import { useEffect, useState, useRef, useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check, CheckCheck, ChevronDown, ChevronUp, Download, Edit3, FileCheck2, FileText, GitMerge, Info, Link2, RefreshCw, Search, ShieldCheck, Sparkles, X, Undo2, Redo2, Scissors, LockKeyhole, Unlock, GripHorizontal, History, MessageSquare, Send, Save } from 'lucide-react';
import { api, useApi } from '../api';
import { useInitialDocumentFocus } from '../navigation';
import { Badge, Card, Empty, Loading, Modal, PageHeader, useToast, FilterTabs, AiBadge } from '../components/ui';
import { RecordPicker } from '../components/workbench';
import './materials.css';

type Chunk = { id: string; position: number; heading: string; content: string; locator: string; status: string };
type Candidate = { id: string; kind: string; title: string; summary: string; content: string; source_chunk_ids: string[]; source_quotes?: { chunk_id: string; quote: string }[]; conditions: string[]; questions: string[]; status?: string; extraction_label?: string; extraction_mode?: string; entity_id?: string };
type Job = { id: string; status: string; progress: number; message: string; created_at?: string; result?: { mode?: string } };
type Detail = { segmentation: { status: string; mode: string; locked: boolean; lock_reason?: string; has_source: boolean }; entity: { id: string; title: string; domain: string; org_id: string; source_kind: string; summary: string; payload: { parse_warnings?: string[]; filename?: string; processing_mode?: string; source_url?: string; [key: string]: any } }; chunks: Chunk[]; historical_chunks?: Chunk[]; candidates: Candidate[]; coverage: Record<string, { status: string; mode: string; message: string; candidate_count?: number }>; jobs: Job[]; stats: { chunks: number; covered: number; candidates: number; adopted: number; pending: number } };
const kindNames: Record<string, string> = { data: '数据资产', rule: '规则资产', skill: '技能资产', knowledge: '知识资产' };
const candidateNames: Record<string, string> = { pending: '待核对', adopted: '已采纳', rejected: '已驳回', merged: '已合并' };
const sourceNames: Record<string, string> = { demo: '演示材料 · 虚构业务', regulation: '法规原文', imported: '存量资料', user: '用户上传' };

export function MaterialDetail() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();
  const focusInitialElement = useInitialDocumentFocus();
  const detail = useApi<Detail>(`/materials/${id}?chunk_id=${encodeURIComponent(searchParams.get('chunk') || '')}`, [id, searchParams.get('chunk')]);
  const sourceVersion = searchParams.get('source_version') || '';
  const anchorId = searchParams.get('anchor') || '';
  const sourceChunkId = searchParams.get('chunk') || '';
  const sourceQuote = searchParams.get('quote') || '';
  const source = useApi<{ text: string; characters: number; source_version: string; version: number; focus?: { anchor_id: string; source_version: string; start: number; end: number; quote: string; locator: string }; versions: { id: string; version: number; created_at: string }[] }>(`/materials/${id}/source?source_version=${encodeURIComponent(sourceVersion)}&anchor_id=${encodeURIComponent(anchorId)}&chunk_id=${encodeURIComponent(sourceChunkId)}&quote=${encodeURIComponent(sourceQuote)}`, [id, sourceVersion, anchorId, sourceChunkId, sourceQuote]);
  const locator = searchParams.get('locator') || '';
  const sourceContext = JSON.stringify([id, sourceVersion, anchorId, sourceChunkId, sourceQuote, locator]);
  const initialSourceView = locator && !(anchorId || sourceChunkId || sourceQuote || sourceVersion) ? 'chunks' : 'original';
  const [sourceSelection, setSourceSelection] = useState({ context: sourceContext, view: initialSourceView, manual: false });
  const sourceView = sourceSelection.context === sourceContext ? sourceSelection.view : initialSourceView;
  const manualSourceView = sourceSelection.context === sourceContext && sourceSelection.manual;
  function setSourceView(view: string, manual = true) { setSourceSelection({ context: sourceContext, view, manual }); }
  const [segmentOpen, setSegmentOpen] = useState(false);
  const [segmentMode, setSegmentMode] = useState('online');
  const [search, setSearch] = useState('');
  const [sourcePage, setSourcePage] = useState(1);
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('pending');
  const [activeCandidate, setActiveCandidate] = useState<string | null>(null);
  const [activeChunk, setActiveChunk] = useState<string | null>(null);
  const tab = searchParams.get('tab') || 'candidates';
  function setTab(value: string) { setSearchParams(previous => { const next = new URLSearchParams(previous); next.set('tab', value); return next; }, { replace: true }); }
  const [mode, setMode] = useState('online');
  const [processOpen, setProcessOpen] = useState(false);
  const [processError, setProcessError] = useState('');
  const [busy, setBusy] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [edit, setEdit] = useState<Candidate | null>(null);
  const [editError, setEditError] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const data = detail.data;
  const active = data?.candidates.find(candidate => candidate.id === activeCandidate);
  const highlightIds = new Set(active?.source_chunk_ids || (activeChunk ? [activeChunk] : []));
  const displayChunks = data ? [...data.chunks, ...(data.historical_chunks || [])] : [];
  const readingChunks = sourceView === 'history' ? data?.historical_chunks || [] : data?.chunks || [];
  const filteredChunks = readingChunks.filter(chunk => !search || `${chunk.heading} ${chunk.content}`.includes(search)) || [];
  const visibleChunks = filteredChunks.slice((sourcePage - 1) * 60, sourcePage * 60);
  const candidates = data?.candidates.filter(candidate => (!kind || candidate.kind === kind) && (!status || (candidate.status || 'pending') === status)) || [];
  const running = job && ['queued', 'running'].includes(job.status);

  useEffect(() => {
    setJob(null); setActiveCandidate(null); setSelected([]); setSearch(''); setSourcePage(1); setSegmentOpen(false); setSourceView(initialSourceView, false);
  }, [id]);

  useEffect(() => {
    if (!data || anchorId || source.loading || source.refreshing || source.data?.focus || manualSourceView) return;
    const chunkId = searchParams.get('chunk');
    const locator = searchParams.get('locator');
    const match = displayChunks.find(chunk => chunk.id === chunkId) || (locator ? displayChunks.find(chunk => chunk.locator === locator || chunk.locator?.includes(locator) || chunk.heading?.includes(locator) || chunk.content.includes(locator)) : null);
    if (match) {
      const historical = data.historical_chunks?.some(chunk => chunk.id === match.id);
      setSourceView(historical ? 'history' : 'chunks', false); setActiveChunk(match.id);
      setSourcePage(Math.floor((historical ? data.historical_chunks! : data.chunks).findIndex(chunk => chunk.id === match.id) / 60) + 1);
      return focusInitialElement(`source-${match.id}`, 'center');
    }
  }, [id, data?.entity.id, sourceContext, source.loading, source.refreshing, source.data?.focus, manualSourceView]);

  useEffect(() => {
    if (manualSourceView || (!source.data?.focus && !((anchorId || sourceChunkId) && source.error))) return;
    setSourceView('original', false);
    if (source.data?.focus) return focusInitialElement('material-anchor-focus', 'center');
  }, [id, data?.entity.id, sourceContext, source.data?.focus?.anchor_id, source.data?.source_version, source.error, manualSourceView]);

  useEffect(() => {
    if (!job && data?.jobs?.[0] && ['queued', 'running'].includes(data.jobs[0].status)) setJob(data.jobs[0]);
  }, [data, job]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const result = await api<Job>(`/jobs/${job.id}`);
        if (!alive) return;
        setJob(result); detail.reload();
        if (!['queued', 'running'].includes(result.status)) toast(result.message);
      } catch { if (alive) setProcessError('进度暂时无法读取，请刷新检查；已保存内容不会丢失。'); }
    }, 2200);
    return () => { alive = false; window.clearInterval(timer); };
  }, [job?.id, job?.status]);

  function showSource(candidate: Candidate) {
    setActiveCandidate(candidate.id); setSearch('');
    const first = candidate.source_chunk_ids?.[0];
    if (first) focusChunk(first);
  }

  function focusChunk(chunkId: string) {
    const historical = data?.historical_chunks?.some(chunk => chunk.id === chunkId);
    setSourceView(historical ? 'history' : 'chunks'); setActiveChunk(chunkId); setSearch('');
    const index = (historical ? data?.historical_chunks || [] : data?.chunks || []).findIndex(chunk => chunk.id === chunkId);
    setSourcePage(Math.floor(Math.max(0, index) / 60) + 1);
    requestAnimationFrame(() => document.getElementById(`source-${chunkId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }

  function openSegmentation(nextMode = 'online') {
    setSegmentMode(nextMode); setSegmentOpen(true);
  }

  async function startProcess() {
    setBusy('extract'); setProcessError('');
    try {
      const result = await api<Job>(`/materials/${id}/extract`, { method: 'POST', body: JSON.stringify({ mode, retry: true }) });
      setJob(result); setProcessOpen(false); toast(mode === 'local' ? '已开始本地规则识别，结果会明确标注识别方式' : 'AI识读任务已提交');
    } catch (error) { setProcessError(error instanceof Error ? error.message : '未能启动整理任务'); }
    finally { setBusy(''); }
  }

  async function changeStatus(candidate: Candidate, next: string) {
    setBusy(candidate.id);
    try {
      await api(`/materials/${id}/candidates/${candidate.id}`, { method: 'PATCH', body: JSON.stringify({ status: next, reason: next === 'rejected' ? '经人工核对，不作为可用资产候选' : '恢复候选，重新核对' }) });
      toast(next === 'rejected' ? '已驳回，原文与处理记录仍保留' : '已恢复为待核对'); detail.reload();
    } catch (error) { toast(error instanceof Error ? error.message : '操作失败'); }
    finally { setBusy(''); }
  }

  async function adopt(candidate: Candidate) {
    setBusy(candidate.id);
    try {
      await api(`/materials/${id}/candidates/${candidate.id}/adopt`, { method: 'POST' });
      toast('已形成资产草稿，原文、适用条件与待核问题一并保留'); detail.reload(); setSelected(selected.filter(value => value !== candidate.id));
    } catch (error) { toast(error instanceof Error ? error.message : '采纳失败，请重试'); }
    finally { setBusy(''); }
  }

  async function saveEdit() {
    if (!edit) return;
    setBusy(edit.id); setEditError('');
    try {
      await api(`/materials/${id}/candidates/${edit.id}`, { method: 'PATCH', body: JSON.stringify({ title: edit.title, kind: edit.kind, summary: edit.summary, content: edit.content, conditions: edit.conditions, questions: edit.questions, source_chunk_ids: edit.source_chunk_ids }) });
      toast('修订已保存，修改记录可追溯'); setEdit(null); detail.reload();
    } catch (error) { setEditError(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(''); }
  }

  async function mergeSelected() {
    setBusy('merge');
    try {
      const result = await api<{ candidate: Candidate }>(`/materials/${id}/candidates/merge`, { method: 'POST', body: JSON.stringify({ candidate_ids: selected }) });
      toast('候选已合并，原候选与全部来源仍保留'); setSelected([]); setActiveCandidate(result.candidate.id); detail.reload();
    } catch (error) { toast(error instanceof Error ? error.message : '合并失败'); }
    finally { setBusy(''); }
  }

  function marked(text: string) {
    if (!search) return text;
    const parts = text.split(search);
    return parts.map((part, index) => <span key={index}>{index > 0 && <mark>{search}</mark>}{part}</span>);
  }

  const resolvedSourceVersion = sourceVersion || (source.data?.focus ? source.data.source_version : '');
  function sourceText() {
    if (!source.data) return '';
    const focus = source.data.focus;
    if (!focus) return marked(source.data.text);
    const chars = Array.from(source.data.text);
    return <>{marked(chars.slice(0, focus.start).join(''))}<mark id="material-anchor-focus" className="materials-exact-anchor" title={focus.locator}>{chars.slice(focus.start, focus.end).join('')}</mark>{marked(chars.slice(focus.end).join(''))}</>;
  }

  if (detail.loading && !data) return <div className="page"><Loading /></div>;
  if (!data) return <div className="page"><Link to="/materials" className="breadcrumb"><ArrowLeft size={15} /> 返回材料中心</Link><Empty title="暂时无法打开材料" description={String(detail.error || '材料不存在或已被移除')} /><AccessButton className="btn" onClick={detail.reload}>重新加载</AccessButton></div>;
  const coveragePercent = data.stats.chunks ? Math.round(data.stats.covered / data.stats.chunks * 100) : 0;
  const failedCount = Object.values(data.coverage).filter(value => value.status === 'failed').length;
  const lastJob = job || data.jobs[0];

  return <div className="page materials-detail-page">
    <Link to="/materials" className="breadcrumb"><ArrowLeft size={14} /> 素材整理 <span>/</span> 原文对照与成果梳理</Link>
    <PageHeader eyebrow="原文留存 · 阅读整理 · 资产萃取" title={data.entity.title} description={data.entity.summary || '保留材料上下文，分别梳理要求、数据、分析思路与实践经验。'} actions={<><a className="btn" href={`/api/materials/${id}/download${resolvedSourceVersion ? `?source_version=${encodeURIComponent(resolvedSourceVersion)}` : ""}`}><Download size={16} /> {resolvedSourceVersion ? "下载该版原文" : "下载原件"}</a><AccessButton className="btn btn-ai" disabled={!!running || !!busy || !data.chunks.length} onClick={() => { setProcessError(''); setProcessOpen(true); }}><Sparkles size={16} />{running ? '正在萃取…' : 'AI 资产萃取'}</AccessButton></>} />
    <div className="materials-detail-meta"><Badge>{sourceNames[data.entity.source_kind] || data.entity.source_kind}</Badge><span>{data.entity.domain || '综合管理'}</span><span>{data.entity.payload.filename || '材料正文'}</span>{data.entity.payload.source_url && <a href={data.entity.payload.source_url} target="_blank" rel="noreferrer">查看原始来源 <Link2 size={13} /></a>}<span className="materials-adopt-notice"><ShieldCheck size={14} /> 采纳后形成草稿，审核发布后投入使用</span></div>
    {data.entity.payload.parse_warnings?.length ? <div className="materials-warning"><Info size={18} /><div><strong>部分材料需要补充识读</strong>{data.entity.payload.parse_warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></div> : null}
    <div className="materials-progress-overview"><div><span className="materials-overview-number">{data.stats.chunks}</span><span>原文片段</span></div><div><span className="materials-overview-number">{data.stats.covered}<small> / {data.stats.chunks}</small></span><span>已处理片段 <b>不是准确率</b></span></div><div><span className="materials-overview-number">{data.stats.pending}</span><span>待核对候选</span></div><div><span className="materials-overview-number">{data.stats.adopted}</span><span>已采纳草稿</span></div><div className="materials-coverage-progress"><div><span>材料处理进度</span><strong>{coveragePercent}%</strong></div><div className="materials-progress-track"><i style={{ width: `${coveragePercent}%` }} /></div><small>未识别出候选的片段仍需复核，避免遗漏隐含经验与引用条件。</small></div></div>
    {lastJob && <div className={`materials-job ${lastJob.status === 'failed' ? 'is-failed' : ''}`}><div>{['queued', 'running'].includes(lastJob.status) ? <RefreshCw size={17} className="materials-spin" /> : <FileCheck2 size={17} />}<span>{lastJob.message}</span></div>{['queued', 'running'].includes(lastJob.status) ? <strong>{lastJob.progress}%</strong> : lastJob.status === 'failed' ? <AccessButton className="btn btn-ghost" onClick={() => { setMode(lastJob.result?.mode || 'online'); setProcessOpen(true); }}>重试未完成部分</AccessButton> : <span className="small muted">候选待核</span>}</div>}
    {processError && !processOpen && <div className="materials-error">{processError}</div>}
    <div className={`materials-workspace ${!data.chunks.length ? 'is-unsegmented' : ''}`}>
      <Card className="materials-source-panel"><div className="materials-panel-head"><div><FileText size={19} /><h2>原始材料</h2><span>{source.data?.characters?.toLocaleString() || '—'} 字</span></div><AccessButton className="btn btn-ghost" onClick={() => { setActiveCandidate(null); setActiveChunk(null); setSearch(''); setSearchParams(previous => { const next = new URLSearchParams(previous); ['anchor', 'chunk', 'quote', 'locator'].forEach(key => next.delete(key)); if (source.data) next.set('source_version', source.data.source_version); return next; }, { replace: true }); }} title="清除高亮">清除高亮</AccessButton></div>
        <div className="materials-source-switch"><AccessButton className={sourceView === 'original' ? 'active' : ''} onClick={() => setSourceView('original')}>连续原文</AccessButton><AccessButton className={sourceView === 'chunks' ? 'active' : ''} disabled={!data.chunks.length} onClick={() => setSourceView('chunks')}>{data.segmentation?.mode === 'source_blocks' ? '原文定位块' : '已确认分段'} <span>{data.chunks.length}</span></AccessButton>{!!data.historical_chunks?.length && <AccessButton className={sourceView === 'history' ? 'active' : ''} onClick={() => setSourceView('history')}>历史引用 <span>{data.historical_chunks.length}</span></AccessButton>}{data.chunks.length > 0 && <AccessButton disabled={!!busy || !!running} title="打开阅读整理工作台，支持人工与 AI 调优，历史出处保持有效" onClick={() => openSegmentation()}>调整阅读分段</AccessButton>}</div>
        {!data.chunks.length && data.segmentation?.has_source && <div className="materials-segment-gate"><AiBadge>先选择原文分段方式</AiBadge><h3>原文已保存，开始阅读整理</h3><p>可先让 AI 提出方案，也可直接手工拆分、合并和调整边界。每次修改自动保存为草稿，确认后形成阅读版本。</p><div><AccessButton className="btn btn-ai" disabled={!!busy} onClick={() => openSegmentation('online')}><Sparkles size={16} /> AI 语义分段</AccessButton><AccessButton className="btn" disabled={!!busy} onClick={() => openSegmentation('manual')}>手工整理</AccessButton></div></div>}
        {(data.historical_chunks?.length || data.entity.payload.reading_version) ? <p className="materials-source-lock"><ShieldCheck size={13} />阅读分段可继续优化。历史候选与资产仍引用原来的精确出处，原文不会被改写。</p> : null}
        <label className="materials-search materials-source-search"><Search size={16} /><input className="input" placeholder="在原文中查找关键词" value={search} onChange={event => { setSearch(event.target.value); setSourcePage(1); }} /></label>
        <div className="materials-original-versions">{sourceView === "original" && <RecordPicker label="原文版本" disabled={source.loading || !source.data?.versions?.length} options={source.data?.versions?.length ? source.data.versions.map((version, index) => ({ value: version.id, label: `原文第 ${version.version} 版${index === 0 ? " · 当前" : " · 历史留存"}`, description: new Date(version.created_at).toLocaleString("zh-CN") })) : [{ value: "__pending__", label: source.error ? "版本信息暂未读取" : "正在读取原文版本", description: "保留原始来源版本与引用位置" }]} value={source.data?.source_version || "__pending__"} onChange={value => { setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("source_version", value); ["anchor", "chunk", "locator", "quote"].forEach(key => next.delete(key)); return next; }, { replace: true }); }} />}</div><div className="materials-source-scroll">{sourceView === 'original' ? source.loading ? <Loading /> : source.error ? <div className="materials-error">{source.error}<AccessButton className="btn" onClick={source.reload}>重新读取</AccessButton></div> : source.data?.text ? <div className="materials-continuous-text"><div className="materials-original-caption">{['TXT', 'MD'].includes(data.entity.payload.format) ? '原文件连续正文' : '按原文件顺序提取的连续文字；表格、版式请对照下载原件'}</div>{search && !source.data.text.includes(search) && <p className="materials-hint">原文未找到“{search}”，下方保留完整正文。</p>}{source.data.focus && <div className="materials-anchor-caption"><ShieldCheck size={14} /><span>引用原文第 {source.data.version} 版 · 第 {source.data.focus.start + 1}–{source.data.focus.end} 字 · 已核对精确出处{source.data.versions[0]?.id !== source.data.source_version ? ' · 正在查看历史原文' : ''}</span></div>}{sourceText()}</div> : <Empty title="来源尚未提供正文" description={data.entity.payload.notes || '请补充可识读原件，平台不会补造缺失内容。'} /> : visibleChunks.length ? visibleChunks.map(chunk => <section id={`source-${chunk.id}`} key={chunk.id} className={`materials-chunk ${highlightIds.has(chunk.id) ? 'is-highlighted' : ''}`} onClick={() => { setActiveChunk(chunk.id); setActiveCandidate(null); }}><div className="materials-chunk-meta"><span>{data.historical_chunks?.some(old => old.id === chunk.id) && <b>历史引用 · </b>}{chunk.locator || `片段 ${chunk.position}`}</span><span>{data.coverage[chunk.id]?.status === 'completed' ? <><CheckCheck size={13} /> 已处理</> : data.coverage[chunk.id]?.status === 'failed' ? '待重试' : '未萃取'}</span></div>{chunk.heading && chunk.heading !== '正文' && <h3>{chunk.heading}{data.segmentation?.mode === 'online' && <small> AI 主题提示</small>}</h3>}<div className="materials-source-text">{marked(chunk.content)}</div>{activeChunk === chunk.id && <p className="materials-chunk-context">{data.candidates.filter(candidate => candidate.source_chunk_ids?.includes(chunk.id)).length} 项候选引用这一片段 · 原文保持不变</p>}</section>) : <Empty title="没有找到对应片段" description="尝试更短的关键词，或切换连续原文查看。" />}</div>
        {sourceView !== 'original' && filteredChunks.length > 60 && <div className="materials-source-pagination"><AccessButton className="btn btn-ghost" disabled={sourcePage === 1} onClick={() => setSourcePage(sourcePage - 1)}>上一组</AccessButton><span>第 {(sourcePage - 1) * 60 + 1}–{Math.min(sourcePage * 60, filteredChunks.length)} / {filteredChunks.length} 个片段</span><AccessButton className="btn btn-ghost" disabled={sourcePage * 60 >= filteredChunks.length} onClick={() => setSourcePage(sourcePage + 1)}>下一组</AccessButton></div>}
      </Card>
      <Card className="materials-candidate-panel"><div className="materials-panel-tabs"><AccessButton className={tab === 'candidates' ? 'active' : ''} onClick={() => setTab('candidates')}><Sparkles size={17} /> 候选成果 <span>{data.candidates.length}</span></AccessButton><AccessButton className={tab === 'coverage' ? 'active' : ''} onClick={() => setTab('coverage')}><FileCheck2 size={17} /> 逐段覆盖 {failedCount > 0 && <b>{failedCount}</b>}</AccessButton></div>
        {tab === 'candidates' ? <><div className="materials-candidate-filters"><FilterTabs label="处理状态" value={status} onChange={value => { setStatus(value); setSelected([]); }} options={[{ value: '', label: '全部', count: data.candidates.filter(c => !kind || c.kind === kind).length }, ...Object.entries(candidateNames).map(([value, label]) => ({ value, label, count: data.candidates.filter(c => (!kind || c.kind === kind) && (c.status || 'pending') === value).length }))]} /><FilterTabs label="资产类型" value={kind} onChange={value => { setKind(value); setSelected([]); }} options={[{ value: '', label: '全部', count: data.candidates.filter(c => !status || (c.status || 'pending') === status).length }, ...Object.entries(kindNames).map(([value, label]) => ({ value, label, count: data.candidates.filter(c => c.kind === value && (!status || (c.status || 'pending') === status)).length }))]} /></div>
          {selected.length > 0 && <div className="materials-mergebar"><span>已选择 {selected.length} 项</span><AccessButton className="btn btn-ghost" disabled={selected.length < 2 || !!busy} onClick={mergeSelected}><GitMerge size={15} /> 合并同类候选</AccessButton><AccessButton className="btn btn-ghost" onClick={() => setSelected([])}>取消</AccessButton></div>}
          <div className="materials-candidates-scroll">{candidates.length ? candidates.map(candidate => {
            const expanded = activeCandidate === candidate.id;
            const pending = (candidate.status || 'pending') === 'pending';
            return <article className={`materials-candidate ${expanded ? 'is-selected' : ''} kind-${candidate.kind}`} key={candidate.id}>
              <div className="materials-candidate-top"><div>{pending && <input type="checkbox" aria-label={`选择候选 ${candidate.title}`} checked={selected.includes(candidate.id)} onChange={event => setSelected(event.target.checked ? [...selected, candidate.id] : selected.filter(value => value !== candidate.id))} />}<span className={`materials-kind kind-${candidate.kind}`}>{kindNames[candidate.kind] || candidate.kind}</span><Badge>{candidateNames[candidate.status || 'pending'] || candidate.status}</Badge></div><AccessButton className="materials-icon-button" title="查看原文与候选详情" onClick={() => expanded ? setActiveCandidate(null) : showSource(candidate)}>{expanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</AccessButton></div>
              <AccessButton className="materials-candidate-title" onClick={() => showSource(candidate)}>{candidate.title}</AccessButton><p className="materials-candidate-summary">{candidate.summary || '请对照原文核对这项候选的业务含义、依据与适用范围。'}</p>
              <div className="materials-candidate-provenance"><AccessButton onClick={() => showSource(candidate)}><Link2 size={13} /> {candidate.source_chunk_ids?.length || 0} 处原文依据</AccessButton><span className={candidate.extraction_mode === 'online' ? 'ai-pill' : ''}>{candidate.extraction_mode === 'online' && <Sparkles size={12} />}{candidate.extraction_label || (candidate.extraction_mode === 'local' ? '本地规则识别' : '待人工核对')}</span></div>
              {expanded && <div className="materials-candidate-body"><div className="materials-candidate-content">{candidate.content}</div>{!!candidate.source_quotes?.length && <div className="materials-verified-quotes"><h4><ShieldCheck size={14} /> 逐字原文依据</h4>{candidate.source_quotes.map((quote, index) => <blockquote key={index} onClick={() => focusChunk(quote.chunk_id)}>{quote.quote}</blockquote>)}</div>}{candidate.conditions?.length > 0 && <div className="materials-detail-block"><h4>前提与适用条件</h4><ul>{candidate.conditions.map((condition, index) => <li key={index}>{condition}</li>)}</ul></div>}{candidate.questions?.length > 0 && <div className="materials-detail-block questions"><h4>还需要核对什么</h4><ul>{candidate.questions.map((question, index) => <li key={index}>{question}</li>)}</ul></div>}<div className="materials-source-links">{candidate.source_chunk_ids?.map(chunkId => { const chunk = displayChunks.find(value => value.id === chunkId); return chunk ? <AccessButton key={chunkId} onClick={() => focusChunk(chunkId)}>{chunk.locator || `片段 ${chunk.position}`} <ArrowRight size={12} /></AccessButton> : null; })}</div></div>}
              <div className="materials-candidate-actions">{pending ? <><AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => { setEdit({ ...candidate, conditions: candidate.conditions || [], questions: candidate.questions || [], source_chunk_ids: candidate.source_chunk_ids || [] }); setEditError(''); }}><Edit3 size={14} /> 编辑</AccessButton><AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => changeStatus(candidate, 'rejected')}><X size={14} /> 驳回</AccessButton><AccessButton className="btn btn-primary" disabled={!!busy} onClick={() => adopt(candidate)}><Check size={14} />{busy === candidate.id ? '保存中…' : '采纳为草稿'}</AccessButton></> : candidate.status === 'adopted' && candidate.entity_id ? <Link className="btn btn-ghost" to={`/assets/${candidate.entity_id}`}>查看资产与审核状态 <ArrowRight size={14} /></Link> : candidate.status === 'rejected' ? <AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => changeStatus(candidate, 'pending')}><RefreshCw size={14} /> 恢复核对</AccessButton> : <span className="muted small">原候选已保留在合并记录中</span>}</div>
            </article>;
          }) : <div className="materials-candidate-empty"><Sparkles size={34} /><h3>{data.candidates.length ? '此筛选条件下没有候选' : '从这份材料中发现可用成果'}</h3><p>{data.candidates.length ? '切换类型或状态，查看已经采纳、驳回或合并的内容。' : '识读会寻找材料中的数据、要求、分析思路和经验，保留出处与需要人工补充的问题。'}</p><AccessButton className="btn" disabled={!!busy} onClick={() => data.candidates.length ? (setKind(''), setStatus('')) : data.chunks.length ? setProcessOpen(true) : openSegmentation()}>{data.candidates.length ? '查看全部候选' : data.chunks.length ? '选择资产萃取方式' : '先确认原文分段'}</AccessButton></div>}</div>
        </> : <div className="materials-coverage-scroll"><div className="materials-coverage-explainer"><Info size={17} /><p>这里记录每个片段是否完成处理及其候选数量。处理完成代表已扫描，不能代替人工对准确性与遗漏的判断。</p></div>{data.chunks.map(chunk => { const coverage = data.coverage[chunk.id]; return <AccessButton className="materials-coverage-row" key={chunk.id} onClick={() => { setActiveCandidate(null); focusChunk(chunk.id); }}><span className={`materials-coverage-dot ${coverage?.status || 'pending'}`} /><div><strong>{chunk.locator || `片段 ${chunk.position}`}</strong><p>{coverage?.message || '尚未处理，可以开始识读或先对照原文。'}</p><small>{coverage ? (coverage.mode === 'local' ? '本地规则识别' : 'AI在线识读') : chunk.heading}</small></div><span>{coverage?.status === 'completed' ? `${coverage.candidate_count || 0} 项` : coverage?.status === 'failed' ? '失败' : '待处理'}</span></AccessButton>; })}</div>}
      </Card>
    </div>
    <div className="materials-bottom-note"><ShieldCheck size={16} /><p>材料记载、经验判断和正式规则需要分别核对。发布资产确认的是其可使用范围，具体业务结论仍依职责与程序形成。</p></div>
    {processOpen && <Modal title="选择资产萃取方式" onClose={() => !busy && setProcessOpen(false)}><p className="muted">在已确认的分段中识别四类资产候选，保留前提、例外与相邻段落。此步骤不会重新分段。已完成的同类识读会跳过，仅继续未完成部分。</p><div className="materials-mode-options"><label className={mode === 'online' ? 'active' : ''}><input type="radio" name="mode" value="online" checked={mode === 'online'} onChange={() => setMode('online')} /><div><strong className="ai-pill"><Sparkles size={17} /> AI 在线资产萃取</strong><p>结合上下文识别四类资产、适用条件与待核问题。需配置AI连接，原文会发送给所配置的模型服务。</p></div></label><label className={mode === 'local' ? 'active' : ''}><input type="radio" name="mode" value="local" checked={mode === 'local'} onChange={() => setMode('local')} /><div><strong><FileText size={17} /> 本地规则识别</strong><p>根据原文用语提出初步候选，不调用在线模型。结果明确标注本地识别，需要人工逐项校核。</p></div></label></div>{processError && <div className="materials-error">{processError}</div>}<div className="materials-modal-actions"><Link className="btn btn-ghost" to="/settings">查看AI连接设置</Link><AccessButton className={mode === 'online' ? 'btn btn-ai' : 'btn btn-primary'} disabled={!!busy} onClick={startProcess}>{mode === 'online' && <Sparkles size={15} />}{busy ? '正在提交…' : '开始萃取候选'}</AccessButton></div></Modal>}
    {segmentOpen && <Modal title="阅读整理工作台" onClose={() => setSegmentOpen(false)} wide><ReadingWorkbench materialId={id!} initialMode={segmentMode} onConfirmed={() => { setSegmentOpen(false); setSourceView('chunks'); setSourcePage(1); detail.reload(); source.reload(); }} /></Modal>}
    {edit && <Modal title="对照原文修订候选成果" onClose={() => !busy && setEdit(null)} wide><div className="grid-2"><label className="form-field">资产类型<select className="select" value={edit.kind} onChange={event => setEdit({ ...edit, kind: event.target.value })}>{Object.entries(kindNames).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></label><label className="form-field">成果名称<input className="input" value={edit.title} onChange={event => setEdit({ ...edit, title: event.target.value })} /></label></div><label className="form-field">内容摘要<textarea className="input" rows={2} value={edit.summary} onChange={event => setEdit({ ...edit, summary: event.target.value })} /></label><label className="form-field">完整内容<textarea className="input materials-edit-content" rows={8} value={edit.content} onChange={event => setEdit({ ...edit, content: event.target.value })} /></label><div className="grid-2"><label className="form-field">适用条件与例外（每行一项）<textarea className="input" rows={4} value={edit.conditions.join('\n')} onChange={event => setEdit({ ...edit, conditions: event.target.value.split('\n') })} /></label><label className="form-field">待核问题（每行一项）<textarea className="input" rows={4} value={edit.questions.join('\n')} onChange={event => setEdit({ ...edit, questions: event.target.value.split('\n') })} /></label></div><fieldset className="materials-source-picker"><legend>引用的原文位置</legend>{displayChunks.map(chunk => <label key={chunk.id}><input type="checkbox" checked={edit.source_chunk_ids.includes(chunk.id)} onChange={event => setEdit({ ...edit, source_chunk_ids: event.target.checked ? [...edit.source_chunk_ids, chunk.id] : edit.source_chunk_ids.filter(value => value !== chunk.id) })} /><span><strong>{chunk.locator}</strong> {chunk.content.slice(0, 110)}</span></label>)}</fieldset>{editError && <div className="materials-error">{editError}</div>}<div className="materials-modal-actions"><AccessButton className="btn" disabled={!!busy} onClick={() => setEdit(null)}>取消</AccessButton><AccessButton className="btn btn-primary" disabled={!!busy} onClick={saveEdit}><Check size={15} />{busy ? '保存中…' : '保存修订'}</AccessButton></div></Modal>}
  </div>;
}

export default MaterialDetail;

type ReadingPart = { id: string; start: number; end: number; heading: string; content: string; locked_after?: boolean };
type ReadingChange = { id: string; start: number; end: number; summary: string; excerpt: string; before: ReadingPart[]; after: ReadingPart[] };
type ReadingDraft = { id: string; revision: number; source_version: string; source_sha256: string; source_number: number; source_update_available?: boolean; archived_drafts: { id: string; source_number: number; created_at: string }[]; characters: number; segments: ReadingPart[]; can_undo: boolean; can_redo: boolean; ai_running?: boolean; pending_ai: { id?: string; mode?: string; groups?: ReadingChange[] }; chat: { role: string; content: string; mode?: string }[]; versions: { id: string; version: number; source_version: string; reason: string; created_at: string }[] };

function ReadingWorkbench({ materialId, initialMode, onConfirmed }: { materialId: string; initialMode: string; onConfirmed: () => void }) {
  const [draft, setDraft] = useState<ReadingDraft | null>(null);
  const [busy, setBusy] = useState('');
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedChanges, setSelectedChanges] = useState<string[]>([]);
  const [stage, setStage] = useState('reading');
  const [activeChangeId, setActiveChangeId] = useState('');
  const [headingEdits, setHeadingEdits] = useState<Record<string, string>>({});
  const proposalSeen = useRef<string | undefined>(undefined);
  const workbenchRef = useRef<HTMLDivElement>(null);
  const [instruction, setInstruction] = useState('按不同管理要求细化阅读段，将适用条件、例外和对应正文放在同一上下文，标题简短。');
  const [granularity, setGranularity] = useState('balanced');
  const [panel, setPanel] = useState(initialMode === 'manual' ? 'manual' : 'ai');
  const [page, setPage] = useState(1);
  const [cursor, setCursor] = useState<{ id: string; at: number } | null>(null);
  const [boundary, setBoundary] = useState<Record<string, number>>({});
  const toast = useToast();
  const pending = draft?.pending_ai.groups || [];
  const isAIWorking = busy === 'ai' || draft?.ai_running;
  const sourceCharacters = useMemo(() => Array.from(draft?.segments.map(part => part.content).join('') || ''), [draft?.segments]);
  const activeChange = pending.find(group => group.id === activeChangeId) || pending[0];
  const activeIndex = activeChange ? pending.indexOf(activeChange) : -1;
  const onlineProposal = draft?.pending_ai.mode === 'online';
  const sourceSlice = (start: number, end: number) => sourceCharacters.slice(start, end).join('');
  async function load() { try { setDraft(await api<ReadingDraft>(`/materials/${materialId}/reading`)); } catch (e) { setError(e instanceof Error ? e.message : '草稿暂时无法读取'); } }
  useEffect(() => { load(); }, [materialId]);
  useEffect(() => {
    if (proposalSeen.current !== draft?.pending_ai.id) {
      proposalSeen.current = draft?.pending_ai.id;
      setSelectedChanges(pending.map(group => group.id)); setHeadingEdits({});
      if (pending.length) setStage('review');
    } else setSelectedChanges(previous => previous.filter(id => pending.some(group => group.id === id)));
    setActiveChangeId(previous => pending.some(group => group.id === previous) ? previous : pending[0]?.id || '');
  }, [draft?.pending_ai.id, pending.map(group => group.id).join(',')]);
  useEffect(() => { if (draft) setPage(previous => Math.min(previous, Math.max(1, Math.ceil(draft.segments.length / 12)))); }, [draft?.segments.length]);
  useEffect(() => { if (!draft?.ai_running) return; const timer = window.setInterval(load, 2500); return () => window.clearInterval(timer); }, [draft?.ai_running]);
  async function operation(action: string, extra: Record<string, any> = {}) {
    if (!draft || busy || inFlight.current) return;
    inFlight.current = true;
    setBusy(action); setError('');
    try {
      const next = await api<ReadingDraft>(`/materials/${materialId}/reading/operation`, { method: 'POST', body: JSON.stringify({ revision: draft.revision, action, ...extra }) });
      setDraft(next); setSelected(previous => previous.filter(id => next.segments.some(part => part.id === id))); setCursor(null); setBoundary({});
    } catch (e) { setError(e instanceof Error ? e.message : '调整未保存，请重试'); }
    finally { inFlight.current = false; setBusy(''); }
  }
  async function suggest(mode = 'online') {
    if (!draft || busy || inFlight.current) return;
    inFlight.current = true;
    setBusy('ai'); setError('');
    try {
      const next = await api<ReadingDraft>(`/materials/${materialId}/reading/ai`, { method: 'POST', body: JSON.stringify({ revision: draft.revision, mode, instruction: mode === 'online' ? instruction : '按自然句段生成基础结构，保留人工固定边界。', scope_ids: selected, granularity }) });
      setDraft({ ...next, ai_running: false });
      toast(next.pending_ai.groups?.length ? '调整建议已生成，请对照差异选择采纳。人工草稿尚未替换。' : '本轮没有需要调整的边界或标题，当前草稿保持不变。');
    } catch (e) { setError(e instanceof Error ? e.message : 'AI 调整未完成，人工草稿保持不变'); }
    finally { inFlight.current = false; setBusy(''); }
  }
  async function applyChanges(ids = selectedChanges) {
    if (!draft || inFlight.current) return; inFlight.current = true; setBusy('apply'); setError('');
    try {
      const allowedIds = new Set(pending.filter(group => ids.includes(group.id)).flatMap(group => group.after.map(part => part.id)));
      const overrides = Object.fromEntries(Object.entries(headingEdits).filter(([id]) => allowedIds.has(id)));
      setDraft(await api<ReadingDraft>(`/materials/${materialId}/reading/apply`, { method: 'POST', body: JSON.stringify({ revision: draft.revision, proposal_id: draft.pending_ai.id, group_ids: ids, heading_overrides: overrides }) }));
      toast(`${ids.length} 处建议已进入草稿，仍可撤销。`);
    }
    catch (e) { setError(e instanceof Error ? e.message : '建议未能应用'); }
    finally { inFlight.current = false; setBusy(''); }
  }
  async function discard(ids?: string[]) {
    if (!draft || inFlight.current) return; inFlight.current = true; setBusy('discard'); setError('');
    try {
      setDraft(await api<ReadingDraft>(`/materials/${materialId}/reading/discard-suggestions`, { method: 'POST', body: JSON.stringify({ revision: draft.revision, proposal_id: draft.pending_ai.id, ...(ids ? { group_ids: ids } : {}) }) }));
      toast(ids ? '本处建议已略过，对应原文与当前分段保持不变。' : '剩余建议已放弃，阅读草稿保持不变。');
    } catch (e) { setError(e instanceof Error ? e.message : '建议未能处理'); } finally { inFlight.current = false; setBusy(''); }
  }
  function locateChange(group: ReadingChange) {
    if (!draft) return;
    const indices = draft.segments.map((part, index) => ({ part, index })).filter(({ part }) => part.start < group.end && part.end > group.start);
    setSelected(indices.map(({ part }) => part.id)); setPage(Math.floor((indices[0]?.index || 0) / 12) + 1); setStage('reading');
    requestAnimationFrame(() => workbenchRef.current?.querySelector<HTMLElement>(`[data-reading-part="${indices[0]?.part.id}"]`)?.scrollIntoView({ block: 'center' }));
  }
  async function confirm() {
    if (!draft || inFlight.current) return; inFlight.current = true; setBusy('confirm'); setError('');
    try { await api(`/materials/${materialId}/reading/confirm`, { method: 'POST', body: JSON.stringify({ revision: draft.revision, reason: '人工核对阅读边界、标题及条件上下文后确认' }) }); toast('新阅读版本已确认，原文与所有历史出处继续有效。'); onConfirmed(); }
    catch (e) { setError(e instanceof Error ? e.message : '暂未确认，请重试'); } finally { inFlight.current = false; setBusy(''); }
  }
  function snapBoundary(index: number, value: number) {
    if (!draft) return value;
    const left = draft.segments[index], right = draft.segments[index + 1];
    const chars = Array.from(left.content + right.content);
    const choices = chars.flatMap((char, offset) => /[。！？；\n]/.test(char) && left.start + offset + 1 < right.end ? [left.start + offset + 1] : []);
    return choices.length ? choices.reduce((best, point) => Math.abs(point - value) < Math.abs(best - value) ? point : best, choices[0]) : value;
  }
  if (!draft) return <div className="reading-workbench">{error ? <div className="materials-error">{error}<AccessButton className="btn" onClick={load}>重新读取</AccessButton></div> : <Loading />}</div>;
  return <div className="reading-workbench" ref={workbenchRef}>
    <div className="reading-workbench-intro"><div><strong><ShieldCheck size={18} /> 原文固定，阅读方式可以不断优化</strong><p>人工操作自动保存为草稿；AI 建议先展示差异。既有候选、资产及图谱链接始终保留原出处。</p></div><div className="reading-save-state"><Save size={14} /> 草稿第 {draft.revision} 次保存 <span>{draft.characters.toLocaleString()} 字完整保留</span></div></div>
    <div className="materials-hint"><ShieldCheck size={16} /><span>当前整理原文第 {draft.source_number} 版。{draft.source_update_available ? "材料已有新原文，历史手稿完整保留；确认新阅读版前请切换。" : "已有引用持续指向当时原文。"}</span>{draft.source_update_available && <AccessButton className="btn" disabled={!!busy} onClick={() => operation("latest_source")}>基于新原文建立草稿</AccessButton>}</div><div className="reading-stage-tabs"><FilterTabs label="工作视图" value={stage} onChange={setStage} options={[{ value: 'reading', label: '阅读与对话', icon: <FileText size={15} /> }, { value: 'review', label: '建议核对', count: pending.length, icon: <CheckCheck size={15} /> }]} /><span>原文第 {draft.source_number} 版 · {draft.segments.length} 个阅读段</span></div>{stage === 'reading' && <div className="reading-toolbar"><div><AccessButton className="btn" disabled={!!busy || !draft.can_undo} onClick={() => operation('undo')}><Undo2 size={15} />撤销</AccessButton><AccessButton className="btn" disabled={!!busy || !draft.can_redo} onClick={() => operation('redo')}><Redo2 size={15} />重做</AccessButton><AccessButton className="btn" disabled={!!busy || selected.length < 2} onClick={() => operation('merge', { segment_ids: selected })}><GitMerge size={15} />合并所选 {selected.length || ''}</AccessButton>{selected.length > 0 && <AccessButton className="btn btn-ghost" onClick={() => setSelected([])}>清除选择</AccessButton>}</div><span>{draft.segments.length} 个阅读段 · 第 {page} / {Math.max(1, Math.ceil(draft.segments.length / 12))} 组</span></div>}
    {error && <div className="materials-error" role="alert">{error}<AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => { setError(''); load(); }}>刷新草稿</AccessButton></div>}
    {stage === 'reading' && !!pending.length && <div className="reading-pending-note"><Info size={14} /><span>还有 {pending.length} 处建议待核对。手工改动标题或边界后，当前待核建议将失效；可先在“建议核对”中编辑建议标题并采纳。</span></div>}
    {stage === 'reading' && <div className="reading-columns"><div className="reading-editor"><div className="reading-panel-title"><h3>阅读草稿</h3><span>在原文中定位光标可拆分；标题可直接编辑</span></div><div className="reading-cards">{draft.segments.slice((page - 1) * 12, page * 12).map((part, offset) => {
      const index = (page - 1) * 12 + offset;
      const next = draft.segments[index + 1];
      const selectedCursor = cursor?.id === part.id && cursor.at > part.start && cursor.at < part.end ? cursor.at : null;
      return <article className={`reading-part ${selected.includes(part.id) ? 'is-selected' : ''}`} key={part.id} data-reading-part={part.id}><div className="reading-part-top"><label><input type="checkbox" checked={selected.includes(part.id)} onChange={event => setSelected(event.target.checked ? [...selected, part.id] : selected.filter(id => id !== part.id))} aria-label={`选择第 ${index + 1} 段`} /><span>{String(index + 1).padStart(2, '0')}</span></label><input className="input reading-heading" aria-label={`第 ${index + 1} 段阅读标题`} key={`${part.id}-${part.heading}`} defaultValue={part.heading} disabled={!!busy} onBlur={event => { if (event.target.value.trim() !== part.heading) operation('rename', { segment_id: part.id, heading: event.target.value }); }} /><small>{part.end - part.start} 字</small></div><div className="reading-part-location">原文第 {part.start + 1}–{part.end} 字 · 阅读标题不改变原文含义</div><textarea className="reading-original" aria-label={`第 ${index + 1} 段原文`} readOnly value={part.content} rows={Math.min(10, Math.max(3, Math.ceil(part.content.length / 55)))} onSelect={event => setCursor({ id: part.id, at: part.start + Array.from(event.currentTarget.value.slice(0, event.currentTarget.selectionStart)).length })} /><div className="reading-part-actions"><AccessButton className="btn btn-ghost" disabled={!!busy || !selectedCursor} onClick={() => operation('split', { segment_id: part.id, at: selectedCursor })}><Scissors size={14} />{selectedCursor ? `在第 ${selectedCursor} 字后拆分` : '定位光标后拆分'}</AccessButton>{next && <AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => operation('lock', { segment_id: part.id, locked: !part.locked_after })}>{part.locked_after ? <LockKeyhole size={14} /> : <Unlock size={14} />}{part.locked_after ? '释放固定边界' : '固定本段边界'}</AccessButton>}</div>{next && <div className={`reading-boundary ${part.locked_after ? 'is-locked' : ''}`}><GripHorizontal size={16} /><label>与下一段的边界<input type="range" aria-label={`调整第 ${index + 1} 段边界`} min={part.start + 1} max={next.end - 1} value={boundary[part.id] ?? part.end} disabled={!!busy || part.locked_after} onChange={event => setBoundary({ ...boundary, [part.id]: snapBoundary(index, Number(event.target.value)) })} onPointerUp={() => { const at = boundary[part.id]; if (at && at !== part.end) operation('move', { segment_id: part.id, at }); }} onKeyUp={() => { const at = boundary[part.id]; if (at && at !== part.end) operation('move', { segment_id: part.id, at }); }} /></label><span>第 {boundary[part.id] ?? part.end} 字后</span></div>}</article>;
    })}</div><div className="materials-pagination"><AccessButton className="btn" disabled={page === 1} onClick={() => setPage(page - 1)}>上一组</AccessButton><span>{draft.segments.length} 段 · 草稿可随时关闭后继续</span><AccessButton className="btn" disabled={page * 12 >= draft.segments.length} onClick={() => setPage(page + 1)}>下一组</AccessButton></div></div>
    <aside className="reading-assistant"><FilterTabs label="调优方式" value={panel} onChange={setPanel} options={[{ value: 'ai', label: 'AI 对话', icon: <Sparkles size={14} /> }, { value: 'manual', label: '手工与版本', icon: <Edit3 size={14} /> }]} />{panel === 'ai' ? <><div className="ai-note"><Sparkles size={16} /><span>可针对{selected.length ? `所选 ${selected.length} 个连续片段` : '全文'}提出修改要求。AI 只建议边界和标题，不改写原文；人工固定边界始终保留。</span></div><FilterTabs label="阅读粒度" value={granularity} onChange={setGranularity} options={[{ value: 'fine', label: '细致' }, { value: 'balanced', label: '适中' }, { value: 'theme', label: '完整主题' }]} /><div className="reading-chat">{draft.chat.length ? draft.chat.slice(-8).map((turn, i) => <div className={`reading-chat-turn ${turn.role}`} key={i}><strong>{turn.role === 'user' ? '我的要求' : '整理助手'}</strong><p>{turn.content}</p></div>) : <p className="muted small">例如：“第 3 段按不同要求拆开，但将‘除外’条件和对应正文放在一起。”</p>}</div><label className="form-field">本轮调优要求<textarea className="input" rows={4} value={instruction} onChange={event => setInstruction(event.target.value)} placeholder="说出你希望如何阅读这份材料…" /></label><AccessButton className="btn btn-ai reading-ask" disabled={!!busy || !!isAIWorking || !instruction.trim()} onClick={() => suggest()}><Sparkles size={16} />{isAIWorking ? '正在分析范围与接缝…' : selected.length ? '对话调优所选片段' : '对话调优全文'}</AccessButton><p className="reading-small-note">建议需逐项确认。单次调优支持 12 万字，更长原文可选择连续片段分次处理。连接失败不会覆盖人工草稿；内容会发送到你配置的在线模型。</p></> : <div className="reading-manual-guide"><h3>按你的阅读习惯整理</h3><ol><li>在原文框中点选光标位置，拆成两个连续片段。</li><li>勾选相邻片段后合并；拖动滑杆或用方向键微调边界。</li><li>固定关键边界，AI 也会保留它。</li><li>标题只作阅读提示。手改边界或标题后，尚未处理的建议会失效，需重新调优；原文修订应另建版本。</li></ol><AccessButton className="btn" disabled={!!busy} onClick={() => suggest('default')}><FileText size={16} />提出默认句段方案</AccessButton><h3><History size={16} /> 已确认的阅读版本</h3>{draft.versions.length ? draft.versions.map(version => <div className="reading-version" key={version.id}><div><strong>阅读版 {version.version}</strong><small>{new Date(version.created_at).toLocaleString('zh-CN')}</small><p>{version.reason}</p></div><AccessButton className="btn btn-ghost" disabled={!!busy || version.source_version !== draft.source_version} onClick={() => operation('restore', { version_id: version.id })}>恢复到草稿</AccessButton></div>) : <p className="muted small">尚未确认首个阅读版本。确认后，每次调整仍可回看历史。</p>}{!!draft.archived_drafts.length && <><h3>其他原文版本的手稿</h3><p className="muted small">切换时会同时留存当前草稿，原文与已确认版本保持不变。</p>{draft.archived_drafts.map(saved => <div className="reading-version" key={saved.id}><div><strong>原文第 {saved.source_number} 版手稿</strong><small>{new Date(saved.created_at).toLocaleString("zh-CN")}</small></div><AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => operation("archived_draft", { version_id: saved.id })}>打开手稿</AccessButton></div>)}</>}</div>}
    {!!pending.length && <AccessButton className="btn reading-review-link" onClick={() => setStage('review')}><CheckCheck size={15} />打开宽幅对照 · {pending.length} 处待核建议<ArrowRight size={15} /></AccessButton>}
    </aside></div>}
    {stage === 'review' && <section className="reading-proposals" aria-label="阅读调整建议核对">
      <div className="reading-panel-title"><div><h3>{onlineProposal ? <Sparkles size={17} /> : <FileText size={17} />}{pending.length ? '待核对的调整建议' : '本轮建议已处理'}</h3><p>逐处对照原文与边界；可先修订建议标题，再采纳整组调整。</p></div><span>{pending.length} 处待核对</span></div>
      {activeChange ? <>
        <div className="reading-proposal-overview"><div className="reading-proposal-caption"><strong>调整位置</strong><span>每组覆盖一段连续原文，同组边界一起处理。</span><label><input type="checkbox" aria-label="选择全部调整建议" checked={selectedChanges.length === pending.length} onChange={event => setSelectedChanges(event.target.checked ? pending.map(group => group.id) : [])} />全选</label></div><div className="reading-proposal-nav">{pending.map((group, index) => <div className={`reading-proposal-position ${group.id === activeChange.id ? 'active' : ''}`} key={group.id}><input type="checkbox" aria-label={`选择第 ${index + 1} 处建议`} checked={selectedChanges.includes(group.id)} onChange={event => setSelectedChanges(previous => event.target.checked ? [...previous, group.id] : previous.filter(id => id !== group.id))} /><AccessButton onClick={() => setActiveChangeId(group.id)} aria-current={group.id === activeChange.id ? 'step' : undefined}><strong>{String(index + 1).padStart(2, '0')}</strong><span>第 {group.start + 1}–{group.end} 字<small>{group.before.length} 段 → {group.after.length} 段</small></span></AccessButton></div>)}</div></div>
        <article className="reading-change" key={activeChange.id}>
          <div className="reading-change-heading"><div><span className="reading-change-order">{String(activeIndex + 1).padStart(2, '0')}</span><div><h4>{activeChange.before.length === activeChange.after.length ? '核对边界与阅读标题' : activeChange.after.length > activeChange.before.length ? '拆分连续原文，明确不同要求' : '合并相邻内容，保留完整语境'}</h4><p>{activeChange.summary} · 原文内容不变</p></div></div><AccessButton className="btn btn-ghost" onClick={() => locateChange(activeChange)}><Search size={14} />定位阅读草稿</AccessButton></div>
          <div className="reading-diff"><section><header><strong>当前方案</strong><span>{activeChange.before.length} 个阅读段</span></header>{activeChange.before.map((part, index) => <div className="reading-comparison-part" key={part.id}><div className="reading-comparison-title"><b>{String(index + 1).padStart(2, '0')}</b><strong>{part.heading}</strong>{part.locked_after && <LockKeyhole size={13} aria-label="边界已固定" />}</div><small>第 {part.start + 1}–{part.end} 字 · {part.end - part.start} 字</small><p>{sourceSlice(part.start, part.end)}</p></div>)}</section><section className="reading-proposed"><header><strong>{onlineProposal && <Sparkles size={14} />}建议方案</strong><span>{activeChange.after.length} 个阅读段 · 标题可编辑</span></header>{activeChange.after.map((part, index) => <div className="reading-comparison-part" key={part.id}><label className="reading-comparison-title"><b>{String(index + 1).padStart(2, '0')}</b><input className="input" aria-label={`建议第 ${index + 1} 段标题`} maxLength={100} value={headingEdits[part.id] ?? part.heading} onChange={event => setHeadingEdits(previous => ({ ...previous, [part.id]: event.target.value }))} disabled={!!busy} />{part.locked_after && <LockKeyhole size={13} aria-label="边界已固定" />}</label><small>第 {part.start + 1}–{part.end} 字 · {part.end - part.start} 字</small><p>{sourceSlice(part.start, part.end)}</p></div>)}</section></div>
          <details className="reading-context"><summary>查看此处的连续原文与前后语境</summary><p>{sourceSlice(Math.max(0, activeChange.start - 100), activeChange.start)}<mark>{sourceSlice(activeChange.start, activeChange.end)}</mark>{sourceSlice(activeChange.end, Math.min(draft.characters, activeChange.end + 100))}</p></details>
          <div className="reading-item-actions"><div><AccessButton className="btn btn-ghost" disabled={activeIndex <= 0} onClick={() => setActiveChangeId(pending[activeIndex - 1].id)}>上一处</AccessButton><span>{activeIndex + 1} / {pending.length}</span><AccessButton className="btn btn-ghost" disabled={activeIndex >= pending.length - 1} onClick={() => setActiveChangeId(pending[activeIndex + 1].id)}>下一处</AccessButton></div><div><AccessButton className="btn" disabled={!!busy} onClick={() => discard([activeChange.id])}><X size={14} />略过本处</AccessButton><AccessButton className={onlineProposal ? 'btn btn-ai' : 'btn btn-primary'} disabled={!!busy || activeChange.after.some(part => !(headingEdits[part.id] ?? part.heading).trim())} onClick={() => applyChanges([activeChange.id])}><Check size={15} />采纳本处</AccessButton></div></div>
        </article>
        <div className="reading-proposal-actions"><span>已勾选 {selectedChanges.length} / {pending.length} 处 · 未采纳的内容仍保持当前方案</span><div><AccessButton className="btn btn-ghost" disabled={!!busy} onClick={() => discard()}>放弃剩余建议</AccessButton><AccessButton className="btn" disabled={!!busy || !selectedChanges.length} onClick={() => applyChanges()}><CheckCheck size={15} />批量采纳所选 {selectedChanges.length} 处</AccessButton></div></div>
      </> : <div className="reading-review-complete"><CheckCheck size={26} /><h4>当前没有待核对的调整建议</h4><p>阅读草稿已保存，可继续手工整理或发起新一轮对话。确认后才生成新的阅读版本。</p><AccessButton className="btn" onClick={() => setStage('reading')}>返回阅读与对话<ArrowRight size={14} /></AccessButton></div>}
    </section>}
    <div className="reading-confirm-bar"><div><ShieldCheck size={17} /><span>原文内容完整保留；阅读分段质量仍需核对条件与例外。</span></div><AccessButton className="btn btn-primary" disabled={!!busy || !!pending.length || !!draft.ai_running || draft.source_update_available} onClick={confirm}><Check size={17} />{busy === 'confirm' ? '正在确认…' : '确认新的阅读版本'}</AccessButton></div>
  </div>;
}
