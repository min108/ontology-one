import { Fragment, ReactNode, useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Archive, ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Briefcase, Building2, Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Database, FileCheck2, FileClock, FileText, GitBranch, GitCompareArrows, Info, Layers, Link2, ListChecks, Network, Pencil, Plus, RotateCcw, Scale, Send, ShieldCheck, Sparkles, Workflow, X } from 'lucide-react';
import { api, useApi } from '../api';
import { Empty, Loading, Modal, useToast, ParagraphText } from '../components/ui';
import { AssetEntity, assetField, assetKinds, assetRoute, assetTopic, assetScenario, kindLabel, plainText, readableStatus, sourceLabel } from './Assets';
import './assets.css';
import { AssetPlacement } from '../components/AssetPlacement';
import { CollectionMembership } from './Knowledge';
import { DataInstances } from '../components/DataInstances';
import {AuthorityRuleSummary} from '../components/AuthorityRuleSummary';
import {usePageState,useSessionValue} from '../navigation';
import './catalog.css';

type Relation = { id?: string; source: string; target: string; type?: string; label?: string; status?: string; source_title?: string; target_title?: string; source_kind?: string; target_kind?: string; payload?: Record<string, any> };
type ActionName = 'submit' | 'approve' | 'return' | 'publish' | 'revise' | 'archive';
const actionNames: Record<ActionName, string> = { submit: '提交审核', approve: '审核通过', return: '退回修改', publish: '发布资产', revise: '发起修订', archive: '归档资产' };
const relationLabels: Record<string, string> = { references: '引用', referenced_by: '被引用', derived_from: '提炼自', based_on: '依据', requires: '需要', uses: '使用', uses_data: '使用数据', supports: '支撑', governs: '约束', implements: '落实', implements_rule: '落实规则', applies_to: '适用于', belongs_to: '归属于', contains: '包含', assigned_to: '交办', responsible_for: '负责', rectifies: '改进', related_to: '相关', maps_to: '映射到', produces: '形成', verifies: '核验', cites: '引用原文', evidence: '证据', informs: '提供参考', contributes_to: '服务于', source: '来源' };
const labelMap: Record<string, string> = { name: '名称', title: '名称', description: '说明', value: '取值', unit: '单位', threshold: '阈值', default: '默认值', source: '来源', source_id: '来源编码', status: '状态', owner: '负责人', date: '日期', due_date: '完成期限', progress: '进度', stage: '阶段', type: '类型', note: '备注', reason: '原因', scope: '适用范围', condition: '适用条件', result: '结果', content: '内容', instruction: '工作要求', evidence: '核验材料', parameter: '参数', sensitivity: '敏感级别', format: '参考形式', constraints: '值域与约束', category: '分类', completed: '已完成', checked: '已核对', required: '必需', priority: '优先级', maturity: '成熟度', label: '名称', column_name: '源字段', standard_id: '标准数据项', transform: '转换方式', verified: '已核验', org_id: '所属单位', effective_from: '生效日期', effective_to: '失效日期', version: '版本', risk: '风险提示', expected: '预期结果', actual: '实际结果' };
const asArray = (value: any) => Array.isArray(value) ? value : value?.items || value?.versions || [];
const dateLabel = (date?: string) => { if (!date) return '未记录'; const d = new Date(date); return Number.isNaN(d.getTime()) ? date : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai', hour12: false }).format(d); };

function ReferenceText({ text }: { text: string }) {
  return <>{text.split(/((?:[DAR]\.\d{2}\.\d{2}\.\d{3}(?:\.T\d+)?)|(?:L-[NP]\d-\d{3}))/g).map((part, index) => /^(?:[DAR]\.\d{2}\.\d{2}\.\d{3}(?:\.T\d+)?|L-[NP]\d-\d{3})$/.test(part) ? <Link key={index} className="asset-detail-inline-link" to={assetRoute(part, part.startsWith('L-') ? 'material' : undefined)}>{part}</Link> : <Fragment key={index}>{part}</Fragment>)}</>;
}

function RichText({ value, empty = '内容尚待补充', steps = false }: { value: any; empty?: string; steps?: boolean }) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return <p className="asset-empty-value">{empty}</p>;
  if (Array.isArray(value)) {
    if (value.every(item => typeof item !== 'object' || item === null)) return <ul className={steps ? 'asset-method-steps' : 'asset-prose-list'}>{value.map((item, index) => <li key={index}>{steps && <span className="asset-method-number">{String(index + 1).padStart(2, '0')}</span>}<p className="asset-prose"><ReferenceText text={plainText(item)} /></p></li>)}</ul>;
    return <div className="asset-stack-list">{value.map((item, index) => <div className="asset-source-item" key={index}>{typeof item === 'object' && item !== null ? <ObjectDetails object={item} /> : <p className="asset-prose">{plainText(item)}</p>}</div>)}</div>;
  }
  if (typeof value === 'object') return <ObjectDetails object={value} />;
  const text = plainText(value);
  const segments = steps ? text.split(/(?=[①②③④⑤⑥⑦⑧⑨⑩])|\n(?=\s*\d+[.、．])/).map(x => x.trim()).filter(Boolean) : [];
  if (steps && segments.length > 1) return <ol className="asset-method-steps">{segments.map((segment, index) => <li key={index}><span className="asset-method-number">{String(index + 1).padStart(2, '0')}</span><p><ReferenceText text={segment.replace(/^[①②③④⑤⑥⑦⑧⑨⑩]\s*/, '')} /></p></li>)}</ol>;
  return <p className="asset-prose"><ReferenceText text={text} /></p>;
}

function ObjectDetails({ object }: { object: Record<string, any> }) {
  if (!object || typeof object !== 'object') return <RichText value={object} />;
  const entries = Object.entries(object).filter(([key, value]) => value !== null && value !== undefined && !['id', 'chunk_id', 'material_id', 'source_url', 'source_file', 'is_demo'].includes(key));
  return <dl className="asset-info-grid">{entries.map(([key, value]) => <div key={key}><dt>{labelMap[key] || (/[^\u0000-\u007f]/.test(key) ? key : key.replaceAll('_', ' '))}</dt><dd>{typeof value === 'object' ? <RichText value={value} /> : <ReferenceText text={key === 'status' ? readableStatus(String(value)) : plainText(value)} />}</dd></div>)}</dl>;
}

function Section({ title, description, icon: Icon, children, className = '' }: { title: string; description?: string; icon?: any; children: ReactNode; className?: string }) {
  return <section className={`asset-section ${className}`}><div className="asset-section-heading"><div><h2>{Icon && <Icon size={16} strokeWidth={1.7} />}{title}</h2>{description && <p>{description}</p>}</div></div><div className="asset-section-content">{children}</div></section>;
}

function InfoGrid({ items, single = false }: { items: [string, any][]; single?: boolean }) {
  return <dl className={`asset-info-grid ${single ? 'asset-info-single' : ''}`}>{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{plainText(value) ? <RichText value={value} /> : <span className="asset-empty-value">尚未记录</span>}</dd></div>)}</dl>;
}

