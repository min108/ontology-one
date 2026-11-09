import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, FileText, FolderOpen, Search, Upload, Sparkles, ShieldCheck, X, Plus, BookOpen, Files, Check, RotateCcw } from 'lucide-react';
import { useApi } from '../api';
import { Badge, Card, Empty, Loading, Modal, PageHeader, Stat, useToast, FilterTabs } from '../components/ui';
import { FileIntake } from '../components/workbench';
import './materials.css';

type Material = { id: string; title: string; summary: string; domain: string; org_id: string; status: string; source_kind: string; updated_at: string; payload?: Record<string, any> };
type List = { items: Material[]; total: number; page: number; page_size: number; facets?: Record<string, Record<string, number>> };
type UploadItem = { key: string; token: string; file: File; status: 'pending' | 'uploading' | 'success' | 'error'; progress: number; error?: string; id?: string };
const sourceNames: Record<string, string> = { demo: '演示材料', regulation: '法规原文', imported: '存量资料', user: '用户上传' };
const statusNames: Record<string, string> = { draft: '待整理', pending: '待核对', review: '审核中', approved: '已审核', published: '已发布', archived: '已归档', failed: '需重试', running: '处理中' };

export function Materials() {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const projectId = params.get('project_id') || '';
  const taskId = params.get('task_id') || '';
  const matterId = params.get('matter_id') || '';
  const query = params.get('q') || '';
  const source = params.get('source_kind') || '';
  const status = params.get('status') || '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  function setFilter(key: string, value: string) { setParams(previous => { const next = new URLSearchParams(previous); value ? next.set(key, value) : next.delete(key); next.delete('page'); return next; }, { replace: true }); }
  function setQuery(value: string) { setFilter('q', value); }
  function setSource(value: string) { setFilter('source_kind', value); }
  function setStatus(value: string) { setFilter('status', value); }
  function setPage(value: number) { setParams(previous => { const next = new URLSearchParams(previous); next.set('page', String(value)); return next; }, { replace: true }); }
  const [uploadOpen, setUploadOpen] = useState(params.get('upload') === '1');
  const [queue, setQueue] = useState<UploadItem[]>([]);
  const [org, setOrg] = useState('');
  const [domain, setDomain] = useState('综合管理');
  const [busy, setBusy] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const busyRef = useRef(false);
  const listing = useApi<List>(`/materials?q=${encodeURIComponent(query)}&status=${status}&source_kind=${source}&project_id=${encodeURIComponent(projectId)}&task_id=${encodeURIComponent(taskId)}&matter_id=${encodeURIComponent(matterId)}&page=${page}&page_size=24`, [query, status, source, page, projectId, taskId, matterId]);
  const organizations = useApi<List>('/entities?kind=organization&page_size=100');
  const items = listing.data?.items || [];
  const orgNames = new Map((organizations.data?.items || []).map(item => [item.id, item.title]));
  const currentCandidates = items.reduce((total, item) => total + (item.payload?.candidate_count || 0), 0);
  const completed = queue.filter(item => item.status === 'success').length;
  const failed = queue.filter(item => item.status === 'error').length;
  const remaining = queue.filter(item => item.status === 'pending' || item.status === 'error').length;
  function facetOptions(field: string, names: Record<string, string>, all: string) {
    const counts = listing.data?.facets?.[field] || {};
    return [{ value: '', label: all, count: Object.values(counts).reduce((a, b) => a + b, 0) }, ...Object.entries(names).filter(([key]) => !['failed', 'running'].includes(key) || counts[key]).map(([value, label]) => ({ value, label, count: counts[value] || 0 }))];
  }
  function addFiles(files: File[]) {
    if (busyRef.current) return;
    setUploadError('');
    setQueue(previous => {
      const added = [...previous];
      for (const file of files) {
        const key = `${file.name}-${file.size}-${file.lastModified}`;
        if (added.some(item => item.key === key)) continue;
        if (added.length >= 40) { setUploadError('每批最多 40 份材料，请完成本批后再继续上传。'); break; }
        added.push({ key, token: crypto.randomUUID(), file, status: 'pending', progress: 0 });
      }
      return added;
    });
  }
  function update(key: string, patch: Partial<UploadItem>) { setQueue(previous => previous.map(item => item.key === key ? { ...item, ...patch } : item)); }
  function send(item: UploadItem): Promise<{ id: string }> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('file', item.file); form.append('upload_token', item.token); form.append('org_id', org); form.append('domain', domain);
      if (projectId) form.append('project_id', projectId);
      if (taskId) form.append('task_id', taskId);
      if (matterId) form.append('matter_id', matterId);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/materials/upload'); xhr.timeout = 180000;
      xhr.upload.onprogress = event => { if (event.lengthComputable) update(item.key, { progress: Math.min(95, Math.round(event.loaded / event.total * 95)) }); };
      xhr.onerror = () => reject(new Error('网络连接中断，请重试这份材料'));
      xhr.ontimeout = () => reject(new Error('上传等待超时，请先查看材料目录确认是否已接入，再重试'));
      xhr.onload = () => {
        let result: any;
        try { result = JSON.parse(xhr.responseText); } catch { reject(new Error('服务返回异常，请检查服务后重试')); return; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(result);
        else reject(new Error(typeof result.detail === 'string' ? result.detail : '上传未完成，请核对文件后重试'));
      };
      xhr.send(form);
    });
  }
  async function upload(onlyKey?: string) {
    if (busyRef.current) return;
    const todo = queue.filter(item => (item.status === 'pending' || item.status === 'error') && (!onlyKey || item.key === onlyKey));
    if (!todo.length) return;
    busyRef.current = true; setBusy(true); setUploadError('');
    let success = 0;
    try {
      for (const item of todo) {
        update(item.key, { status: 'uploading', progress: 0, error: '' });
        try {
          if (item.file.size > 25 * 1024 * 1024) throw new Error('超过单份 25 MB 上限，请按完整章节拆分');
          if (!/\.(txt|md|pdf|docx|html|htm)$/i.test(item.file.name)) throw new Error('该格式不支持。请使用 TXT、MD、PDF、DOCX 或 HTML');
          const result = await send(item);
          update(item.key, { status: 'success', progress: 100, id: result.id }); success += 1;
        } catch (error) { update(item.key, { status: 'error', error: error instanceof Error ? error.message : '上传失败，可单独重试', progress: 0 }); }
      }
      listing.reload(); toast(`本轮已保存 ${success} / ${todo.length} 份材料；请进入原文后选择分段方式`);
    } finally { busyRef.current = false; setBusy(false); }
  }

  return <div className="page materials-page">
    <PageHeader eyebrow="资料归集与资产整理" title="素材整理" description="汇集制度要求、项目报告与业务经验，保留原文依据，逐步整理为可核对、可复用的本体资产。" actions={<button className="btn btn-primary" onClick={() => setUploadOpen(true)}><Upload size={17} /> 批量上传材料</button>} />
    {(matterId || taskId || projectId) && <div className="materials-context"><FolderOpen size={17} /><span>当前目录与本次上传均归入指定{matterId ? '专项事项' : taskId ? '工作任务' : '专题'}，采纳成果将同步关联。</span><Link className="btn btn-ghost" to={matterId ? (projectId ? `/projects/${projectId}?panel=matters&matter=${matterId}` : `/assets/${matterId}`) : taskId ? `/tasks/${taskId}` : `/projects/${projectId}`}>返回{matterId ? '事项' : taskId ? '任务' : '专题'}详情 <ArrowRight size={15} /></Link><Link to="/materials">查看全部资料</Link></div>}
    <div className="materials-intro">
      <div className="materials-intro-copy"><span className="materials-kicker"><BookOpen size={15} /> 原文归集 · 语义分段 · 资产萃取</span><h2>先读懂材料，再提炼知识价值</h2><p>原件完整留存，分段方式由你选择。原文分段与资产萃取分别确认，让要求、事实和实践经验各有依据。</p><button className="btn btn-ghost" onClick={() => setUploadOpen(true)}>归集一批材料 <ArrowRight size={15} /></button></div>
      <div className="materials-flow"><div><span>01</span><FileText size={22} /><strong>归集原始材料</strong><small>多份上传、逐份留存</small></div><ArrowRight size={16} /><div><span>02</span><Sparkles size={22} /><strong>确认原文分段</strong><small>人工整理与 AI 对话</small></div><ArrowRight size={16} /><div><span>03</span><ShieldCheck size={22} /><strong>萃取并核对资产</strong><small>采纳草稿、审定关联</small></div></div>
    </div>
    <div className="metric-grid materials-metrics"><Stat label="符合条件的材料" value={listing.data?.total ?? '—'} detail="标签计数覆盖全部结果" /><Stat label="当前页原文片段" value={items.reduce((sum, item) => sum + Number(item.payload?.chunk_count || 0), 0)} detail="阅读分段可调，出处保持稳定" /><Stat label="当前页候选成果" value={currentCandidates} detail="可编辑、合并、核对与采纳" /><Stat label="当前页待分段材料" value={items.filter(item => item.payload?.segmentation_status === 'unsegmented').length} detail="已保留连续原文，等待选择" /></div>
    <Card className="materials-library">
      <div className="materials-section-top"><div><h2>材料目录</h2><p className="muted small">按整理状态和来源直接切换；数量随检索与其他筛选实时变化。</p></div><span className="materials-count">{listing.data?.total || 0} 份资料</span></div>
      <div className="materials-library-filters"><label className="materials-search"><Search size={17} /><input className="input" value={query} placeholder="搜索材料名称、正文摘要或业务主题" onChange={event => { setQuery(event.target.value); }} /></label><FilterTabs label="整理状态" options={facetOptions('status', statusNames, '全部状态')} value={status} onChange={value => { setStatus(value); }} /><FilterTabs label="材料来源" options={facetOptions('source_kind', sourceNames, '全部来源')} value={source} onChange={value => { setSource(value); }} /></div>
      {listing.loading ? <Loading /> : listing.error ? <div className="materials-error"><p>{String(listing.error)}</p><button className="btn" onClick={listing.reload}>重新加载</button></div> : items.length ? <div className="materials-list">{items.map(item => <Link to={`/materials/${item.id}`} className="materials-row" key={item.id}><div className={`materials-fileicon ${item.source_kind === 'regulation' ? 'is-regulation' : ''}`}><FileText size={25} /><span>{String(item.payload?.format || '文档')}</span></div><div className="materials-row-main"><div className="materials-row-title"><h3>{item.title}</h3><Badge>{sourceNames[item.source_kind] || item.source_kind}</Badge></div><p>{item.summary || '打开材料，对照原文开展梳理、核对与资产关联。'}</p><div className="materials-meta"><span>{item.domain || '综合管理'}</span><span>{orgNames.get(item.org_id) || item.org_id || '待明确归属单位'}</span><span>{item.updated_at ? new Date(item.updated_at).toLocaleDateString('zh-CN') : '—'} 更新</span></div></div><div className="materials-row-progress"><Badge>{statusNames[item.status] || item.status}</Badge><small>{item.payload?.segmentation_status === 'unsegmented' ? '原文已保存 · 待选择分段' : `${item.payload?.candidate_count || 0} 项候选 · ${item.payload?.chunk_count || 0} 个片段`}</small></div><ArrowRight size={18} className="materials-row-arrow" /></Link>)}</div> : <Empty title="没有找到符合条件的材料" description="调整检索条件，或上传制度、项目报告、案例与工作指引开始整理。" />}
      {listing.data && listing.data.total > 24 && <div className="materials-pagination"><button className="btn" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} / {Math.ceil(listing.data.total / 24)} 页</span><button className="btn" disabled={page * 24 >= listing.data.total} onClick={() => setPage(page + 1)}>下一页</button></div>}
    </Card>
    <div className="materials-footnotes"><div><Files size={20} /><div><strong>一份材料可以贡献多类资产</strong><p>要求、事实、分析思路和实践经验分别整理，通过共同来源彼此关联。</p></div></div><div><FolderOpen size={20} /><div><strong>宽大业务表使用台账接入</strong><p>多级表头、单位口径和字段映射，在<Link to="/datasets">台账接入工作台</Link>中处理。</p></div></div></div>
    {uploadOpen && <Modal title="批量上传待整理材料" onClose={() => !busy && setUploadOpen(false)} wide><p className="muted">可一次选择多份文件，共用本批归属与业务领域。上传后先查看连续原文，自主选择人工整理或 AI 对话调优。</p><FileIntake files={queue.map(item => item.file)} onFiles={addFiles} multiple accept=".txt,.md,.pdf,.docx,.html,.htm" maxFileSize={25 * 1024 * 1024} maxFiles={40} disabled={busy} showFiles={false} title="选择或拖入一批原始材料" description="TXT / Markdown / PDF / Word / HTML · 每份不超过 25 MB · 每批最多 40 份" onValidationError={setUploadError} />
      <div className="grid-2"><label className="form-field">归属单位<select className="select" disabled={busy} value={org} onChange={event => setOrg(event.target.value)}><option value="">暂未明确</option>{organizations.data?.items.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label><label className="form-field">业务领域<input className="input" disabled={busy} value={domain} onChange={event => setDomain(event.target.value)} placeholder="如：数据治理、采购管理" /></label></div>
      {!!queue.length && <><div className="materials-upload-summary"><strong>{queue.length} 份材料</strong><span>{completed} 已保存 · {failed} 未完成 · {queue.length - completed - failed} 待处理</span>{!busy && completed > 0 && <button className="btn btn-ghost" onClick={() => setQueue(queue.filter(item => item.status !== 'success'))}>移出已完成项</button>}</div><div className="materials-upload-queue" aria-live="polite">{queue.map(item => <div className={`materials-upload-item is-${item.status}`} key={item.key}><FileText size={19} /><div className="materials-upload-info"><strong>{item.file.name}</strong><small>{item.status === 'error' ? item.error : item.status === 'success' ? '原件与连续原文已保存，尚未分段' : item.status === 'uploading' ? item.progress >= 95 ? '文件已传输，正在读取原文…' : `正在上传 ${item.progress}%` : `${(item.file.size / 1024).toFixed(1)} KB · 等待上传`}</small>{item.status === 'uploading' && <progress value={item.progress} max="100" />}</div>{item.status === 'success' ? <Link className="btn btn-ghost" to={`/materials/${item.id}`}><Check size={15} /> 查看原文</Link> : item.status === 'error' ? <button className="btn btn-ghost" disabled={busy} onClick={() => upload(item.key)}><RotateCcw size={14} /> 重试</button> : null}{!busy && item.status !== 'success' && <button className="materials-icon-button" title={`移除 ${item.file.name}`} onClick={() => setQueue(queue.filter(value => value.key !== item.key))}><X size={15} /></button>}</div>)}</div></>}
      <div className="materials-hint"><ShieldCheck size={17} /><span>逐份接入，某份失败不会影响其他材料；成功项不会重复上传。扫描 PDF 请先进行文字识别，原始文件与整理成果均保存在本地。</span></div>{uploadError && <div className="materials-error" role="alert">{uploadError}</div>}<div className="materials-modal-actions"><button className="btn" disabled={busy} onClick={() => setUploadOpen(false)}>{completed ? '完成，返回目录' : '取消'}</button><button className="btn btn-primary" disabled={busy || !remaining} onClick={() => upload()}><Upload size={16} />{busy ? '正在逐份接入…' : completed || failed ? `继续上传 ${remaining} 份` : `开始上传${remaining ? ` ${remaining} 份` : ''}`}</button></div>
    </Modal>}
  </div>;
}
export default Materials;
