import {Link} from 'react-router-dom';
import {Database, ArrowUpRight, RefreshCw} from 'lucide-react';
import {useApi} from '../api';

const labels: Record<string,string> = {
  draft:'草稿',in_review:'待审核',published:'已发布',rejected:'已退回',stale:'待重新确认',
};
const invalidReasons: Record<string,string> = {
  schema_drift:'源结构已变化',source_changed:'数据源配置或启停状态已变化',
  target_changed:'本体标准已更新',target_unpublished:'本体标准已取消发布',target_inactive:'本体标准已失效',
};

/** Metadata only. External records are fetched exclusively in the query workbench. */
export function SourceLineage({assetId}:{assetId:string}) {
  const q=useApi<any>(`/sources/lineage/${encodeURIComponent(assetId)}`);
  const items=q.data?.items||[];
  return <section className="asset-section">
    <div className="asset-section-heading"><div><h2><Database size={16}/>外部数据来源</h2>
      <p>查看源字段与本体标准的映射、版本和归属。这里仅展示元数据，业务记录保留在源系统。</p></div></div>
    <div className="asset-section-content">
      {q.loading?<p className="asset-empty-value">正在读取来源映射…</p>:q.error?
        <div role="alert"><p>{q.error}</p><button className="btn" onClick={q.reload}><RefreshCw size={14}/>重新加载</button></div>:
        items.length?<div className="asset-stack-list">{items.map((item:any)=><div className="asset-source-item" key={item.id||item.mapping_id}>
          <div><strong>{item.title||item.mapping_title||'字段映射'}</strong>
            <span className="muted"> · {labels[item.status]||item.status} · v{item.version||item.mapping_version}</span></div>
          <p className="asset-prose">{item.source_title||item.source_name||item.source_id} / {item.resource_name||item.resource_id}</p>
          {item.invalid_reason&&<p role="status">{invalidReasons[item.invalid_reason]||'映射需重新确认'}</p>}
          <Link className="asset-material-cta" to={`/data-sources/${encodeURIComponent(item.source_id)}?mapping=${encodeURIComponent(item.id||item.mapping_id)}`}>
            <span>查看映射与源端查询</span><ArrowUpRight size={15}/></Link>
        </div>)}</div>:<p className="asset-empty-value">尚无可查看的外部来源映射。可在数据源管理中发现表结构，并将源字段映射到本标准。</p>}
    </div>
  </section>;
}