function DataContent({ entity }: { entity: AssetEntity }) {
  const f = (...keys: string[]) => assetField(entity, ...keys);
  return <>
    {entity.payload?.snapshot_readonly ? <Section title="历史口径与数据实例" icon={Database}><p className="asset-prose">当前阅读的是第 {entity.version} 版标准留档。字段对应会随业务核对更新，当前生产数据实例不直接挂接到此历史口径。</p><Link className="asset-material-cta" to={`/assets/${encodeURIComponent(entity.id)}#data-instances`}><span>打开当前标准，查看现行关联的原表数据</span><ArrowUpRight size={15}/></Link></Section> : <DataInstances standardId={entity.id}/>}
    <Section title="数据定义与业务口径" icon={Database} description="明确这项数据表示什么，以及它的业务边界。"><RichText value={f('definition', '业务含义', 'content') || entity.summary} /><InfoGrid items={[
      ['二级分类', f('category', '二级分类')], ['三级分类', f('subcategory', '三级分类')], ['数据形态', f('data_form', '数据形态')], ['资产归属', f('ownership', '资产归属')],
    ]} /></Section>
    <Section title="表示方式与质量约束" icon={ListChecks}><InfoGrid items={[
      ['参考形式', f('format', '参考形式')], ['值域与约束', f('constraints', '值域与约束')], ['标准依据', f('standard_basis', '标准依据')], ['分类分级', f('sensitivity', '敏感级别')],
    ]} /></Section>
    <Section title="用正例与反例划清边界" icon={BookOpen}><div className="asset-example-grid"><div className="asset-example"><h3><CheckCircle2 size={14} /> 规范示例</h3><p>{plainText(f('positive_example', '正例')) || '暂未补充规范示例'}</p></div><div className="asset-example is-negative"><h3><X size={14} /> 典型误用</h3><p>{plainText(f('negative_example', '反例')) || '暂未补充误用示例'}</p></div></div></Section>
  </>;
}

function RuleContent({ entity }: { entity: AssetEntity }) {
  if(entity.payload?.authority_spec)return <AuthorityRuleSummary entity={entity}/>;
  const f = (...keys: string[]) => assetField(entity, ...keys);
  const original = f('original_text', '规则内容', 'content');
  return <>
    <Section title="规则要求" icon={Scale}><RichText value={f('obligation', 'content', '规则内容') || entity.summary} /><InfoGrid items={[
      ['规则类型', f('rule_type', '规则类型')], ['资产来源', f('asset_source') || '待标注'], ['适用对象与业务范围', f('scope', '适用范围')],
    ]} /></Section>
    <Section title="适用条件与例外" icon={ListChecks}><InfoGrid single items={[
      ['触发条件', f('conditions', '适用条件')], ['例外与排除情形', f('exceptions', '例外情形')], ['核查与判断说明', f('review_note', 'verification_note', '审核说明')],
    ]} /></Section>
    <Section title="依据原文" icon={FileText} description="保留引用正文，可沿来源定位回到材料核对。">{original ? <blockquote className="asset-original-quote">{plainText(original)}</blockquote> : <RichText value="" empty="尚未登记引用原文。" />}<InfoGrid items={[
      ['依据名称', f('basis', '依据法规', '依据名称')], ['条款位置', f('clause', '条款号')], ['制定或发布机关', f('authority', '发布机关')], ['法规编号', f('law_id', '法规编号')],
    ]} />{f('law_id') && <Link className="asset-material-cta" to={`/materials/${encodeURIComponent(plainText(f('law_id')))}?locator=${encodeURIComponent(plainText(f('clause')))}`}><span>打开法规原文并核对条款</span><ArrowUpRight size={15} /></Link>}</Section>
    <Section title="版本时效与适用判断" icon={FileClock}><InfoGrid items={[
      ['生效日期', f('effective_from', '生效日期')], ['失效或终止日期', f('effective_to', '失效日期')], ['法规时效状态', f('legal_status', 'temporal_status', 'validity', '时效状态')], ['版本依据', f('version_info', 'version_note', '版本说明')],
    ]} /><RichText value={f('temporal_note', 'applicability_note')} empty="判断具体业务时，应将所引版本与业务发生时间、适用对象一并核对。" /></Section>
  </>;
}

function AlgorithmContent({ entity }: { entity: AssetEntity }) {
  const f = (...keys: string[]) => assetField(entity, ...keys);
  return <>
    <Section title="业务场景与分析目标" icon={Workflow}><RichText value={f('scenario', '应用场景', 'content') || entity.summary} /><InfoGrid items={[
      ['算法类型', f('algorithm_type', '算法类型')], ['所属主题', f('category', '业务主题')], ['资产成熟度', f('maturity') || '方法说明'], ['适用范围', f('scope', 'applicability')],
    ]} /></Section>
    <Section title="输入、方法与结果的完整路径" icon={GitBranch}><div className="asset-flow"><div><strong>业务输入</strong><small>标准数据与证据材料</small></div><ArrowRight size={17} /><div><strong>分析方法</strong><small>条件、计算与人工核验</small></div><ArrowRight size={17} /><div><strong>结果交付</strong><small>线索、画像或核查清单</small></div></div><RichText value={f('inputs', '数据需求')} empty="暂未明确所需输入。" /></Section>
    <Section title="分析方法" icon={ListChecks} description="保留每个分析步骤，便于逐项核对其业务含义。"><RichText value={f('method', '分析方法', 'steps')} steps /></Section>
    <Section title="判断条件与参数" icon={ShieldCheck}><RichText value={f('conditions', '判定标准', 'criteria')} /><InfoGrid single items={[
      ['参数定义', f('parameters', 'params')], ['方法边界与人工复核', f('boundary', 'limitations', 'review_note')],
    ]} /></Section>
    <Section title="输出与使用方式" icon={FileCheck2}><RichText value={f('outputs', '输出结果', 'output')} /><RichText value={f('follow_up', 'next_action')} empty="" /></Section>
  </>;
}

