import {ButtonHTMLAttributes,Children,isValidElement,ReactNode,forwardRef} from 'react';
import {useAuth} from '../auth';
function labelText(children:ReactNode):string{return Children.toArray(children).map(child=>typeof child==='string'||typeof child==='number'?String(child):isValidElement<{children?:ReactNode}>(child)?labelText(child.props.children):'').join('')}
/** Permission-aware affordance for existing business controls. Server authorization remains authoritative. */
export const AccessButton=forwardRef<HTMLButtonElement,ButtonHTMLAttributes<HTMLButtonElement>&{permission?:'read'|'edit'|'review'}>(function AccessButton({permission,...props},ref){
 const auth=useAuth(),permissions=auth?.session?.permissions;
 const label=(String(props['aria-label']||'')+' '+labelText(props.children)).trim();
 const review=/^(?:审核通过|通过审核|确认通过|确认审核|确认退回|批准|退回|发布|确认发布|审核发布|不采纳此关联|确认关联成立)/.test(label);
 const edit=/Excel.*导入|一键导入/.test(label)||/^(?:新建|创建|建立|发起|保存|提交|上传|导入|批量上传|删除|移除|编辑|修订|修改|调整|采纳|采用|生成|开始萃取|重新萃取|确认|完成|办结|归档|撤回|更新|重置|恢复|添加|增补|补齐|拆分|合并|运行|执行|继续接入|AI\s*(?:建议|构建|识别|分析|整理|萃取|提炼)|智能构建)/.test(label);
 const readControl=/^(?:检索|搜索|查询|筛选|查找|查看|打开|展开|收起|返回|上一页|下一页|刷新|重新加载|重试|取消|关闭|导出|下载|打印|放大|缩小|适应|选择|清空|收藏)/.test(label)||props.role==='option'||props['aria-haspopup']==='listbox'||props.role==='tab'||props.className?.split(' ').some(c=>['filter-tab','catalog-root','catalog-tree-select','catalog-group'].includes(c));
 const required=permission||(readControl?'read':review?'review':edit?'edit':'read');
 const denied=required==='edit'?!permissions?.can_edit:required==='review'?!permissions?.can_review:false;
 return <button ref={ref} {...props} disabled={props.disabled||denied} data-access={required} title={denied?(required==='review'?'当前角色没有审核发布权限':'当前角色没有建设维护权限'):props.title} className={[props.className,denied?'access-disabled':''].filter(Boolean).join(' ')}/>;
});
