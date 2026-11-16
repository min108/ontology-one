# R1-5 图谱迟到视口回调修复

实际浏览器中，图谱自动适应画布的动画尚未结束时进入建设工作区，迟到的 `onMoveEnd` 会继续调用旧 `setSearchParams`，将地址从 `/build` 拉回 `/graph?vx=…`。InstanceGraph 的实例视口回调存在相同问题；图谱与实例图之间切换时，旧回调还会污染新视角的查询参数。

GraphWorkbench 与 InstanceGraph 均使用布局效应登记组件存活状态，卸载时立即作废。视口保存首先验证组件仍存活、工作区尚未离开；仅有效页面继续保存既有视口参数。没有延迟导航或等待动画，也没有改动旧 API 请求封装。

新增真实页面组件测试，mock ReactFlow 仅捕获其 `onMoveEnd`，在真实 BrowserRouter 导航完成后主动交付已捕获的迟到回调。两个页面分别覆盖：仍挂载时保存视口、离开至建设页、切换图谱类型、机构切换已进入 leaving 而组件尚未卸载。全部运行于 StrictMode。修复前 6 个故障反例失败、2 个正常保存用例通过；修复后 8 项全部通过。

类型检查、lint、format、legacy 清单检查与 diff 空白检查通过。legacy/api.ts SHA256 保持 `677bd70d30a3fefcec350d2fab76d47e339deccc932266257f02b37790315958`。浏览器故障最终复验由根外壳工作树集成后完成。