const knowledgeLabels: Record<string, string> = { initial_signal: '初始信号', signals: '初始信号', evidence_path: '关键证据与核查路径', evidence: '关键证据', key_distinction: '关键辨析', alternative_explanation: '其他合理解释', alternatives: '其他解释', missing_evidence: '尚待补充', review_conclusion: '复核判断', reuse_boundary: '适用边界', context: '事项情境', steps: '核查路径', boundary: '适用边界' };
function KnowledgeInsights({ entity }: { entity: AssetEntity }) {
  const p = entity.payload || {};
  const structured = p.structured_insights;
  let sections: { title: string; content: any }[] = [];
  if (structured && typeof structured === 'object') sections = Object.entries(structured).map(([key, content]) => ({ title: knowledgeLabels[key] || key, content }));
  else {
    const text = plainText(p.content || p['正文']);
    const headings = /^(?:#{1,4}\s*)?(事项情境|业务情境|业务背景|初始信号|核心问题|关键辨析|其他解释|其他合理解释|关键证据|核查与证据|当前认识|复核判断|核查路径|适用边界与反例|适用边界|后续反馈|经验启示|尚待补充)(?:[:：]\s*(.*))?\s*$/gm;
    const matches = [...text.matchAll(headings)];
    if (matches.length) {
      const intro = text.slice(0, matches[0].index).trim();
      if (intro && !intro.startsWith('演示内容：')) sections.push({ title: '内容概述', content: intro });
      matches.forEach((match, index) => sections.push({ title: match[1], content: [match[2], text.slice((match.index || 0) + match[0].length, matches[index + 1]?.index ?? text.length).trim()].filter(Boolean).join('\n') }));
    } else if (text) sections = [{ title: '业务认识', content: text }];
  }
  const missing = sections.filter(section => /尚待|待补|缺口/.test(section.title) || !plainText(section.content).trim() || /^(尚待提供者补充|尚待补充者提供|尚未提供|未提供|待补充)/.test(plainText(section.content).trim()));
  const present = sections.filter(section => !missing.includes(section));
  const missingNames = Array.isArray(p.unanswered_dimensions) ? p.unanswered_dimensions.map((value: any) => knowledgeLabels[String(value)] || plainText(value)).filter(Boolean) : [];
  return <><div className="knowledge-insights">{present.map((section, index) => <article className="knowledge-insight" key={`${section.title}-${index}`}><h3><span>{String(index + 1).padStart(2, '0')}</span>{section.title}</h3><RichText value={section.content} /></article>)}</div>{(!sections.length) && <RichText value="" />}{(missing.length > 0 || missingNames.length > 0 || p.questions) && <details className="knowledge-missing"><summary><Info size={14} /><strong>尚待补充的材料与问题</strong><span>{missing.length || missingNames.length || 1} 项</span></summary>{missing.map((section, index) => <div key={index}><h4>{section.title}</h4><RichText value={section.content} /></div>)}{missingNames.length > 0 && <p className="asset-prose">待完善维度：{missingNames.join('、')}</p>}{p.questions && <RichText value={p.questions} />}</details>}</>;
}
function KnowledgeContent({ entity }: { entity: AssetEntity }) {
  const f = (...keys: string[]) => assetField(entity, ...keys);
  const sections=entity.payload?.article_sections;
  if(Array.isArray(sections)&&sections.length)return <>{!entity.payload?.snapshot_readonly&&<CollectionMembership assetId={entity.id}/>}<section className="asset-section"><div className="asset-section-content"><nav className="knowledge-article-toc" aria-label="本文目录">{sections.map((s:any,i:number)=><a key={s.id||i} href={'#'+encodeURIComponent(s.id||'section-'+i)}><span>{String(i+1).padStart(2,'0')}</span>{s.title}</a>)}</nav>{sections.map((s:any,i:number)=><article className="knowledge-article-section" id={s.id||'section-'+i} key={s.id||i}><h2><span>{String(i+1).padStart(2,'0')}</span>{s.title}</h2><div className="article-prose"><ParagraphText text={plainText(s.content)}/></div>{s.source_refs?.length>0&&<details className="knowledge-chapter-sources"><summary>本节原文依据 · {s.source_refs.length} 处</summary>{s.source_refs.map((ref:any,n:number)=>{const p=new URLSearchParams();if(ref.chunk_id)p.set('chunk',ref.chunk_id);if(ref.anchor_id)p.set('anchor',ref.anchor_id);else if(ref.quote)p.set('quote',ref.quote);if(ref.source_version)p.set('source_version',ref.source_version);if(ref.locator)p.set('locator',ref.locator);return <div key={n}><blockquote>{ref.quote||ref.excerpt}</blockquote><Link to={assetRoute(ref.material_id||ref.id,'material')+'?'+p}>{ref.title||ref.material_id} · {ref.locator||'原文定位'}<ArrowUpRight size={12}/></Link></div>})}</details>}{s.editor_note&&<p className="muted">编者补充：{s.editor_note}</p>}</article>)}</div></section></>;
  return <>
    <Section title="背景与需要辨明的问题" icon={BookOpen}><RichText value={f('context', '背景') || entity.summary} /><InfoGrid items={[
      ['知识类型', f('knowledge_type', '知识类型')], ['适用场景', f('scenario', 'scope')],
    ]} /></Section>
    <Section title="业务认识与关键辨析" icon={Layers} description="分开阅读初始信号、证据和其他解释，保留判断过程及适用边界。"><KnowledgeInsights entity={entity} />{f('key_points', '要点') && <div className="knowledge-key-points"><h3>复用前应记住的要点</h3><RichText value={f('key_points', '要点')} /></div>}</Section>
    <Section title="可复用的核查路径" icon={ListChecks}><RichText value={f('steps', '核查步骤', 'verification_steps')} steps /></Section>
    <Section title="方法边界与反例" icon={ShieldCheck}><InfoGrid single items={[
      ['适用边界', f('boundary', '边界')], ['反例与常见误判', f('counterexample', 'negative_example', '反例')],
    ]} /></Section>
    <Section title="经验启示与后续复用" icon={GitBranch}><RichText value={f('lessons', '启示', 'reuse_note')} /></Section>
  </>;
}

function BusinessContent({ entity }: { entity: AssetEntity }) {
  const f = (...keys: string[]) => assetField(entity, ...keys);
  const kind = entity.kind;
  if (kind === 'issue' && f('record_type') === 'analysis_run') {
    const findings = Array.isArray(f('findings')) ? f('findings') : [];
    const datasetId = plainText(f('dataset_id'));
    const statistics: [string, any][] = [['检查记录', f('total_rows')], ['正常记录', f('normal_rows')], ['待核查发现', f('finding_count')], ['涉及记录', f('affected_rows')], ['证据不足', f('insufficient_rows')], ['分析基准日期', f('as_of')]];
    return <>
      <div className="asset-generic-facts">{statistics.map(([label, value]) => <div className="asset-generic-fact" key={label}><span>{label}</span><strong>{value === '' ? '未记录' : plainText(value)}</strong></div>)}</div>
      <Section title="本次分析结论" icon={FileCheck2}><RichText value={f('conclusion') || entity.summary} />{datasetId && <Link className="asset-material-cta" to={assetRoute(datasetId, 'dataset')}><span>查看来源台账 · {plainText(f('dataset_title')) || datasetId}</span><ArrowUpRight size={15} /></Link>}<Link className="asset-material-cta" to={`/applications?run=${encodeURIComponent(entity.id)}`}><span>回到本体应用查看本次完整分析</span><ArrowUpRight size={15} /></Link></Section>
      <Section title="发现事项与原始记录" icon={ListChecks} description="分析发现用于安排进一步核查，原始记录可穿透查看。">{findings.length ? findings.map((finding: any, index: number) => <article className="asset-source-item" key={index}><h3>{index + 1}. {finding.title || '待核查发现'}</h3><RichText value={finding.explanation || finding.description || finding.reason} /><div className="asset-pill-row">{(Array.isArray(finding.row_numbers) ? finding.row_numbers : []).map((row: any) => <Link className="asset-detail-inline-link asset-pill" key={row} to={`/datasets/${encodeURIComponent(datasetId)}?row=${encodeURIComponent(row)}`}>台账第 {row} 行 <ArrowUpRight size={11} /></Link>)}</div>{(finding.record || finding.values) && <details className="asset-record-details"><summary>查看本次取证记录</summary><RichText value={finding.values || finding.record} /></details>}</article>) : <p className="asset-empty-value">本次分析未记录待核查发现。</p>}</Section>
      <Section title="输入口径与方法边界" icon={ShieldCheck}><InfoGrid single items={[[ '本次使用的业务字段', f('fields') ], [ '适用边界与正常解释', f('boundary') ], [ '尚缺的核验材料', f('insufficient') ]]} /></Section>
    </>;
  }
  if (kind === 'case' || kind === 'issue') return <>
    <Section title={kind === 'case' ? '案例背景与核查问题' : '问题事实与待核实事项'} icon={Briefcase}><RichText value={f('scenario', 'context', 'background') || entity.summary} /><InfoGrid items={[
      ['证据状态', f('evidence_status')], ['核查责任人', f('owner')], ['问题分类', f('category', 'issue_type')], ['处置阶段', f('stage') || readableStatus(entity.status)],
    ]} /></Section>
    <Section title="已掌握的事实与发现" icon={FileText}><RichText value={f('findings', 'content', 'facts')} /></Section>
    <Section title="核查过程与证据辨析" icon={ListChecks}><RichText value={f('steps', 'analysis', 'verification_steps')} steps />{f('normal_explanation') && <InfoGrid single items={[[ '正常业务解释', f('normal_explanation') ]]} />}</Section>
    <Section title="阶段判断与后续工作" icon={ShieldCheck}><InfoGrid single items={[
      ['当前结论', f('conclusion', 'result')], ['未决证据与判断边界', f('boundary', 'missing_evidence', 'limitations')], ['下一步行动', f('next_action', 'follow_up')],
    ]} /></Section>
  </>;
  if (kind === 'project') return <>
    <div className="asset-generic-facts"><div className="asset-generic-fact"><span>牵头负责人</span><strong>{plainText(f('owner')) || '待安排'}</strong></div><div className="asset-generic-fact"><span>工作周期</span><strong>{plainText(f('period', 'due_date')) || '按专题计划推进'}</strong></div><div className="asset-generic-fact"><span>专题进度</span><strong>{f('progress') !== '' ? `${plainText(f('progress'))}%` : readableStatus(entity.status)}</strong></div></div>
    <Section title="专题目标与覆盖范围" icon={Briefcase}><RichText value={f('objective', 'objectives', 'content') || entity.summary} /><InfoGrid single items={[[ '业务与单位范围', f('scope') ], [ '协同成员', f('members') ]]} /></Section>
    <Section title="工作阶段与里程碑" icon={ListChecks}><RichText value={f('milestones', 'stages')} steps /></Section>
    <Section title="预期交付与验收要点" icon={FileCheck2}><RichText value={f('expected_outputs', 'deliverables', 'acceptance_criteria')} /></Section>
  </>;
  if (kind === 'task') return <>
    <Section title="任务要求" icon={ListChecks}><RichText value={f('instruction', 'content') || entity.summary} /><InfoGrid items={[
      ['责任人', f('owner')], ['完成期限', f('due_date')], ['工作类型', f('work_type', 'task_type')], ['优先级', f('priority')],
    ]} /></Section>
    <Section title="办理清单与验收要求" icon={FileCheck2}><RichText value={f('checklist', 'steps')} steps /></Section>
    <Section title="办理进展与关联工作" icon={GitBranch}><RichText value={f('progress_note', 'result', 'next_action')} empty="办理进展尚待记录。" /><div className="asset-pill-row">{f('project_id') && <Link className="asset-detail-inline-link asset-pill" to={assetRoute(plainText(f('project_id')))}>查看关联专题 <ArrowUpRight size={12} /></Link>}{f('case_id') && <Link className="asset-detail-inline-link asset-pill" to={assetRoute(plainText(f('case_id')))}>查看关联案例 <ArrowUpRight size={12} /></Link>}</div></Section>
  </>;
  if (kind === 'organization') return <>
    <Section title="单位概况" icon={Building2}><RichText value={f('description', 'content', 'profile') || entity.summary} /><InfoGrid items={[
      ['组织层级', f('level', 'org_level')], ['单位类型', f('organization_type', 'org_type', 'type')], ['所属条线', f('business_line', 'sector') || entity.domain], ['管理职责', f('responsibilities')],
    ]} /></Section>
    <Section title="业务关注与业务范围" icon={ShieldCheck}><RichText value={f('scope', 'business_focus', 'focus_areas')} /></Section>
    <Section title="关联资产与工作入口" icon={Network}><p className="asset-prose">在关联页查看本单位参与的材料、专题、任务与业务资产，或在图谱中展开组织关系。</p><Link className="asset-material-cta" to={`/graph?node=${encodeURIComponent(entity.id)}&mode=focus`}><span>围绕本单位展开关联</span><ArrowUpRight size={15} /></Link></Section>
  </>;
  return <>
    <Section title="业务内容" icon={FileText}><RichText value={f('content', 'body', 'description', 'instruction', 'context') || entity.summary} /></Section>
    <Section title="业务属性" icon={ListChecks}><InfoGrid items={[
      ['对象类型', kindLabel(kind)], ['业务分类', f('category', 'type', 'material_type')], ['适用范围', f('scope')], ['版本说明', f('version_info')], ['责任人', f('owner')], ['下一步工作', f('next_action')],
    ]} /></Section>
    {(kind === 'material' || kind === 'dataset') && <Link className="asset-material-cta" to={assetRoute(entity.id, kind)}><span>进入{kind === 'material' ? '材料阅读与提炼' : '台账数据与映射'}页面</span><ArrowUpRight size={15} /></Link>}
  </>;
}

function Sources({ entity }: { entity: AssetEntity }) {
  const payload = entity.payload || {};
  const references = Array.isArray(payload.source_refs) ? payload.source_refs : [];
  const fallbackId = payload.source_id || payload.law_id;
  const refs = references.length ? references : fallbackId ? [{ material_id: fallbackId, title: payload.source_title || payload.basis || '查看来源材料', locator: payload.source_locator || payload.clause }] : [];
  return <Section title="材料出处与原文定位" icon={FileText}>
    {refs.length ? refs.map((ref: any, index: number) => { const materialId = ref.material_id || ref.source_id || ref.id; const route = typeof ref.href === 'string' && /^\/(materials|datasets|assets)\//.test(ref.href) ? ref.href : assetRoute(materialId || '', ref.kind); const locator = ref.locator || ref.source_locator; const query = new URLSearchParams(); if (locator) query.set('locator', plainText(locator)); if (ref.chunk_id) query.set('chunk', String(ref.chunk_id)); if(ref.anchor_id)query.set('anchor',ref.anchor_id);else if(ref.quote)query.set('quote',ref.quote);if(ref.source_version)query.set('source_version',String(ref.source_version));
      return <article className="asset-source-item" key={`${materialId}-${index}`}><h3>{materialId ? <Link to={`${route}${query.size ? `?${query}` : ''}`}>{ref.title || materialId}<ArrowUpRight size={12} /></Link> : <span>{ref.title || '来源信息待完善'}</span>}</h3>{locator && <p>定位 · {plainText(locator)}</p>}{(ref.quote||ref.excerpt) && <p className="asset-source-excerpt">{plainText(ref.quote||ref.excerpt)}</p>}</article>;
    }) : <p className="asset-empty-value">暂未关联来源材料，可通过修订补充依据与定位信息。</p>}
    {payload.source_url && /^https?:\/\//.test(payload.source_url) && <a className="asset-material-cta" href={payload.source_url} target="_blank" rel="noreferrer"><span>访问记录的来源网页</span><ArrowUpRight size={14} /></a>}
  </Section>;
}

function RelationsPanel({ entity }: { entity: AssetEntity }) {
  const [view,updateView]=usePageState({relation_filter:'all',relation_page:'1'});const filter=view.relation_filter,setFilter=(v:string)=>updateView({relation_filter:v,relation_page:'1'}),page=Math.max(1,Number(view.relation_page)||1),setPage=(v:number|((n:number)=>number))=>updateView({relation_page:String(typeof v==='function'?v(page):v)});
  const query = new URLSearchParams({ node: entity.id, status: filter === 'all' ? '' : filter, page: String(page), page_size: '60' });
  const result = useApi<{ items: Relation[]; total: number }>(`/relations?${query}`);
  const records = result.data?.items || [];
  const incoming = records.filter(r => r.target === entity.id);
  const outgoing = records.filter(r => r.source === entity.id);
  const incomingCount = entity.relation_counts?.incoming ?? entity.relations?.incoming?.length ?? 0;
  const outgoingCount = entity.relation_counts?.outgoing ?? entity.relations?.outgoing?.length ?? 0;
  const total = result.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / 60));
  const render = (relations: Relation[], direction: 'incoming' | 'outgoing') => relations.length ? relations.map((relation, index) => {
    const targetId = direction === 'outgoing' ? relation.target : relation.source;
    const title = direction === 'outgoing' ? relation.target_title : relation.source_title;
    const kind = direction === 'outgoing' ? relation.target_kind : relation.source_kind;
    let payload: any = relation.payload || {}; if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { payload = {}; } }
    return <div className="asset-relation-row" key={relation.id || `${targetId}-${index}`}><span className="asset-relation-symbol"><Link2 size={14} /></span><div><Link className="asset-relation-title" to={assetRoute(targetId, kind)}>{title || targetId}</Link><div className="asset-relation-info"><span>{relation.label || relationLabels[relation.type || ''] || '业务关联'}</span><span className={`asset-state asset-state-${relation.status}`}>{relation.status === 'confirmed' ? '已确认关联' : relation.status === 'candidate' ? '候选参考' : readableStatus(relation.status)}</span><span>{targetId}</span></div>{(payload.reason || payload.review_reason) && <p className="asset-relation-remark">{plainText(payload.review_reason || payload.reason)}</p>}</div><ArrowUpRight size={13} color="#a2aebc" /></div>;
  }) : <p className="asset-empty-value">本页暂无{direction === 'incoming' ? '来自其他对象的' : '指向其他对象的'}关联。</p>;
  return <>
    <div className="asset-relationship-overview"><div><strong>{incomingCount.toLocaleString()}</strong>关联到本项</div><ArrowRight size={20} /><div className="asset-current-node"><small>{entity.id}</small>{entity.title}</div><ArrowRight size={20} /><div><strong>{outgoingCount.toLocaleString()}</strong>本项关联至</div></div>
    <div className="asset-relation-filter">{[{ id: 'all', title: `全部关联 ${(incomingCount + outgoingCount).toLocaleString()}` }, { id: 'confirmed', title: '已确认' }, { id: 'candidate', title: '候选参考' }, { id: 'rejected', title: '已排除' }].map(item => <button key={item.id} className={filter === item.id ? 'is-active' : ''} onClick={() => { setFilter(item.id); setPage(1); }}>{item.title}</button>)}<Link className="btn btn-ghost" to={`/graph?node=${encodeURIComponent(entity.id)}&mode=focus`}><Network size={14} /> 在图谱中展开</Link></div>
    <p className="asset-proof-note">候选关联用于提供核查方向，尚不能作为确认依据。已确认关联表示关系经过确认，具体结论仍需结合原文版本与业务证据。</p>
    {result.loading ? <div className="asset-loading"><Loading /></div> : result.error ? <div className="asset-error"><Empty title="关联暂时无法加载" description={String(result.error)} /><button className="btn btn-ghost" onClick={result.reload}>重新加载</button></div> : <><div className="asset-relations-grid"><Section title="哪些对象关联到这里" icon={GitBranch}>{render(incoming, 'incoming')}</Section><Section title="这项资产连接了什么" icon={Network}>{render(outgoing, 'outgoing')}</Section></div>{total > 0 && <div className="asset-pagination"><span>当前筛选共 {total.toLocaleString()} 条关联，每页最多 60 条</span><div><button className="btn btn-ghost" aria-label="上一页关联" disabled={page <= 1} onClick={() => setPage(x => x - 1)}><ChevronLeft size={15} /></button><span>{page} / {pages} 页</span><button className="btn btn-ghost" aria-label="下一页关联" disabled={page >= pages} onClick={() => setPage(x => x + 1)}><ChevronRight size={15} /></button></div></div>}</>}
  </>;
}

