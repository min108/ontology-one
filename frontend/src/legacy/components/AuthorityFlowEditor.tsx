import {AccessButton} from './AccessButton';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Handle, Position, MarkerType, applyNodeChanges, type Connection, type Node, type NodeChange, type ReactFlowInstance } from '@xyflow/react';
import { ArrowRight, Building2, Circle, CircleCheck, Diamond, Grip, LayoutGrid, Maximize2, Minus, Plus, Redo2, Route, ShieldCheck, Trash2, Undo2 } from 'lucide-react';
import '@xyflow/react/dist/style.css';
import './authority-flow.css';

type FlowNode = { id:string; kind:string; title:string; org_id?:string; role?:string; person_id?:string; responsibility?:string; authority?:string; control?:string; evidence?:string; time_limit?:string; x?:number; y?:number; [key:string]:any };
type FlowEdge = { id:string; source:string; target:string; label?:string; [key:string]:any };
type Spec = { nodes?:FlowNode[]; edges?:FlowEdge[]; [key:string]:any };
type Props = { spec:Spec; onChange:(spec:any)=>void; organizations:{id:string;title:string}[]; people:any[]; readOnly?:boolean };
const NODE_KINDS = [
  {kind:'start',label:'开始',title:'事项提出',icon:Circle,color:'#598978'},
  {kind:'step',label:'办理环节',title:'新增办理环节',icon:ShieldCheck,color:'#557993'},
  {kind:'decision',label:'条件判断',title:'是否符合办理条件',icon:Diamond,color:'#ab834d'},
  {kind:'unit',label:'机构职责',title:'新增机构职责',icon:Building2,color:'#87749c'},
  {kind:'end',label:'结束',title:'归档留痕',icon:CircleCheck,color:'#a16b78'},
];
const metadata = (kind:string) => NODE_KINDS.find(x=>x.kind===kind)||NODE_KINDS[1];
const clone = <T,>(value:T):T => JSON.parse(JSON.stringify(value));
const keyOf = (value:any) => JSON.stringify(value);
const newId = (prefix:string) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`;

function AuthorityNode({data,selected}:{data:any;selected?:boolean}){
  const n = data.item as FlowNode, meta = metadata(n.kind), Icon = meta.icon;
  return <div className={`authority-flow-node authority-flow-${n.kind} ${selected?'is-selected':''}`} style={{'--flow-color':meta.color} as React.CSSProperties}>
    {n.kind!=='start'&&<><Handle type="target" position={Position.Left} id="in" aria-label="连入此环节" isConnectable={!data.readOnly}/><Handle className="authority-flow-routing-handle" type="target" position={Position.Top} id="top-in" isConnectable={false}/><Handle className="authority-flow-routing-handle" type="target" position={Position.Right} id="right-in" isConnectable={false}/><Handle className="authority-flow-routing-handle" type="target" position={Position.Bottom} id="bottom-in" isConnectable={false}/></>}
    <div className="authority-flow-node-type"><Icon size={14}/><span>{meta.label}</span><small>{String(data.index+1).padStart(2,'0')}</small></div>
    <b title={n.title}>{n.title||'未填写环节名称'}</b>
    <div className="authority-flow-node-owner" title={data.orgTitle||n.role||'待明确办理主体'}>{data.orgTitle||n.role||'待明确办理主体'}</div>
    {n.control&&<div className="authority-flow-node-control"><ShieldCheck size={11}/><span>{n.control}</span></div>}
    {n.kind!=='end'&&<><Handle type="source" position={Position.Right} id="out" aria-label="从此环节连出" isConnectable={!data.readOnly}/><Handle className="authority-flow-routing-handle" type="source" position={Position.Bottom} id="bottom-out" isConnectable={false}/><Handle className="authority-flow-routing-handle" type="source" position={Position.Left} id="left-out" isConnectable={false}/><Handle className="authority-flow-routing-handle" type="source" position={Position.Top} id="top-out" isConnectable={false}/></>}
  </div>;
}
const nodeTypes = {authority:AuthorityNode};

/** A cycle-safe, left-to-right layout. Rework loops remain actual directed links. */
export function arrangeAuthorityNodes(nodes:FlowNode[], edges:FlowEdge[]):FlowNode[]{
  const ids=new Set(nodes.map(n=>n.id)), degrees=new Map(nodes.map(n=>[n.id,0])), outgoing=new Map(nodes.map(n=>[n.id,[] as string[]]));
  edges.forEach(e=>{if(ids.has(e.source)&&ids.has(e.target)&&e.source!==e.target){degrees.set(e.target,(degrees.get(e.target)||0)+1);outgoing.get(e.source)!.push(e.target)}});
  const queue=nodes.filter(n=>degrees.get(n.id)===0).map(n=>n.id), levels=new Map<string,number>();
  queue.forEach(id=>levels.set(id,0));
  for(let i=0;i<queue.length;i++){
    const id=queue[i]; for(const next of outgoing.get(id)||[]){levels.set(next,Math.max(levels.get(next)||0,(levels.get(id)||0)+1));degrees.set(next,(degrees.get(next)||0)-1);if(degrees.get(next)===0)queue.push(next)}
  }
  // Remaining nodes belong to a cycle (or follow one); use bounded breadth traversal.
  const visited=new Set(queue);let base=Math.max(-1,...levels.values())+1;
  for(const n of nodes){if(visited.has(n.id))continue;const pending=[n.id];visited.add(n.id);levels.set(n.id,base);
    for(let i=0;i<pending.length;i++){const id=pending[i];for(const next of outgoing.get(id)||[]){if(!visited.has(next)){visited.add(next);levels.set(next,(levels.get(id)||base)+1);pending.push(next)}}}
    base=Math.max(base,...levels.values())+1;
  }
  const columns=new Map<number,FlowNode[]>();nodes.forEach(n=>{const level=levels.get(n.id)||0;columns.set(level,[...(columns.get(level)||[]),n])});
  const height=Math.max(1,...[...columns.values()].map(v=>v.length));
  return nodes.map(n=>{const col=levels.get(n.id)||0,group=columns.get(col)!;return {...n,x:48+col*292,y:48+(height-group.length)*84+group.indexOf(n)*168}});
}

export function AuthorityFlowEditor({spec,onChange,organizations,people,readOnly=false}:Props){
  const current=useRef(spec), lastEmitted=useRef(keyOf(spec)), undo=useRef<Spec[]>([]), redo=useRef<Spec[]>([]), grouped=useRef({key:'',time:0});
  const [historyVersion,setHistoryVersion]=useState(0),[selectedId,setSelectedId]=useState(''),[edgeId,setEdgeId]=useState(''),[notice,setNotice]=useState(''),[connectTo,setConnectTo]=useState(''),[connectionLabel,setConnectionLabel]=useState('');
  const canvas=useRef<HTMLDivElement>(null), flow=useRef<ReactFlowInstance|null>(null), initialFit=useRef(false), [ready,setReady]=useState(false);
  const nodes=spec.nodes||[], edges=spec.edges||[];
  const byOrg=useMemo(()=>new Map(organizations.map(o=>[o.id,o.title])),[organizations]);
  const selected=nodes.find(n=>n.id===selectedId), selectedEdge=edges.find(e=>e.id===edgeId);
  const viewNodes=useMemo(()=>nodes.map((n,index)=>({id:n.id,type:'authority',position:{x:Number.isFinite(n.x)?n.x!:48+(index%4)*292,y:Number.isFinite(n.y)?n.y!:48+Math.floor(index/4)*168},selected:n.id===selectedId,data:{item:n,index,orgTitle:byOrg.get(n.org_id||''),readOnly}})),[keyOf(nodes),selectedId,byOrg,readOnly]);
  const [renderNodes,setRenderNodes]=useState<Node[]>(viewNodes);
  useEffect(()=>setRenderNodes(viewNodes),[viewNodes]);
  useEffect(()=>{
    const incoming=keyOf(spec);
    if(incoming!==lastEmitted.current&&keyOf({nodes:current.current.nodes,edges:current.current.edges})!==keyOf({nodes:spec.nodes,edges:spec.edges})){undo.current=[...undo.current.slice(-39),clone(current.current)];redo.current=[];grouped.current={key:'',time:0};setHistoryVersion(v=>v+1)}
    current.current=spec;lastEmitted.current=incoming;
  },[spec]);
  useEffect(()=>{if(selectedId&&!nodes.some(n=>n.id===selectedId))setSelectedId('');if(edgeId&&!edges.some(e=>e.id===edgeId))setEdgeId('')},[keyOf(nodes.map(n=>n.id)),keyOf(edges.map(e=>e.id))]);
  useEffect(()=>{
    if(!ready||!nodes.length||initialFit.current)return;
    initialFit.current=true;
    const handle=requestAnimationFrame(()=>{flow.current?.fitView({padding:.18,maxZoom:1,duration:0})});
    return()=>{cancelAnimationFrame(handle);initialFit.current=false};
  },[ready,!!nodes.length]);
  const commit=(next:Spec,group='')=>{
    if(readOnly||keyOf(current.current)===keyOf(next))return;
    const now=Date.now();if(!group||group!==grouped.current.key||now-grouped.current.time>900)undo.current=[...undo.current.slice(-39),clone(current.current)];
    grouped.current={key:group,time:now};redo.current=[];current.current=next;lastEmitted.current=keyOf(next);onChange(next);setHistoryVersion(v=>v+1);
  };
  const undoChange=(direction:'undo'|'redo')=>{
    if(readOnly)return;const from=direction==='undo'?undo:redo,to=direction==='undo'?redo:undo,next=from.current.pop();if(!next)return;
    to.current.push(clone(current.current));const restored={...current.current,nodes:next.nodes,edges:next.edges};current.current=restored;lastEmitted.current=keyOf(restored);grouped.current={key:'',time:0};onChange(restored);setHistoryVersion(v=>v+1);setNotice(direction==='undo'?'已撤销上一步流程编辑':'已重做流程编辑');
  };
  const updateNode=(key:string,value:string)=>{
    if(!selected)return;const patch:any={[key]:value};if(key==='person_id'&&value&&!selected.org_id)patch.org_id=people.find(p=>p.id===value)?.org_id||'';if(key==='org_id'&&selected.person_id&&!people.some(p=>p.id===selected.person_id&&p.org_id===value))patch.person_id='';
    commit({...current.current,nodes:(current.current.nodes||[]).map(n=>n.id===selected.id?{...n,...patch}:n)},selected.id+':'+key);
  };
  const addNode=(kind:string,position?:{x:number;y:number})=>{
    if(readOnly)return;const meta=metadata(kind), rect=canvas.current?.getBoundingClientRect();
    const point=position||(rect&&flow.current?flow.current.screenToFlowPosition({x:rect.left+rect.width/2-110,y:rect.top+rect.height/2-60}):{x:48+(nodes.length%3)*292,y:48+Math.floor(nodes.length/3)*168});
    const occupied=(current.current.nodes||[]).some(n=>Math.abs((n.x||0)-point.x)<70&&Math.abs((n.y||0)-point.y)<70);
    const node:FlowNode={id:newId('step'),kind,title:meta.title,x:Math.round(point.x+(occupied?40:0)),y:Math.round(point.y+(occupied?50:0)),org_id:'',role:'',person_id:'',responsibility:'',authority:'',control:'',evidence:'',time_limit:''};
    commit({...current.current,nodes:[...(current.current.nodes||[]),node]});setSelectedId(node.id);setEdgeId('');setNotice(`已添加${meta.label}，可拖动定位并在详情中补充职责。`);
  };
  const connect=(connection:Connection|{source:string;target:string;label?:string})=>{
    if(readOnly)return;const {source,target}=connection;
    if(!source||!target||source===target){setNotice('请选择两个不同的环节建立连接。');return}
    if((current.current.edges||[]).some(e=>e.source===source&&e.target===target)){setNotice('这两个环节之间已有连接，可选中连线修改条件。');return}
    const from=(current.current.nodes||[]).find(n=>n.id===source),to=(current.current.nodes||[]).find(n=>n.id===target);
    if(!from||!to||from.kind==='end'||to.kind==='start'){setNotice('结束环节不能继续连出，开始环节不能作为下游。');return}
    const edge={id:newId('link'),source,target,label:('label'in connection&&connection.label)|| (from.kind==='decision'?'请填写分支条件':'')};
    commit({...current.current,edges:[...(current.current.edges||[]),edge]});setSelectedId('');setEdgeId(edge.id);setConnectTo('');setConnectionLabel('');setNotice('已建立连接，可在关系详情中明确流转条件。');
  };
  const removeSelected=()=>{
    if(readOnly)return;if(selected){commit({...current.current,nodes:(current.current.nodes||[]).filter(n=>n.id!==selected.id),edges:(current.current.edges||[]).filter(e=>e.source!==selected.id&&e.target!==selected.id)});setNotice('已移除此环节及相连关系，可撤销恢复。');setSelectedId('')}
    else if(selectedEdge){commit({...current.current,edges:(current.current.edges||[]).filter(e=>e.id!==selectedEdge.id)});setNotice('已移除此连接，可撤销恢复。');setEdgeId('')}
  };
  const arrange=()=>{commit({...current.current,nodes:arrangeAuthorityNodes(current.current.nodes||[],current.current.edges||[])});requestAnimationFrame(()=>requestAnimationFrame(()=>flow.current?.fitView({padding:.18,maxZoom:1,duration:200})));setNotice('已按流转关系整理布局；退回、复核等回路保留原方向。')};
  const edgeHandles=(edge:FlowEdge)=>{
    const source=nodes.find(n=>n.id===edge.source),target=nodes.find(n=>n.id===edge.target);if(!source||!target)return {sourceHandle:'out',targetHandle:'in'};
    const dx=(target.x||0)-(source.x||0),dy=(target.y||0)-(source.y||0);
    if(dy>140)return {sourceHandle:'bottom-out',targetHandle:'top-in'};
    if(dy < -140&&Math.abs(dx)<180)return {sourceHandle:'out',targetHandle:'right-in'};
    if(dy < -140)return {sourceHandle:'top-out',targetHandle:'bottom-in'};
    return dx<0?{sourceHandle:'left-out',targetHandle:'right-in'}:{sourceHandle:'out',targetHandle:'in'};
  };
  const rfEdges=edges.map(e=>({id:e.id,source:e.source,target:e.target,...edgeHandles(e),type:'smoothstep',label:e.label||'',selected:e.id===edgeId,markerEnd:{type:MarkerType.ArrowClosed,color:e.id===edgeId?'#a51c30':'#8b9dad'},style:{stroke:e.id===edgeId?'#a51c30':'#8b9dad',strokeWidth:e.id===edgeId?2:1.5},labelStyle:{fontSize:11,fill:'#586b80'},labelBgStyle:{fill:'#f8fafc',fillOpacity:.96},labelBgPadding:[7,4] as [number,number],labelBgBorderRadius:5,pathOptions:{borderRadius:16}}));
  const changes=(items:NodeChange[])=>{
    setRenderNodes(old=>applyNodeChanges(items.filter(x=>x.type!=='remove'&&(!readOnly||x.type!=='position')),old));
    const picked=items.find(x=>x.type==='select'&&x.selected);if(picked?.type==='select'){setSelectedId(picked.id);setEdgeId('')}
    const positions=items.filter(x=>x.type==='position'&&x.position&&x.dragging!==true);
    if(!readOnly&&positions.length)commit({...current.current,nodes:(current.current.nodes||[]).map(n=>{const change=positions.find(x=>x.type==='position'&&x.id===n.id);return change?.type==='position'&&change.position?{...n,x:Math.round(change.position.x),y:Math.round(change.position.y)}:n})},'node-position');
  };
  const orgPeople=people.filter(p=>!selected?.org_id||p.org_id===selected.org_id||p.organization_id===selected.org_id);
  const ownerName=(id?:string)=>people.find(p=>p.id===id)?.title||people.find(p=>p.id===id)?.name||'';
  const fields=[['responsibility','办理职责','需要完成什么、向谁负责'],['authority','行权边界','审批权限、金额范围或集体决策要求'],['control','流程控制','不相容岗位分离、复核与回避要求'],['evidence','留痕材料','应形成的申请、意见、决议或凭证'],['time_limit','办理时限','办理期限及异常情形的处理要求']];
  void historyVersion;
  return <section className={`authority-flow ${readOnly?'is-readonly':''}`} aria-label="业务审批流程编辑器">
    <header className="authority-flow-toolbar"><div className="authority-flow-heading"><Route size={17}/><b>业务审批图</b><span>{nodes.length} 个环节 · {edges.length} 条联系</span></div><div className="authority-flow-actions">
      {!readOnly&&<><AccessButton type="button" title="撤销流程编辑" aria-label="撤销流程编辑" disabled={!undo.current.length} onClick={()=>undoChange('undo')}><Undo2 size={15}/></AccessButton><AccessButton type="button" title="重做流程编辑" aria-label="重做流程编辑" disabled={!redo.current.length} onClick={()=>undoChange('redo')}><Redo2 size={15}/></AccessButton><i/><AccessButton type="button" disabled={!nodes.length} onClick={arrange}><LayoutGrid size={15}/>整理布局</AccessButton></>}
      <AccessButton type="button" onClick={()=>flow.current?.fitView({padding:.18,maxZoom:1,duration:200})}><Maximize2 size={15}/>适应画布</AccessButton>
    </div></header>
    {!readOnly&&<div className="authority-flow-palette"><span><Grip size={14}/>添加环节</span>{NODE_KINDS.map(meta=>{const Icon=meta.icon;return <AccessButton key={meta.kind} type="button" draggable onDragStart={e=>{e.dataTransfer.setData('application/authority-node',meta.kind);e.dataTransfer.effectAllowed='copy'}} onClick={()=>addNode(meta.kind)} title={`点击添加或拖入画布：${meta.label}`}><Icon size={14} style={{color:meta.color}}/>{meta.label}<Plus size={11}/></AccessButton>})}<small>拖入画布 · 从右侧圆点连向下一环节</small></div>}
    <div className={"authority-flow-workspace "+(readOnly&&!selected&&!selectedEdge?"authority-flow-overview":"")}><div className="authority-flow-canvas" ref={canvas} tabIndex={0} aria-label="流程画布" onDragOver={e=>{if(!readOnly&&e.dataTransfer.types.includes('application/authority-node')){e.preventDefault();e.dataTransfer.dropEffect='copy'}}} onDrop={e=>{if(readOnly)return;const kind=e.dataTransfer.getData('application/authority-node');if(!NODE_KINDS.some(n=>n.kind===kind))return;e.preventDefault();addNode(kind,flow.current?.screenToFlowPosition({x:e.clientX,y:e.clientY}))}} onKeyDown={e=>{
      if(readOnly||(e.target as HTMLElement).closest('input,textarea,select,[contenteditable=true]'))return;
      if((e.key==='Delete'||e.key==='Backspace')&&(selected||selectedEdge)){e.preventDefault();removeSelected()}
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undoChange(e.shiftKey?'redo':'undo')}
    }}>
      <ReactFlow nodes={renderNodes} edges={rfEdges} nodeTypes={nodeTypes} onInit={instance=>{flow.current=instance;setReady(true)}} onNodesChange={changes} onNodeClick={(_,n)=>{setSelectedId(n.id);setEdgeId('');setConnectTo('')}} onEdgeClick={(_,e)=>{setEdgeId(e.id);setSelectedId('')}} onPaneClick={()=>{setSelectedId('');setEdgeId('')}} onNodeDragStop={(_,node)=>{if(!readOnly)commit({...current.current,nodes:(current.current.nodes||[]).map(n=>n.id===node.id?{...n,x:Math.round(node.position.x),y:Math.round(node.position.y)}:n)})}} onConnect={connect} nodesDraggable={!readOnly} nodesConnectable={!readOnly} edgesReconnectable={false} deleteKeyCode={null} multiSelectionKeyCode={null} selectionOnDrag={false} zoomOnDoubleClick={false} minZoom={.18} maxZoom={1.5} proOptions={{hideAttribution:true}} aria-label="业务审批流程关系图"><Background gap={24} size={.8} color="#d2dbe5"/></ReactFlow>
      {!nodes.length&&<div className="authority-flow-empty"><Route size={30}/><h3>{readOnly?'尚未记录运行环节':'从一个办理环节开始'}</h3><p>{readOnly?'该规范暂未配置流程图。':'拖入开始、办理、判断等环节，连接后补充职责、行权边界与留痕要求。'}</p>{!readOnly&&<AccessButton type="button" className="btn" onClick={()=>addNode('start')}><Plus size={14}/>添加开始环节</AccessButton>}</div>}
      <div className="authority-flow-zoom"><AccessButton type="button" aria-label="放大流程图" onClick={()=>flow.current?.zoomIn({duration:150})}><Plus size={16}/></AccessButton><AccessButton type="button" aria-label="缩小流程图" onClick={()=>flow.current?.zoomOut({duration:150})}><Minus size={16}/></AccessButton></div>
    </div><aside className="authority-flow-inspector" aria-label="流程环节详情">
      {selected?<><div className="authority-flow-inspector-title"><span>{metadata(selected.kind).label}详情</span>{!readOnly&&<AccessButton type="button" className="authority-flow-delete" onClick={removeSelected} title="删除环节及相连关系"><Trash2 size={13}/>删除</AccessButton>}</div>
        {readOnly?<><h3>{selected.title}</h3><dl>{[['办理机构',byOrg.get(selected.org_id||'')],['责任岗位',selected.role],['具体人员',ownerName(selected.person_id)],...fields.map(([key,label])=>[label,selected[key]])].filter(([,value])=>value).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></>:<>
          <label>环节名称<input value={selected.title||''} maxLength={120} onChange={e=>updateNode('title',e.target.value)} placeholder="填写明确的办理事项"/></label>
          <label>办理机构<select aria-label="办理机构" value={selected.org_id||''} onChange={e=>updateNode('org_id',e.target.value)}><option value="">待明确机构</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.title}</option>)}</select></label>
          <div className="authority-flow-field-pair"><label>责任岗位<input value={selected.role||''} maxLength={120} onChange={e=>updateNode('role',e.target.value)} placeholder="如财务复核岗"/></label><label>具体人员<select aria-label="具体人员" value={selected.person_id||''} onChange={e=>updateNode('person_id',e.target.value)}><option value="">按岗位管理</option>{orgPeople.map(p=><option key={p.id} value={p.id}>{p.title||p.name}</option>)}</select></label></div>
          {fields.map(([key,label,placeholder])=><label key={key}>{label}{key==='time_limit'?<input value={selected[key]||''} maxLength={300} placeholder={placeholder} onChange={e=>updateNode(key,e.target.value)}/>:<textarea value={selected[key]||''} rows={2} maxLength={4000} placeholder={placeholder} onChange={e=>updateNode(key,e.target.value)}/>}</label>)}
          {selected.kind!=='end'&&nodes.some(n=>n.id!==selected.id&&n.kind!=='start')&&<div className="authority-flow-connect"><b><ArrowRight size={13}/>连接下一环节</b><select aria-label="选择下一环节" value={connectTo} onChange={e=>setConnectTo(e.target.value)}><option value="">选择下游办理环节</option>{nodes.filter(n=>n.id!==selected.id&&n.kind!=='start').map(n=><option key={n.id} value={n.id}>{n.title||'未命名环节'}</option>)}</select><input aria-label="流转条件" placeholder={selected.kind==='decision'?'如：达到集体决策权限':'流转条件（选填）'} value={connectionLabel} maxLength={200} onChange={e=>setConnectionLabel(e.target.value)}/><AccessButton type="button" disabled={!connectTo} onClick={()=>connect({source:selected.id,target:connectTo,label:connectionLabel})}>建立连接<ArrowRight size={13}/></AccessButton></div>}
        </>}
      </>:selectedEdge?<><div className="authority-flow-inspector-title"><span>流转关系</span>{!readOnly&&<AccessButton type="button" className="authority-flow-delete" onClick={removeSelected}><Trash2 size={13}/>删除</AccessButton>}</div><div className="authority-flow-relation"><b>{nodes.find(n=>n.id===selectedEdge.source)?.title}</b><ArrowRight size={18}/><b>{nodes.find(n=>n.id===selectedEdge.target)?.title}</b></div>{readOnly?<p className="authority-flow-edge-copy">{selectedEdge.label||'顺序办理'}</p>:<label>流转条件或关系说明<textarea rows={3} maxLength={200} value={selectedEdge.label||''} placeholder="如：审核通过、材料需补正；机构之间可填写职责协同关系" onChange={e=>commit({...current.current,edges:(current.current.edges||[]).map(item=>item.id===selectedEdge.id?{...item,label:e.target.value}:item)},selectedEdge.id+':label')}/><small>条件判断环节应分别写清各条分支的适用条件。</small></label>}</>:<div className="authority-flow-guide"><span className="authority-flow-guide-icon"><ShieldCheck size={24}/></span><h3>把行权过程说清楚</h3><p>选择一个环节，查看或补充“谁来办、办什么、依何行权、如何制约、留下什么记录”。</p><ol><li>办理事项与条件分支</li><li>机构、岗位及人员归属</li><li>权限边界与制约措施</li></ol><div>组织职责方案可用“机构职责”环节记录机构设置、职责配置与协同关系。</div></div>}
    </aside></div>
    <footer className="authority-flow-footer"><span role="status" aria-live="polite">{notice||(readOnly?'阅读模式 · 点击环节或连线查看权责要求':'画布编辑会进入当前草稿；完成后请保存并人工复核。')}</span><small>{readOnly?'只读查看':'拖动定位 · 连线表达流转 · 支持撤销'}</small></footer>
  </section>;
}
export default AuthorityFlowEditor;
