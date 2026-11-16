// Acceptance expectations, independent of the application navigation implementation.
export const primaryPaths = [
  "/",
  "/build",
  "/ontology",
  "/apply",
  "/govern",
  "/admin",
];
export const redirects = [
  [
    "/security?probe=security#password",
    "/account/security?probe=security#password",
    "账号安全",
  ],
  [
    "/administration?probe=users#members",
    "/admin/users?probe=users#members",
    "账号与权限",
  ],
  [
    "/settings?probe=model#connection",
    "/admin/model?probe=model#connection",
    "模型与运行",
  ],
  ["/settings?probe=demo#demo", "/admin/demo?probe=demo#demo", "演示资料"],
];
export const layouts = [
  { name: "1366", width: 1366, height: 768, zoom: "native 100%" },
  {
    name: "125-equivalent",
    width: 1093,
    height: 614,
    zoom: "125% equivalent CSS viewport; not native browser zoom",
  },
];
export function secondaryPaths(workspace, role, permissions) {
  if (!["modeler", "reviewer", "admin"].includes(role))
    throw new Error("Unreviewed acceptance role");
  const paths = {
    "/": [],
    "/build": [
      "/materials",
      "/datasets",
      "/data-sources",
      "/experience",
      "/skill-development",
      "/projects",
      "/work",
    ],
    "/ontology": [
      "/assets",
      "/authority",
      "/graph",
      ...(permissions.includes("federation.read") ? ["/group"] : []),
    ],
    "/apply": ["/applications"],
    "/govern": ["/maintenance", "/impact"],
    "/admin": [
      ...(role === "admin" ? ["/admin/users"] : []),
      "/admin/model",
      ...(role === "admin" ? ["/integration-applications"] : []),
      "/admin/backup",
      "/admin/demo",
      "/admin/system",
    ],
  };
  if (!(workspace in paths)) throw new Error("Unreviewed acceptance workspace");
  return paths[workspace];
}

export function redirectMatches(value, target) {
  const url = new URL(value);
  if (url.searchParams.getAll("via").length > 1) return false;
  url.searchParams.delete("via");
  return url.pathname + url.search + url.hash === target;
}