function VersionHistory({ id, refresh }: { id: string; refresh: number }) {
  const versions = useApi<any>(`/entities/${encodeURIComponent(id)}/versions`, [id, refresh]);
  const rows = asArray(versions.data);
  if (versions.loading) return <div className="asset-loading"><Loading /></div>;
  if (versions.error) return <div className="asset-error"><Empty title="暂时无法读取修订历史" description={String(versions.error)} /><button className="btn btn-ghost" onClick={versions.reload}>重试</button></div>;
  if (!rows.length) return <Empty title="当前还没有历史版本" description="编辑保存或发起修订后，可在这里回看版本与变更说明。" />;
  return <div className="asset-history-list">{[...rows].sort((a, b) => Number(b.version) - Number(a.version)).map((row, index) => {
    let snapshot: any = row.snapshot || {};
    if (typeof snapshot === 'string') { try { snapshot = JSON.parse(snapshot); } catch { snapshot = {}; } }
    let payload = snapshot.payload || {};
    if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { payload = {}; } }
    const fields = ['definition', 'content', 'scenario', 'method', 'context', 'instruction', 'findings', 'objective'];
    const contentKey = fields.find(key => payload[key]);
    return <article className="asset-history-entry" key={row.id || `${row.version}-${index}`}><div className="asset-history-top"><div><FileClock size={16} color="#8b9db1" /><strong>版本 {row.version || index + 1}</strong>{snapshot.status && <span className={`asset-state asset-state-${snapshot.status}`}>{readableStatus(snapshot.status)}</span>}</div><time>{dateLabel(row.created_at)}</time></div><h3>{snapshot.title || '资产修订记录'}</h3><p>{row.reason || row.description || '已保存版本快照'}</p><details><summary>查看该版内容</summary><InfoGrid items={[[ '名称', snapshot.title ], [ '业务领域', snapshot.domain ], [ '摘要', snapshot.summary ], [ '来源', sourceLabel(snapshot.source_kind) ]]} />{contentKey && <div style={{ marginTop: 15 }}><RichText value={payload[contentKey]} /></div>}</details></article>;
  })}</div>;
}

