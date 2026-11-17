/*
 * 知元界面基准 v1 · 基准页共用脚本
 * 做三件事：注入图标、画出外壳（顶栏与左侧导航）、提供基准页的状态切换器。
 * 实施时外壳由 AppShell / TopBar / NavRail 实现，图标用 @ant-design/icons 中含义相同的线性图标。
 *
 * 每张基准页在 <body> 上声明：
 *   data-ws    当前一级工作区：home | build | ontology | apply | govern | admin
 *   data-item  当前二级入口的名称（与导航文字一致）；工作台留空
 *   data-user  当前用户；data-role 身份
 * 页面里的 <i data-icon="名称"></i> 会被换成对应图标。
 *   data-form  页面形态：split 表示三栏页或列表加详情页（视口不足 1440 时导航自动收起）
 * 并可在本脚本之前定义 window.BM = { states: [[值, 名称], …], views: true }。
 * 地址栏可带 #state=…&view=pro&bare 直达某个状态（bare 隐藏切换器，用于截图）。
 */
(function () {
  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    build:
      '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14.5v6M14.5 17.5h6"/>',
    ontology:
      '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.2 10.9 7.6-3.8M8.2 13.1l7.6 3.8"/>',
    apply: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    govern:
      '<path d="M12 3 4.5 6v5.5c0 4.5 3.1 8 7.5 9.5 4.4-1.5 7.5-5 7.5-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
    admin:
      '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h10M18 18h2"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    bell: '<path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7 2.5 7h-17S6 15 6 9z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    tasks: '<path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17M11 6h10M11 12h10M11 18h10"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.2M12 17h0"/>',
    'chev-down': '<path d="m6 9 6 6 6-6"/>',
    'chev-right': '<path d="m9 6 6 6-6 6"/>',
    'chev-left': '<path d="m15 6-6 6 6 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4M12 17h0"/>',
    'x-circle': '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h0"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    sparkle: '<path d="M11 4 12.8 9.2 18 11l-5.2 1.8L11 18l-1.8-5.2L4 11l5.2-1.8zM19 15.5v4M17 17.5h4"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    more: '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    'arrow-left': '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 10l-2 2 2 2"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    undo: '<path d="M8 6 4 10l4 4M4 10h11a5 5 0 0 1 0 10h-3"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    flask:
      '<path d="M9 3h6M10 3v6l-5.5 9.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3M7.5 15h9"/>',
    archive:
      '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
    return: '<path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 6 6v5"/>',
    rule: '<path d="M6 3h12v18H6zM9 8h6M9 12h6M9 16h3"/>',
    data: '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
    skill: '<path d="M13 3 5 13.5h6L10 21l9-11h-6z"/>',
    knowledge: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>',
    people:
      '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
    columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    quote: '<path d="M5 17v-5a5 5 0 0 1 5-5M5 12h4v5H5zM14 17v-5a5 5 0 0 1 5-5M14 12h4v5h-4z"/>',
  };
  const NAV = [
    ['home', '工作台', []],
    ['build', '建设', ['制度整理', '素材整理', '数据台账导入', '数据源管理', '经验萃取室', '技能开发', '专题工作室', '我的工作台']],
    ['ontology', '本体', ['本体目录', '业务资产', '权责规范', '本体图谱']],
    ['apply', '应用', ['应用助手', '我的报告']],
    ['govern', '治理', ['审核中心', '审核维护', '变更复核']],
    ['admin', '管理', ['账号与权限', '模型与运行', '备份与升级', '演示资料', '系统信息']],
  ];
  const icon = (name, cls) =>
    '<svg class="zy-icon' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  window.zyIcon = icon;

  const sprite =
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true">' +
    Object.entries(ICONS)
      .map(([k, v]) => '<symbol id="i-' + k + '" viewBox="0 0 24 24">' + v + '</symbol>')
      .join('') +
    '</svg>';

  /* 标志位：实施时用仓库现有的产品标志文件；基准页用同色占位图形，不复制任何标志 */
  const mark =
    '<svg class="zy-brand__mark" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="var(--zy-color-accent)"/><path d="M10 21h12L16 10.5z" fill="none" stroke="var(--zy-color-text-inverse)" stroke-width="1.8" stroke-linejoin="round"/><circle cx="10" cy="21" r="2.6" fill="var(--zy-color-text-inverse)"/><circle cx="22" cy="21" r="2.6" fill="var(--zy-color-text-inverse)"/><circle cx="16" cy="10.5" r="2.6" fill="var(--zy-color-text-inverse)"/></svg>';

  function navHtml(ws, item, collapsed) {
    return (
      '<nav class="zy-nav' + (collapsed ? ' zy-nav--collapsed' : '') + '" aria-label="主导航">' +
      NAV.map(([key, label, subs]) => {
        const open = key === ws;
        const current = open && !item ? ' aria-current="page"' : '';
        let html =
          '<a class="zy-nav__item' + (open ? ' is-open' : '') + '" href="#"' + current + '>' +
          icon(key, 'zy-icon--lg') + '<span>' + label + '</span>' +
          (subs.length ? icon(open ? 'chev-down' : 'chev-right', 'zy-nav__chev') : '') +
          '</a>';
        if (open && subs.length)
          html +=
            '<div class="zy-nav__group">' +
            subs
              .map((s) => '<a class="zy-nav__sub" href="#"' + (s === item ? ' aria-current="page"' : '') + '>' + s + '</a>')
              .join('') +
            '</div>';
        return html;
      }).join('') +
      '<div class="zy-nav__foot"><a class="zy-nav__item" href="#">' + icon('panel', 'zy-icon--lg') + '<span>收起导航</span></a></div>' +
      '</nav>'
    );
  }
  function topbarHtml(user, role, org) {
    return (
      '<header class="zy-topbar">' +
      '<div class="zy-brand">' + mark + '<span class="zy-brand__name">知元</span></div>' +
      '<button class="zy-org" type="button" aria-haspopup="dialog">' + org + '<span class="zy-org__role">' + role + '</span>' + icon('chev-down') + '</button>' +
      '<div class="zy-omnibox" role="search">' + icon('search') + '<span>查找内容，或说一句想做的事</span><kbd class="zy-kbd">Ctrl K</kbd></div>' +
      '<div class="zy-topbar__tools">' +
      '<button class="zy-iconbtn" type="button" aria-label="任务，2 项进行中">' + icon('tasks', 'zy-icon--lg') + '<span class="zy-count">2</span></button>' +
      '<button class="zy-iconbtn" type="button" aria-label="通知">' + icon('bell', 'zy-icon--lg') + '</button>' +
      '<button class="zy-iconbtn" type="button" aria-label="帮助">' + icon('help', 'zy-icon--lg') + '</button>' +
      '<button class="zy-iconbtn" type="button">' + icon('sparkle', 'zy-icon--lg') + '助手</button>' +
      '<button class="zy-user" type="button" aria-haspopup="menu"><span class="zy-avatar">' + user.slice(0, 1) + '</span>' + user + icon('chev-down') + '</button>' +
      '</div></header>'
    );
  }
  window.zyShell = { navHtml, topbarHtml };

  const body = document.body;
  const d = body.dataset;
  /* 页面里写 <i data-icon="check"></i> 即可，这里换成图标；class 原样保留 */
  document.querySelectorAll('i[data-icon]').forEach((el) => {
    el.outerHTML = icon(el.dataset.icon, el.className);
  });
  body.insertAdjacentHTML(
    'afterbegin',
    sprite + topbarHtml(d.user || '周明远', d.role || '建设人员', d.org || '集团总部') + navHtml(d.ws || 'home', d.item || '', d.nav === 'collapsed'),
  );

  /* 基准页状态切换器 */
  const bm = window.BM || {};
  const params = new URLSearchParams(location.hash.slice(1));
  function apply() {
    document.querySelectorAll('[data-when]').forEach((el) => {
      el.hidden = !el.dataset.when.split(' ').includes(d.state);
    });
    document.querySelectorAll('.bm-switch button').forEach((b) => {
      const [k, v] = b.dataset.set.split(':');
      b.setAttribute('aria-pressed', String((d[k] || '') === v));
    });
  }
  if (bm.states) d.state = params.get('state') || bm.states[0][0];
  if (bm.views) d.view = params.get('view') || 'simple';
  if (params.has('bare')) d.bare = '';
  if (bm.states || bm.views) {
    let html = '<div class="bm-switch" data-meta><span class="bm-switch__label">基准页状态</span>';
    (bm.states || []).forEach(([v, n]) => (html += '<button type="button" data-set="state:' + v + '">' + n + '</button>'));
    if (bm.views)
      html +=
        '<span class="bm-switch__label">视图</span><button type="button" data-set="view:simple">简洁视图</button><button type="button" data-set="view:pro">专业视图</button>';
    body.insertAdjacentHTML('beforeend', html + '</div>');
    body.querySelector('.bm-switch').addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      const [k, v] = b.dataset.set.split(':');
      d[k] = v;
      apply();
    });
  }
  /* 页面内写了 data-go="state:xxx" 的入口，点击即切到对应状态（与设计中的真实入口一致） */
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-go]');
    if (!t) return;
    e.preventDefault();
    const [k, v] = t.dataset.go.split(':');
    d[k] = v;
    apply();
  });
  apply();
})();
