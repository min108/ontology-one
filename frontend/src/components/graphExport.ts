import type {Edge, Node} from '@xyflow/react';

export type GraphImageScope = string | {
  label?: string;
  description?: string;
  truncated?: boolean;
  edgeTruncated?: boolean;
  totalNodes?: number;
  totalEdges?: number;
};

export type GraphImageExportOptions = {
  nodes: Node[];
  edges: Edge[];
  title: string;
  scope?: GraphImageScope;
  format?: 'png' | 'svg';
};

export type GraphImageExportResult = {
  filename: string;
  width: number;
  height: number;
  nodeCount: number;
  edgeCount: number;
  scaled: boolean;
  omittedEdgeCount: number;
  omittedLabelCount: number;
  blob: Blob;
};

const KIND: Record<string, {label: string; color: string}> = {
  data: {label: '数据资产', color: '#41677f'},
  rule: {label: '规则资产', color: '#aa8140'},
  skill: {label: '技能资产', color: '#8070aa'},
  knowledge: {label: '知识资产', color: '#508579'},
  material: {label: '原始材料', color: '#8593a4'},
  dataset: {label: '单位数据表', color: '#64879c'},
  organization: {label: '组织单位', color: '#8b8293'},
  project: {label: '业务专题', color: '#925869'},
  task: {label: '工作任务', color: '#75899e'},
  case: {label: '业务案例', color: '#948572'},
  issue: {label: '核对事项', color: '#ad7866'},
  collection: {label: '知识专栏', color: '#659681'},
  matter: {label: '业务事项', color: '#af785c'},
  evidence: {label: '证据引用', color: '#8a967b'},
  analysis: {label: '分析记录', color: '#748f9b'},
  group: {label: '业务分组', color: '#677f96'},
};
const SOURCE: Record<string, string> = {imported: '存量资产', regulation: '法规原文', demo: '演示业务', user: '用户上传'};
const STATUS: Record<string, {label: string; color: string; dash: string}> = {
  confirmed: {label: '已确认', color: '#7890a5', dash: ''},
  source_verified: {label: '来源文本一致', color: '#718dae', dash: ''},
  candidate: {label: '待核实', color: '#ac8e60', dash: '6 5'},
  demo: {label: '演示关联', color: '#759689', dash: '2 5'},
  mixed: {label: '多种状态汇总', color: '#73859c', dash: '8 3 2 3'},
  directory: {label: '目录导航', color: '#a8b5c4', dash: '2 5'},
  other: {label: '其他状态', color: '#98a1ad', dash: '8 4 2 4'},
};
const FONT = '"Noto Sans SC","Microsoft YaHei","PingFang SC",Arial,sans-serif';
const CARD_W = 240;
const CARD_H = 110;
const PAD = 54;
const MAX_CANVAS_SIDE = 8192;
const MAX_CANVAS_PIXELS = 16_000_000;
const NUMBER = new Intl.NumberFormat('zh-CN');
const text = (value: unknown) => String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, '').replace(/\s+/g, ' ').trim();
const esc = (value: unknown) => text(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
const fmt = (value: unknown) => NUMBER.format(Number.isFinite(Number(value)) ? Number(value) : 0);
const n = (value: number) => Number(value.toFixed(2));

type ExportNode = {id: string; x: number; y: number; item: any};
type Rect = {x: number; y: number; width: number; height: number};

/**
 * Export the actual loaded ReactFlow node positions, independent of viewport,
 * scrolling or DOM visibility. Produces self-contained SVG or a bounded PNG and
 * starts a browser download. Rejects invalid/empty views instead of blank images.
 */
export async function exportGraphImage({nodes, edges, title, scope, format = 'png'}: GraphImageExportOptions): Promise<GraphImageExportResult> {
  if (!Array.isArray(nodes) || !nodes.length) throw new Error('当前视图没有可导出的节点，请先加载图谱。');
  if (!Array.isArray(edges)) throw new Error('当前关系数据尚未准备完整，请重新加载图谱。');
  if (format !== 'png' && format !== 'svg') throw new Error('请选择 PNG 图片或 SVG 矢量图格式。');
  const visibleNodes = nodes.filter(node => node && !node.hidden);
  if (!visibleNodes.length) throw new Error('当前视图没有可导出的节点，请先加载图谱。');
  if (visibleNodes.some(node => !node.id || !Number.isFinite(node.position?.x) || !Number.isFinite(node.position?.y) || Math.abs(node.position.x) > 10_000_000 || Math.abs(node.position.y) > 10_000_000)) {
    throw new Error('部分节点的布局位置无效，请重新整理视图后导出。');
  }
  if (new Set(visibleNodes.map(node => node.id)).size !== visibleNodes.length) throw new Error('当前视图存在重复节点编号，请重新加载后导出。');
  // Wait for the page's Chinese font, with a bounded fallback to installed fonts.
  if (document.fonts) await Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 1800))]);
  const measureCanvas = document.createElement('canvas');
  const measure = measureCanvas.getContext('2d');
  if (!measure) throw new Error('浏览器无法生成图片，请换用支持图形绘制的浏览器。');
  const widthOf = (value: string, size: number, weight = 400) => {
    measure.font = `${weight} ${size}px ${FONT}`;
    return measure.measureText(value).width;
  };
  const ellipsis = (value: string, maxWidth: number, size: number, weight = 400) => {
    if (widthOf(value, size, weight) <= maxWidth) return value;
    const chars = Array.from(value);
    while (chars.length && widthOf(chars.join('') + '…', size, weight) > maxWidth) chars.pop();
    return chars.join('') + '…';
  };
  const wrap = (value: unknown, maxWidth: number, size: number, lines: number, weight = 400) => {
    const chars = Array.from(text(value));
    const result: string[] = [];
    let current = '';
    for (let i = 0; i < chars.length; i++) {
      const next = current + chars[i];
      if (current && widthOf(next, size, weight) > maxWidth) {
        result.push(current);
        current = chars[i];
        if (result.length === lines - 1) {
          result.push(ellipsis(chars.slice(i).join(''), maxWidth, size, weight));
          return result;
        }
      } else current = next;
    }
    if (current || !result.length) result.push(current);
    return result;
  };
  const svgText = (x: number, y: number, value: unknown, size = 12, fill = '#677c91', weight = 400, extra = '') => `<text x="${n(x)}" y="${n(y)}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${esc(value)}</text>`;
  const nodeMap = new Map<string, ExportNode>();
  visibleNodes.forEach(node => nodeMap.set(node.id, {id: node.id, x: node.position.x, y: node.position.y, item: (node.data as any)?.item || {id: node.id, label: node.id, kind: 'group'}}));
  const validEdges = edges.filter(edge => edge && !edge.hidden && nodeMap.has(edge.source) && nodeMap.has(edge.target));
  const omittedEdgeCount = edges.filter(edge => edge && !edge.hidden).length - validEdges.length;
  const minX = Math.min(...visibleNodes.map(node => node.position.x));
  const minY = Math.min(...visibleNodes.map(node => node.position.y));
  const maxX = Math.max(...visibleNodes.map(node => node.position.x + CARD_W));
  const maxY = Math.max(...visibleNodes.map(node => node.position.y + CARD_H));
  const graphW = maxX - minX;
  const graphH = maxY - minY;
  // Space outside cards also keeps backward and self-loop curves in the image.
  const margin = 106;
  const logicalWidth = Math.max(1200, Math.ceil(graphW + margin * 2));
  const contentsWidth = logicalWidth - PAD * 2;
  const titleLines = wrap(title || '业务关系图谱', contentsWidth - 54, 27, 2, 600);
  const scopeObject = scope && typeof scope === 'object' ? scope : {};
  const scopeLabel = typeof scope === 'string' ? scope : scopeObject.label || scopeObject.description || '当前已加载的关系视图';
  const scopeLines = wrap(`视图范围：${scopeLabel}`, contentsWidth, 12, 3);
  const truncated = !!scopeObject.truncated || !!scopeObject.edgeTruncated;
  const warning = [
    truncated ? '范围提示：当前视图存在数量截断，图片未包含全部对象或关系。' : '范围说明：完整绘制当前已加载布局；导出内容以本次视图的范围与筛选条件为准。',
    omittedEdgeCount ? `${fmt(omittedEdgeCount)}条关联的端点未加载，本图未绘制。` : '',
  ].filter(Boolean).join(' ');
  const warningLines = wrap(warning, contentsWidth - 26, 11, 3);
  const headingY = 67;
  const scopeY = headingY + titleLines.length * 35 + 6;
  const statsY = scopeY + scopeLines.length * 19 + 7;
  const warningY = statsY + 18;
  const warningH = warningLines.length * 17 + 17;
  const graphTop = warningY + warningH + 37;
  const translatedX = (logicalWidth - graphW) / 2 - minX;
  const translatedY = graphTop + margin - minY;
  const graphBottom = graphTop + graphH + margin * 2;
  const categories = Array.from(new Set(Array.from(nodeMap.values()).map(node => text(node.item.kind) || 'group')));
  const statuses = Array.from(new Set(validEdges.map(edge => {
    const status = text((edge.data as any)?.item?.status || 'confirmed');
    return STATUS[status] ? status : 'other';
  })));
  const legendEntries = categories.map(kind => ({type: 'kind', id: kind, ...(KIND[kind] || KIND.group)}))
    .concat(statuses.map(status => ({type: 'status', id: status, ...STATUS[status]})) as any);
  const legendPositions: {entry: any; x: number; y: number}[] = [];
  let legendX = PAD;
  let legendY = graphBottom + 49;
  for (const entry of legendEntries) {
    const needed = widthOf(entry.label, 11) + (entry.type === 'status' ? 65 : 39);
    if (legendX + needed > logicalWidth - PAD) {legendX = PAD; legendY += 25;}
    legendPositions.push({entry, x: legendX, y: legendY});
    legendX += needed;
  }
  const logicalHeight = Math.ceil(legendY + 89);
  const scale = Math.min(2, MAX_CANVAS_SIDE / logicalWidth, MAX_CANVAS_SIDE / logicalHeight, Math.sqrt(MAX_CANVAS_PIXELS / (logicalWidth * logicalHeight)));
  const pngWidth = Math.max(1, Math.floor(logicalWidth * scale));
  const pngHeight = Math.max(1, Math.floor(logicalHeight * scale));
  const rects = Array.from(nodeMap.values()).map(node => ({x: node.x, y: node.y, width: CARD_W, height: CARD_H}));
  const occupied: Rect[] = [];
  const overlap = (a: Rect, b: Rect) => a.x < b.x + b.width + 4 && a.x + a.width + 4 > b.x && a.y < b.y + b.height + 4 && a.y + a.height + 4 > b.y;
  const edgePaths: string[] = [];
  const edgeLabels: string[] = [];
  let omittedLabelCount = 0;
  for (const edge of validEdges) {
    const source = nodeMap.get(edge.source)!;
    const target = nodeMap.get(edge.target)!;
    const item = (edge.data as any)?.item || {};
    const statusName = text(item.status || 'confirmed');
    const status = STATUS[statusName] || STATUS.other;
    const marker = STATUS[statusName] ? statusName : 'other';
    const baseLabel = text(item.label || (typeof edge.label === 'string' ? edge.label : '') || '业务关联');
    const aggregateCount = Number(item.count) > 1 && !/\d[\d,，]*\s*条/.test(baseLabel) ? ` · ${fmt(item.count)}条` : '';
    const label = baseLabel + aggregateCount;
    const sx = edge.sourceHandle === 'left' ? source.x - 2 : source.x + CARD_W + 2;
    const sy = source.y + CARD_H / 2;
    const tx = edge.targetHandle === 'right' ? target.x + CARD_W + 3 : target.x - 3;
    const ty = target.y + CARD_H / 2;
    const control = Math.min(88, Math.max(45, Math.abs(tx - sx) * .32));
    const points = edge.source === edge.target
      ? [{x: sx, y: sy}, {x: sx + 82, y: sy - 94}, {x: source.x + CARD_W / 2, y: source.y - 94}, {x: source.x + CARD_W / 2, y: source.y - 3}]
      : [{x: sx, y: sy}, {x: sx + control, y: sy}, {x: tx - control, y: ty}, {x: tx, y: ty}];
    let path = `M ${n(points[0].x)} ${n(points[0].y)} C ${n(points[1].x)} ${n(points[1].y)}, ${n(points[2].x)} ${n(points[2].y)}, ${n(points[3].x)} ${n(points[3].y)}`;
    let atlasPoints: {x:number;y:number}[] | null = null;
    if (edge.type === 'atlas') {
      const metadata = edge.data as any;
      const same = metadata.sourceColumn === metadata.targetColumn;
      const backwards = metadata.sourceColumn > metadata.targetColumn;
      const long = Math.abs(metadata.sourceColumn - metadata.targetColumn) > 1;
      const gx = sx + (same ? 24 : backwards ? -25 : 25);
      const hx = tx + (same ? 24 : backwards ? 25 : -25);
      const corridor = Number(metadata.corridor) || 138;
      atlasPoints = same ? [{x:sx,y:sy},{x:gx,y:sy},{x:gx,y:ty},{x:tx,y:ty}]
        : long ? [{x:sx,y:sy},{x:gx,y:sy},{x:gx,y:corridor},{x:hx,y:corridor},{x:hx,y:ty},{x:tx,y:ty}]
        : [{x:sx,y:sy},{x:(sx+tx)/2,y:sy},{x:(sx+tx)/2,y:ty},{x:tx,y:ty}];
      path = atlasPoints.map((point,index)=>`${index ? 'L' : 'M'} ${n(point.x)} ${n(point.y)}`).join(' ');
    }
    const strokeWidth = item.aggregate ? Math.min(2.1, 1 + Math.log10(Number(item.count) || 1) * .18) : 1.25;
    edgePaths.push(`<path d="${path}" fill="none" stroke="${status.color}" stroke-width="${n(strokeWidth)}" stroke-opacity="0.68" stroke-linejoin="round" ${status.dash ? `stroke-dasharray="${status.dash}"` : ''} ${item.directory ? '' : `marker-end="url(#arrow-${marker})"`}><title>${esc(source.item.label)} → ${esc(target.item.label)}：${esc(label)} · ${esc(status.label)}</title></path>`);
    const shortLabel = ellipsis(label, 138, 10);
    const labelW = widthOf(shortLabel, 10) + 14;
    let place: Rect | undefined;
    const centers = atlasPoints ? atlasPoints.slice(1).map((point,index)=>({x:(point.x+atlasPoints![index].x)/2,y:(point.y+atlasPoints![index].y)/2})) : [.5,.36,.64,.24,.76].map(t=>{const u=1-t;return {x:u*u*u*points[0].x+3*u*u*t*points[1].x+3*u*t*t*points[2].x+t*t*t*points[3].x,y:u*u*u*points[0].y+3*u*u*t*points[1].y+3*u*t*t*points[2].y+t*t*t*points[3].y}});
    for (const {x,y} of centers) {
      const box = {x: x - labelW / 2, y: y - 10, width: labelW, height: 19};
      if (!rects.some(rect => overlap(box, rect)) && !occupied.some(rect => overlap(box, rect))) {place = box; break;}
    }
    if (place) {
      occupied.push(place);
      edgeLabels.push(`<g><rect x="${n(place.x)}" y="${n(place.y)}" width="${n(place.width)}" height="19" rx="5" fill="#f7f9fc" fill-opacity="0.96" stroke="#e1e7ed" stroke-width="0.6"/>${svgText(place.x + place.width / 2, place.y + 13, shortLabel, 10, '#748699', 400, 'text-anchor="middle"')}<title>${esc(label)}</title></g>`);
    } else omittedLabelCount++;
  }
  const cardShapes = Array.from(nodeMap.values()).map(node => {
    const item = node.item;
    const kind = KIND[text(item.kind)] || KIND.group;
    const aggregate = !!item.aggregate;
    const labelLines = wrap(item.label || node.id, CARD_W - 34, aggregate ? 15 : 14, 2, 600);
    const source = SOURCE[text(item.source_kind)] || '来源待说明';
    const titleY = labelLines.length > 1 ? 53 : 59;
    const label = labelLines.map((line, i) => svgText(17, titleY + i * 18, line, aggregate ? 15 : 14, '#24374b', 600)).join('');
    const bottom = aggregate
      ? `${svgText(17, 96, fmt(item.directory_count ?? item.count), 21, kind.color, 600)}${svgText(24 + widthOf(fmt(item.directory_count ?? item.count), 21, 600), 95, item.directory_count !== undefined ? '家（个）机构' : '个对象', 10, '#8997a7')}${svgText(CARD_W - 17, 95, item.directory_count !== undefined ? `${fmt(item.count)}项归口对象` : '聚合分组', 9, '#8997a7', 400, 'text-anchor="end"')}`
      : `${svgText(17, 96, ellipsis(node.id, 139, 9), 9, '#8e9bad')}${svgText(CARD_W - 17, 96, source, 9, item.source_kind === 'demo' ? '#9d835b' : '#8e9bad', 400, 'text-anchor="end"')}`;
    return `<g transform="translate(${n(node.x)} ${n(node.y)})"><title>${esc(item.label || node.id)} · ${esc(node.id)}${aggregate ? ` · ${esc(fmt(item.count))}个对象` : ` · ${esc(source)}`}</title><rect x="1" y="3" width="${CARD_W}" height="${CARD_H}" rx="13" fill="#bac9d5" opacity="0.14"/><rect width="${CARD_W}" height="${CARD_H}" rx="13" fill="#ffffff" stroke="${item.root ? kind.color : '#d7e1ea'}" stroke-width="${item.root ? 1.8 : 1}"/><rect x="0" y="18" width="3" height="28" rx="1.5" fill="${kind.color}"/><rect x="17" y="15" width="17" height="17" rx="5" fill="${kind.color}" fill-opacity="0.12"/><circle cx="25.5" cy="23.5" r="3.2" fill="${kind.color}"/>${svgText(42, 27, aggregate ? '聚合分组' : kind.label, 10, kind.color, 500)}${item.root ? svgText(CARD_W - 17, 27, '当前中心', 9, kind.color, 500, 'text-anchor="end"') : ''}${label}${bottom}</g>`;
  }).join('');
  const markers = Object.entries(STATUS).map(([key, value]) => `<marker id="arrow-${key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 1 1 L 9 5 L 1 9 Z" fill="${value.color}"/></marker>`).join('');
  const legends = legendPositions.map(({entry, x, y}) => entry.type === 'kind'
    ? `<rect x="${n(x)}" y="${n(y - 9)}" width="9" height="9" rx="2.5" fill="${entry.color}"/>${svgText(x + 16, y, entry.label, 11, '#7a8b9d')}`
    : `<line x1="${n(x)}" y1="${n(y - 4)}" x2="${n(x + 30)}" y2="${n(y - 4)}" stroke="${entry.color}" stroke-width="1.7" ${entry.dash ? `stroke-dasharray="${entry.dash}"` : ''}/>${svgText(x + 38, y, entry.label, 11, '#7a8b9d')}`
  ).join('');
  const dateLabel = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date());
  const totalInfo = Number.isFinite(scopeObject.totalNodes) ? ` · 全库 ${fmt(scopeObject.totalNodes)} 个对象${Number.isFinite(scopeObject.totalEdges) ? ` / ${fmt(scopeObject.totalEdges)} 条关系` : ''}` : '';
  const footerNote = omittedLabelCount ? `密集关联保留方向与状态，${fmt(omittedLabelCount)}条连线名称未展开。演示业务与法规原文按来源分别标识。` : '演示业务与法规原文按来源分别标识；关系的确认状态见图例。';
  const drawing = `<defs>${markers}</defs><rect width="100%" height="100%" fill="#f4f7fb"/><rect x="22" y="22" width="${logicalWidth - 44}" height="${logicalHeight - 44}" rx="20" fill="#fbfcfe" stroke="#e2e8f0"/><rect x="${PAD}" y="45" width="4" height="31" rx="2" fill="#a51c30"/>${titleLines.map((line, i) => svgText(PAD + 20, headingY + i * 35, line, 27, '#20364e', 600)).join('')}${scopeLines.map((line, i) => svgText(PAD, scopeY + i * 19, line, 12, '#74879b')).join('')}${svgText(PAD, statsY, `${fmt(visibleNodes.length)} 个图中节点 · ${fmt(validEdges.length)} 条图中关系${totalInfo}`, 11, '#8c9aae')}<rect x="${PAD}" y="${warningY}" width="${contentsWidth}" height="${warningH}" rx="7" fill="${truncated ? '#f9f1e3' : '#eef3f8'}"/>${warningLines.map((line, i) => svgText(PAD + 13, warningY + 19 + i * 17, line, 11, truncated ? '#a08454' : '#7e90a3')).join('')}<g transform="translate(${n(translatedX)} ${n(translatedY)})">${edgePaths.join('')}${edgeLabels.join('')}${cardShapes}</g><line x1="${PAD}" y1="${graphBottom + 24}" x2="${logicalWidth - PAD}" y2="${graphBottom + 24}" stroke="#e2e8ef"/>${legends}${svgText(PAD, legendY + 33, footerNote, 10, '#98a5b4')}${svgText(PAD, legendY + 56, 'Ontology One · 业务资产与业务事项关系视图', 10, '#a0abba')}${svgText(logicalWidth - PAD, legendY + 56, `导出于 ${dateLabel}（北京时间）`, 10, '#a0abba', 400, 'text-anchor="end"')}`;
  const documentSvg = (width: number, height: number) => `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${logicalWidth} ${logicalHeight}" role="img" aria-label="${esc(title || '业务关系图谱')}" font-family="${esc(FONT)}"><title>${esc(title || '业务关系图谱')}</title><desc>${esc(scopeLabel)}。${esc(warning)}</desc>${drawing}</svg>`;
  let blob: Blob;
  let width = logicalWidth;
  let height = logicalHeight;
  if (format === 'svg') {
    blob = new Blob([documentSvg(logicalWidth, logicalHeight)], {type: 'image/svg+xml;charset=utf-8'});
  } else {
    width = pngWidth;
    height = pngHeight;
    const svgBlob = new Blob([documentSvg(pngWidth, pngHeight)], {type: 'image/svg+xml;charset=utf-8'});
    const svgUrl = URL.createObjectURL(svgBlob);
    const image = new Image();
    try {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {image.onload = null; image.onerror = null; reject(new Error('图片生成超时，请缩小图谱范围或选择 SVG 格式。'));}, 15000);
        image.onload = () => {clearTimeout(timer); resolve();};
        image.onerror = () => {clearTimeout(timer); reject(new Error('图谱图片生成失败，请尝试导出 SVG 矢量图。'));};
        image.src = svgUrl;
      });
      const canvas = document.createElement('canvas');
      canvas.width = pngWidth;
      canvas.height = pngHeight;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('浏览器无法生成此尺寸图片，请选择 SVG 矢量图。');
      context.fillStyle = '#f4f7fb';
      context.fillRect(0, 0, pngWidth, pngHeight);
      context.drawImage(image, 0, 0, pngWidth, pngHeight);
      blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('PNG 图片生成失败，请选择 SVG 矢量图。')), 'image/png'));
      canvas.width = 1;
      canvas.height = 1;
    } finally {URL.revokeObjectURL(svgUrl);}
  }
  const safeTitle = text(title || '业务关系图谱').replace(/[<>:"/\\|?*]/g, '-').replace(/[. ]+$/g, '').slice(0, 70) || '业务关系图谱';
  const localDate = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  const filename = `Ontology One-${safeTitle}-${localDate}.${format}`;
  const downloadUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = downloadUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 60_000);
  return {filename, width, height, nodeCount: visibleNodes.length, edgeCount: validEdges.length, scaled: format === 'png' && scale < 1.999, omittedEdgeCount, omittedLabelCount, blob};
}