const editFields: Record<string, [string, string, number?][]> = {
  data: [['definition', '业务定义与口径', 4], ['format', '参考形式', 2], ['constraints', '值域与约束', 3], ['standard_basis', '标准依据', 2], ['positive_example', '规范示例', 3], ['negative_example', '典型误用', 3]],
  rule: [['content', '规则要求', 5], ['scope', '适用范围', 3], ['conditions', '触发条件', 4], ['exceptions', '例外与排除情形', 3], ['review_note', '核查与判断说明', 3]],
  algorithm: [['scenario', '业务场景', 4], ['inputs', '输入需求', 4], ['method', '分析方法', 7], ['conditions', '判断条件', 4], ['outputs', '输出与使用方式', 4], ['maturity', '资产成熟度', 2]],
  knowledge: [['knowledge_type', '知识类型', 2], ['context', '业务背景', 4], ['content', '业务认识与辨析', 6], ['key_points', '关键要点', 4], ['steps', '核查路径', 5], ['boundary', '适用边界', 3], ['counterexample', '反例与误判', 3], ['lessons', '经验启示', 3]],
  project: [['objective', '工作目标', 4], ['scope', '覆盖范围', 4], ['expected_outputs', '预期交付', 4]],
  task: [['instruction', '任务要求', 5], ['owner', '责任人', 2], ['due_date', '完成期限', 2], ['checklist', '办理清单', 5]],
  case: [['scenario', '案例背景', 4], ['findings', '事实与发现', 5], ['evidence_status', '证据状态', 2], ['conclusion', '阶段结论', 4], ['next_action', '后续工作', 4]],
  issue: [['findings', '问题事实', 5], ['evidence_status', '证据状态', 2], ['conclusion', '阶段判断', 4], ['next_action', '后续核查', 4]],
  organization: [['description', '单位概况', 5], ['scope', '业务范围', 4], ['business_focus', '业务关注', 4]],
};

