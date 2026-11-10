/** Shared business vocabulary. Source documents retain their original wording. */
export const MODULES={overview:'机构总览',group:'集团成果',security:'账号安全',administration:'账号与权限',work:'我的工作台',projects:'专题工作室',experience:'经验萃取室',materials:'素材整理',assets:'业务资产',datasets:'数据导入',graph:'本体图谱',applications:'本体应用',impact:'变更复核',maintenance:'审核维护',settings:'系统设置',briefing:'业务汇报导览',knowledge:'知识专栏',authority:'权责规范'} as const;
export function entityHref(id:string,kind?:string):string{
 const encoded=encodeURIComponent(id);
 if(kind==='person'||id.startsWith('PERSON-AUTH-'))return '/authority?person='+encoded;
 if(kind==='collection'||/^COL-K-/.test(id))return '/knowledge/collections/'+encoded;
 if(kind==='material'||/^(L-[NP]\d-|MAT-)/.test(id))return '/materials/'+encoded;
 if(kind==='dataset'||/^DS-/.test(id))return '/datasets/'+encoded;
 if(kind==='project'||/^PRJ-/.test(id))return '/projects/'+encoded;
 if(kind==='task'||/^TASK-/.test(id))return '/tasks/'+encoded;
 return '/assets/'+encoded;
}
export const OBJECT_NAMES:Record<string,string>={data:'数据资产',rule:'规则资产',skill:'技能资产',knowledge:'知识资产',material:'素材',dataset:'数据台账',project:'专题',task:'任务',collection:'知识专栏',matter:'业务事项',evidence:'依据引用',organization:'组织单位',person:'任职人员',case:'案例',issue:'核对事项'};
const EVENTS:Record<string,string>={create:'创建记录',upload:'上传素材',extract:'开展资产萃取',submit:'提交审核',approve:'审核通过',return:'退回补充',publish:'发布成果',revise:'发起修订',archive:'归档记录',complete:'办结任务',claim:'认领任务',reopen:'重新办理',edit:'修改内容',update:'更新记录',candidate_review:'核对候选成果',candidate_merge:'合并候选成果',candidate_adopt:'采纳为资产草稿',candidate_reject:'暂不采纳候选',candidate_restore:'恢复候选',segment:'整理原文分段',segment_confirm:'确认分段',mapping:'核对字段对应',analyze:'开展分析',generate:'生成整理建议',delete:'移除记录',adopt:'采纳成果',reject:'暂不采纳',restore:'恢复记录'};
export function eventLabel(action:string,kind?:string){
 if(!action)return '业务记录';
 if(action==='create'&&kind)return '创建'+(OBJECT_NAMES[kind]||'记录');
 if(action==='archive'&&kind)return '归档'+(OBJECT_NAMES[kind]||'记录');
 return EVENTS[action]||(/[\u3400-\u9fff]/.test(action)?action:'业务记录更新');
}
export function routeLabel(path:string){
 const parts=path.split('?')[0].split('/').filter(Boolean);
 if(!parts.length)return MODULES.overview;
 if(parts[0]==='knowledge')return MODULES.knowledge;
 if(parts[0]==='tasks')return '任务办理';
 if(parts[0]==='skill-development')return '技能开发';
 if(parts[0]==='integration-applications')return '集成应用';
 if(parts[0]==='data-sources')return '数据源管理';
 const key=parts[0] as keyof typeof MODULES;
 return MODULES[key]||'业务档案';
}
