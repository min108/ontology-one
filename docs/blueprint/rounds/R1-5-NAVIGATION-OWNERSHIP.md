# R1-5 来路同步的地址所有权

根真实浏览器 trace 显示，首次返航是 `/build` → `/graph?via=…`，没有 `vx` 参数；图谱视口保存发生在返航之后。因此仅修复 Graph 回调不足以解释或消除整个竞态。

NavigationProvider 的布局效应原先信任渲染时的 Location，会按该地址补齐来路参数。Suspense 保留旧 Location/Route 上下文时，浏览器可能已经进入新的地址；旧效应随后补 `via`，将浏览器恢复到旧路由。

修复在 `useLayoutEffect` 入口逐项比较浏览器实际 pathname、search、hash 与当前渲染 Location。在任何 pending 读取/清理、活动来路或缓存变动、滚动、navigate 之前，若地址不一致立即返回。两个 `anchor.closest` 判断不在本次变更中，根外壳新增的导航根标记保持不动。

真实 BrowserRouter 组件反例分别将浏览器置于 `/build`、`/graph?q=next`、`/graph#next`，同时保留旧 `/graph` Route/Location。修复前 3 项均失败，实际收到 `/graph?via=…`；修复后断言目的地址不变、本地导航缓存未写入、scrollTo 未执行。另一个正常流程测试覆盖补齐 via、跨页来路 parent、返回链接以及恢复保存的 420px 滚动位置。

4 项来路测试及 14 项图谱测试通过；类型、lint、format、diff 检查通过。legacy/api.ts 字节不变。根合并后继续以原浏览器流程复验，组件测试不代替真实浏览器最终验收。