function EditModal({ entity, onClose, onSaved }: { entity: AssetEntity; onClose: () => void; onSaved: (openPlacement?: boolean) => void }) {
  const toast = useToast();
  const draftKey='asset-edit-'+entity.id+'-'+entity.version;
  const isArticle=entity.kind==='knowledge'&&Array.isArray(entity.payload?.article_sections)&&entity.payload.article_sections.length>0;
  const [chapters,setChapters]=useSessionValue<any[]>(draftKey+'-chapters',structuredClone(entity.payload?.article_sections||[]));
  const structured = entity.kind === 'knowledge' && entity.payload?.structured_insights && typeof entity.payload.structured_insights === 'object' && !Array.isArray(entity.payload.structured_insights) ? entity.payload.structured_insights : null;
  const baseFields = editFields[entity.kind] || [['content', '业务内容', 7] as [string, string, number]];
  const fields: [string, string, number?][] = isArticle?[]:structured ? [...baseFields.filter(([key]) => key !== 'content'), ...Object.keys(structured).map(key => [`structured_insights.${key}`, knowledgeLabels[key] || key, 4] as [string, string, number])] : baseFields;
  const originalValue = (key: string) => key.startsWith('structured_insights.') ? structured?.[key.split('.')[1]] : entity.payload?.[key];
  const [title, setTitle] = useSessionValue(draftKey+'-title',entity.title);
  const [summary, setSummary] = useSessionValue(draftKey+'-summary',entity.summary || '');
  const [domain, setDomain] = useSessionValue(draftKey+'-domain',entity.domain || '');
  const [reason, setReason] = useSessionValue(draftKey+'-reason','');
  const [values, setValues] = useSessionValue<Record<string,string>>(draftKey+'-values',Object.fromEntries(fields.map(([key]) => [key, plainText(originalValue(key))])));
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async (openPlacement = false) => {
    if (!title.trim()) { setError('请填写资产名称。'); return; }
    if (!reason.trim()) { setError('请说明本次修改的内容或依据，便于后续追溯。'); return; }
    if(isArticle&&chapters.some(s=>!s.title.trim()||!s.content.trim())){setError('请补充每一节的标题与正文，或移除空白章节。');return;}
    setSaving(true); setError('');
    const payload = { ...(entity.payload || {}) };
    if(isArticle){payload.article_sections=chapters;payload.content=chapters.map(s=>s.title+'\n'+s.content).join('\n\n');payload.editor_review_required=true;}
    const editedKeys=new Set([...changed,...fields.map(([key])=>key).filter(key=>values[key]!==plainText(originalValue(key)))]);
    for (const key of editedKeys) { const original = originalValue(key); const value = Array.isArray(original) && original.every(x => typeof x === 'string') ? values[key].split('\n').map(x => x.trim()).filter(Boolean) : values[key]; if (key.startsWith('structured_insights.')) payload.structured_insights = { ...payload.structured_insights, [key.split('.')[1]]: value }; else payload[key] = value; }
    if ([...editedKeys].some(key => key.startsWith('structured_insights.'))) payload.content = Object.entries(payload.structured_insights).map(([key, value]) => `${knowledgeLabels[key] || key}\n${plainText(value)}`).join('\n\n');
    const nextStatus = ['data', 'rule', 'algorithm', 'knowledge'].includes(entity.kind) && ['published', 'approved'].includes(entity.status || '') ? 'draft' : entity.status;
    try { await api(`/entities/${encodeURIComponent(entity.id)}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim(), summary: summary.trim(), domain: domain.trim(), payload, status: nextStatus, reason: reason.trim() }) }); toast('修改已保存，修订历史已更新'); ['chapters','title','summary','domain','reason','values'].forEach(suffix=>sessionStorage.removeItem('ontology-one-draft-'+draftKey+'-'+suffix)); onSaved(openPlacement); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : '保存失败，请重试。'); }
    finally { setSaving(false); }
  };
  return <Modal title={`编辑${kindLabel(entity.kind)}`} onClose={() => { if (!saving) onClose(); }} wide>
    <p className="asset-modal-note">补充清晰的业务表述与本次修改依据，让下一位使用者理解变化。未保存内容会在本次浏览器会话中暂存，再次打开编辑可继续。{['data', 'rule', 'algorithm', 'knowledge'].includes(entity.kind) && ['published', 'approved'].includes(entity.status || '') ? '已发布或已审核的内容保存后进入草稿，待重新审核发布。' : ''}</p>
    <div className="asset-edit-grid"><label className="asset-edit-full">名称<input aria-label="名称" autoFocus value={title} onChange={e => setTitle(e.target.value)} maxLength={500} /></label><label className="asset-edit-full">摘要<textarea aria-label="摘要" rows={3} value={summary} onChange={e => setSummary(e.target.value)} /></label><label className="asset-edit-full">业务领域<input aria-label="业务领域" value={domain} onChange={e => setDomain(e.target.value)} /></label><div className="asset-edit-section-title">业务内容</div>
      {isArticle&&<div className="asset-edit-full">{chapters.map((s,i)=><div className="knowledge-editor-chapter" key={s.id}><header><span>{i+1}</span><input className="input" aria-label={`第${i+1}节标题`} value={s.title} onChange={e=>setChapters(old=>old.map((c,n)=>n===i?{...c,title:e.target.value}:c))}/><button className="icon-btn" aria-label={`移除第${i+1}节`} disabled={chapters.length===1} onClick={()=>setChapters(old=>old.filter((_,n)=>n!==i))}><X size={15}/></button></header><textarea aria-label={`第${i+1}节正文`} value={plainText(s.content)} onChange={e=>setChapters(old=>old.map((c,n)=>n===i?{...c,content:e.target.value,manually_edited:true}:c))}/><small>{s.source_refs?.length||0} 处原文依据保留；新增判断请在修改说明中交代依据。</small></div>)}<button className="btn btn-ghost" onClick={()=>setChapters(old=>[...old,{id:'section-'+crypto.randomUUID().slice(0,8),title:'',content:'',source_refs:[],manually_edited:true}])}><Plus size={14}/>增加章节</button></div>}
      {fields.map(([key, label, rows]) => { const original = originalValue(key); const complex = typeof original === 'object' && original !== null && (!Array.isArray(original) || original.some(x => typeof x === 'object'));
        return complex ? <div key={key} className="asset-edit-full"><label>{label}</label><RichText value={original} /></div> : <label className="asset-edit-full" key={key}>{label}<textarea aria-label={label} rows={rows || 3} value={values[key] || ''} onChange={e => { setValues(old => ({ ...old, [key]: e.target.value })); setChanged(old => new Set([...old, key])); }} /></label>;
      })}<div className="asset-edit-section-title">修订留痕</div><label className="asset-edit-full">修改说明 <textarea aria-label="修改说明" rows={3} value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：补充适用范围，区分到账日期与指令发出日期。" /></label>
    </div>{error && <p className="asset-inline-error" role="alert">{error}</p>}<div className="asset-modal-footer"><button className="btn btn-ghost" onClick={onClose} disabled={saving}>取消</button><button className="btn btn-primary" onClick={() => save()} disabled={saving}>{saving ? '正在保存…' : '保存修改'}</button>{assetKinds.some(k => k.kind === entity.kind) && <button className="btn btn-ai" onClick={() => save(true)} disabled={saving}><Sparkles size={14} />保存并关联定位</button>}</div>
  </Modal>;
}

function ActionModal({ entity, action, onClose, onSaved }: { entity: AssetEntity; action: ActionName; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const descriptions: Record<ActionName, string> = { submit: '将当前内容提交审核，并记录本次提交的说明。', approve: '确认当前业务内容、来源和适用边界，并记录审核意见。', return: '说明需要补充或修正的内容，供下一轮修订处理。', publish: '将当前版本发布为可复用资产，并保留发布说明。', revise: '基于当前内容开启新的修订，保留既有版本以便回溯。', archive: '结束该资产的日常使用，保留内容与关系记录。' };
  const submit = async () => {
    if (!reason.trim()) { setError('请填写办理意见，便于记录责任与处理依据。'); return; }
    setSaving(true); setError('');
    try { await api(`/entities/${encodeURIComponent(entity.id)}/action`, { method: 'POST', body: JSON.stringify({ action, reason: reason.trim(), actor: '本地工作用户', ...(entity.payload?.authority_spec?{expected_version:entity.version}:{}) }) }); toast(`${actionNames[action]}已完成`); onSaved(); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败，请重试。'); }
    finally { setSaving(false); }
  };
  return <Modal title={actionNames[action]} onClose={() => { if (!saving) onClose(); }}><p className="asset-action-context">{descriptions[action]}</p><p className="asset-modal-note">{entity.title} · {readableStatus(entity.status)}</p><textarea className="asset-action-reason" aria-label="办理意见" autoFocus placeholder={action === 'return' ? '请写明退回原因和需要补充的材料或口径…' : '填写本次办理意见与依据…'} value={reason} onChange={e => setReason(e.target.value)} />{error && <p className="asset-inline-error" role="alert">{error}</p>}<div className="asset-modal-footer"><button className="btn btn-ghost" onClick={onClose} disabled={saving}>取消</button><button className="btn btn-primary" onClick={submit} disabled={saving}>{saving ? '正在办理…' : `确认${actionNames[action]}`}</button></div></Modal>;
}

export function AssetDetail() {
  const { id = '' } = useParams();
  const [queryParams,setQueryParams] = useSearchParams();
  const openPlacementFromLink = queryParams.get('placement') === '1';
  const tab=['detail','relations','history'].includes(queryParams.get('tab')||'')?queryParams.get('tab')!:'detail';
  const setTab=(value:string)=>{const p=new URLSearchParams(queryParams);p.set('tab',value);setQueryParams(p)};
  const [editing, setEditing] = useState(false);
  const [placement, setPlacement] = useState(false);
  const [action, setAction] = useState<ActionName | null>(null);
  const [refresh, setRefresh] = useState(0);
  const request = useApi<AssetEntity>(`/entities/${encodeURIComponent(id)}${queryParams.get('version')?'?version='+queryParams.get('version'):''}`, [id, refresh]);
  const entity = request.data;
  useEffect(() => { setEditing(false); setPlacement(openPlacementFromLink); setAction(null); }, [id, openPlacementFromLink]);
  const relatedCount = (entity?.relation_counts?.incoming ?? entity?.relations?.incoming?.length ?? 0) + (entity?.relation_counts?.outgoing ?? entity?.relations?.outgoing?.length ?? 0);
  const saved = (openPlacement = false) => { setRefresh(old => old + 1); request.reload(); if (openPlacement) setPlacement(true); };
  if (request.loading) return <div className="page asset-loading"><Loading /></div>;
  if (request.error || !entity) return <div className="page asset-error"><Empty title="暂时无法打开这项资产" description={request.error ? String(request.error) : '这项业务对象可能尚未导入，或链接已变化。'} /><div className="asset-pill-row"><button className="btn btn-primary" onClick={request.reload}>重新加载</button><Link className="btn btn-ghost" to="/assets"><ArrowLeft size={15} /> 返回业务资产</Link></div></div>;
  const meta = assetKinds.find(x => x.kind === entity.kind);
  const Icon = meta?.icon || (entity.kind === 'organization' ? Building2 : entity.kind === 'project' ? Briefcase : entity.kind === 'task' ? ListChecks : FileText);
  const payload = entity.payload || {};
  const isDemo = entity.source_kind === 'demo' || payload.is_demo;
  const displayKind = entity.kind === 'issue' && payload.record_type === 'analysis_run' ? '业务分析记录' : kindLabel(entity.kind);
  const legalStatus = entity.kind === 'rule' ? plainText(payload.legal_status || payload.temporal_status || payload.validity) : '';
  const returnPath = entity.kind === 'project' ? '/projects' : entity.kind === 'task' ? '/work' : `/assets${meta ? `?kind=${entity.kind}` : ''}`;
  const returnLabel = entity.kind === 'project' ? '业务专题' : entity.kind === 'task' ? '工作台' : '业务资产';
  const status = entity.status || 'draft';
  const historical=payload.active_asset===false||payload.snapshot_readonly===true;
  return <div className="page asset-detail-page">
    <nav className="asset-detail-breadcrumb"><Link to={returnPath}>{returnLabel}</Link><ChevronRight size={12} /><span>{displayKind}</span><ChevronRight size={12} /><span>{entity.title}</span></nav>
    <header className={`asset-detail-banner asset-color-${meta?.color || 'blue'}`}><div className="asset-detail-topline"><div><span className="asset-detail-kind"><Icon size={17} />{displayKind}</span><span className={`asset-source asset-source-${entity.source_kind}`}>{sourceLabel(entity.source_kind)}</span><span className={`asset-state asset-state-${status}`}>{readableStatus(status)}</span>{legalStatus && <span className={`asset-state ${/废止|尚未|失效|待核/.test(legalStatus) ? 'asset-state-returned' : ''}`}>依据时效登记 · {legalStatus}</span>}</div><span className="asset-code">{entity.id}</span></div><h1>{entity.title}</h1>{(assetTopic(entity) || assetScenario(entity)) && <div className="asset-detail-topic asset-business-tags">{assetTopic(entity) && <span className="asset-topic-label">{assetTopic(entity)}</span>}{assetScenario(entity) && <span className="asset-scenario-label">{assetScenario(entity)}</span>}</div>}{entity.summary && <p className="asset-detail-summary">{entity.summary}</p>}<div className="asset-detail-meta"><span><Layers size={13} />{entity.domain || '未分类'}</span><span><FileClock size={13} />当前版本 V{entity.version || 1}</span><span><Clock3 size={13} />更新于 {dateLabel(entity.updated_at)}</span>{entity.org_id && <Link className="asset-detail-inline-link" to={assetRoute(entity.org_id)}><Building2 size={12} />查看所属单位</Link>}</div>
      <div className="asset-detail-actions">{!historical&&<>{['case', 'project'].includes(entity.kind) && <Link className="btn btn-primary" to={`/experience?${entity.kind}=${encodeURIComponent(entity.id)}`}><BookOpen size={14} />整理这项工作经验</Link>}{payload.authority_spec?<Link className="btn btn-ghost" to={'/authority/'+encodeURIComponent(entity.id)}><Pencil size={14}/>编排权责内容</Link>:<button className="btn btn-ghost" onClick={() => setEditing(true)}><Pencil size={14} />编辑内容</button>}{meta && <button className="btn btn-ai" onClick={() => setPlacement(true)}><Sparkles size={14} />AI 关联定位</button>}{['draft', 'returned'].includes(status) && <button className="btn btn-primary" onClick={() => setAction('submit')}><Send size={14} />提交审核</button>}{status === 'review' && <><button className="btn btn-primary" onClick={() => setAction('approve')}><Check size={14} />审核通过</button><button className="btn btn-ghost" onClick={() => setAction('return')}><RotateCcw size={14} />退回修改</button></>}{status === 'approved' && <button className="btn btn-primary" onClick={() => setAction('publish')}><CheckCircle2 size={14} />发布资产</button>}{!['draft', 'returned'].includes(status) && <button className="btn btn-ghost" onClick={() => setAction('revise')}><GitBranch size={14} />发起修订</button>}{!['archived', 'draft', 'returned'].includes(status) && <button className="btn btn-ghost" onClick={() => setAction('archive')}><Archive size={14} />归档</button>}</>}<Link className="btn btn-ghost" to={`/graph?node=${encodeURIComponent(entity.id)}&mode=focus`}><Network size={14} />关联图谱</Link>{["data","rule","algorithm","knowledge"].includes(entity.kind)&&<Link className="btn btn-ghost" to={`/impact?source=${encodeURIComponent(entity.id)}`}><GitCompareArrows size={14}/>变更复核</Link>}</div>
    </header>
    {historical&&<div className="asset-historical-banner"><strong>历史追溯条目</strong> · {payload.snapshot_readonly?`当前阅读收录时的 V${entity.version}，最新版本为 V${payload.current_version}。`:payload.record_role==='procedure_reference'?'本条为办理步骤参考，已从当前规则目录移出。':'本条已整合，原编号、内容与引用予以保留。'}{payload.snapshot_readonly&&<Link to={assetRoute(entity.id)}>查看最新版本<ArrowUpRight size={13}/></Link>}{payload.merged_into&&<Link to={assetRoute(payload.merged_into)+'#'+encodeURIComponent(payload.merged_section||'')}>阅读整合后的完整成果<ArrowUpRight size={13}/></Link>}</div>}
    {isDemo && <div className="asset-demo-notice"><Info size={15} /><div>{plainText(payload.demo_notice) || '本条为演示素材，用于体验业务分析与资产治理流程；其中的单位、人物、事项与结论均不构成真实业务事实。'}</div></div>}
    <nav className="asset-detail-tabs" aria-label="详情页签"><button className={tab === 'detail' ? 'is-active' : ''} onClick={() => setTab('detail')}><FileText size={15} />业务详情</button><button className={tab === 'relations' ? 'is-active' : ''} onClick={() => setTab('relations')}><Network size={15} />关联与依据<small>{relatedCount}</small></button><button className={tab === 'history' ? 'is-active' : ''} onClick={() => setTab('history')}><FileClock size={15} />修订历史</button></nav>
    {tab === 'detail' && <div className="asset-detail-layout"><main className="asset-detail-main">{entity.kind === 'data' ? <DataContent entity={entity} /> : entity.kind === 'rule' ? <RuleContent entity={entity} /> : entity.kind === 'algorithm' ? <AlgorithmContent entity={entity} /> : entity.kind === 'knowledge' ? <KnowledgeContent entity={entity} /> : <BusinessContent entity={entity} />}</main><aside className="asset-detail-aside"><Sources entity={entity} /><Section title="管理信息" icon={ShieldCheck}><InfoGrid single items={[[ '资产编码', entity.id ], [ '来源性质', sourceLabel(entity.source_kind) ], ...(entity.kind === 'rule' ? [[ '资产来源', payload.asset_source || '待标注' ] as [string, any]] : []), [ '当前状态', readableStatus(entity.status) ], [ '登记时间', dateLabel(entity.created_at) ], [ '最近更新', dateLabel(entity.updated_at) ]]} /></Section><Section title="关联概览" icon={Network}><InfoGrid items={[[ '关联到本项', `${entity.relation_counts?.incoming ?? entity.relations?.incoming?.length ?? 0} 项` ], [ '本项关联至', `${entity.relation_counts?.outgoing ?? entity.relations?.outgoing?.length ?? 0} 项` ]]} /><button className="asset-material-cta" style={{ border: 0, width: '100%', cursor: 'pointer', textAlign: 'left' }} onClick={() => setTab('relations')}><span>查看关联对象与关系状态</span><ArrowRight size={14} /></button></Section>{payload.review_note && entity.kind !== 'rule' && <Section title="核验备注" icon={FileCheck2}><RichText value={payload.review_note} /></Section>}</aside></div>}
    {tab === 'relations' && <RelationsPanel entity={entity} />}
    {tab === 'history' && <VersionHistory id={entity.id} refresh={refresh} />}
    {placement && <AssetPlacement entity={entity} onClose={() => setPlacement(false)} onSaved={saved} />}
    {editing && <EditModal entity={entity} onClose={() => setEditing(false)} onSaved={saved} />}
    {action && <ActionModal entity={entity} action={action} onClose={() => setAction(null)} onSaved={saved} />}
  </div>;
}

export default AssetDetail;
