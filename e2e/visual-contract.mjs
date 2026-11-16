// Reviewed acceptance inventory, independent of application route configuration.
export const visualLayouts = [
  { name: "1366", width: 1366, height: 768, zoom: 1 },
  { name: "1920", width: 1920, height: 1080, zoom: 1 },
  { name: "1366-native125", width: 1366, height: 768, zoom: 1.25 },
];
export const visualModes = ["simple", "professional"];
export const visualPages = [
  {
    name: "home",
    path: "/",
    heading: "工作台",
    ready: '[data-home-workbench][aria-busy="false"]',
  },
  { name: "shell-legacy", path: "/materials", legacy: true },
  {
    name: "security",
    path: "/account/security",
    heading: "账号安全",
    ready: '[aria-label="账号安全"][aria-busy="false"]',
  },
  {
    name: "users",
    path: "/admin/users",
    heading: "账号与权限",
    ready: '[aria-label="账号与权限"][aria-busy="false"]',
  },
  {
    name: "orgs",
    path: "/admin/orgs",
    heading: "机构目录",
    ready: '[aria-label="机构目录"][aria-busy="false"]',
  },
  {
    name: "audit",
    path: "/admin/audit",
    heading: "审计记录",
    ready: '[aria-label="审计记录"][aria-busy="false"]',
  },
  {
    name: "model",
    path: "/admin/model",
    heading: "模型与运行",
    ready: '[data-admin-ready="true"]',
  },
  {
    name: "backup",
    path: "/admin/backup",
    heading: "备份与升级",
    ready: '[data-admin-ready="true"]',
  },
  {
    name: "system",
    path: "/admin/system",
    heading: "系统信息",
    ready: '[data-admin-ready="true"]',
  },
  {
    name: "demo",
    path: "/admin/demo",
    heading: "演示资料",
    readyButton: "刷新状态",
  },
];

export function assertVisualInventory() {
  if (
    visualPages.length < 8 ||
    new Set(visualPages.map((page) => page.path)).size !== visualPages.length
  )
    throw new Error(
      "Visual acceptance requires at least eight distinct page states",
    );
  if (visualLayouts.length !== 3 || visualModes.length !== 2)
    throw new Error("Visual acceptance requires three layouts and both views");
}

export function seriousViolations(result) {
  return result.violations.filter((item) =>
    ["serious", "critical"].includes(item.impact),
  );
}
