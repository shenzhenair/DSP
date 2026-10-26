// -*- coding: utf-8 -*-
// 通用工具函数 + 侧边栏 + API 封装

// ============================================================
// 图标 SVG（内联，避免外部资源依赖）
// ============================================================
const ICO = {
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
  log: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" style="width:13px;height:13px"><path d="M12 5v14M5 12h14"/></svg>',
};

// ============================================================
// 状态常量
// ============================================================
const STATUS = {
  running: { cls: 'run', txt: '运行中' },
  success: { cls: 'ok', txt: '成功' },
  failed: { cls: 'err', txt: '失败' },
  never: { cls: 'never', txt: '未执行' },
  paused: { cls: 'paused', txt: '已停用' },
};

// ============================================================
// 工具函数
// ============================================================
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtTime(s) {
  if (!s) return '—';
  return s.replace('T', ' ').substring(0, 19);
}

function fmtDuration(sec) {
  if (sec == null || sec === '') return '—';
  sec = parseFloat(sec);
  if (sec < 60) return sec.toFixed(1) + 's';
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  if (m < 60) return m + 'm' + (r ? r + 's' : '');
  const h = Math.floor(m / 60), rm = m % 60;
  return h + 'h' + rm + 'm';
}

function cronText(cron) {
  if (!cron) return '—';
  const p = cron.trim().split(/\s+/);
  if (p.length !== 5) return cron;
  const [mi, h, dom, mon, dow] = p;
  if (mi === '0' && h === '*' && dom === '*' && mon === '*' && dow === '*') return '每小时整点';
  if (dom === '*' && mon === '*' && /^\d+$/.test(mi) && /^\d+$/.test(h)) {
    const t = `${String(+h).padStart(2,'0')}:${String(+mi).padStart(2,'0')}`;
    if (dow === '1-5') return `工作日 ${t}`;
    if (dow === '*') return `每日 ${t}`;
    if (/^[0-6]$/.test(dow)) {
      const wd = ['日','一','二','三','四','五','六'][+dow];
      return `每周${wd} ${t}`;
    }
  }
  if (dow === '*' && mon === '*' && /^\d+$/.test(mi) && /^\d+$/.test(h) && /^\d+$/.test(dom)) {
    return `每月${+dom}日 ${String(+h).padStart(2,'0')}:${String(+mi).padStart(2,'0')}`;
  }
  if (mon === '1,4,7,10' && dow === '*' && /^\d+$/.test(mi) && /^\d+$/.test(h) && /^\d+$/.test(dom)) {
    return `每季度${+dom}日 ${String(+h).padStart(2,'0')}:${String(+mi).padStart(2,'0')}`;
  }
  return cron;
}

// ============================================================
// API 封装
// ============================================================
async function api(url, opts) {
  opts = opts || {};
  const headers = opts.headers || {};
  let body = opts.body;
  if (body && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const resp = await fetch(url, {
    method: opts.method || 'GET',
    headers: headers,
    body: body,
    credentials: 'same-origin',
    cache: 'no-store',
  });
  let data;
  try { data = await resp.json(); } catch (e) { data = {}; }
  if (resp.status === 401 && data.auth_required) {
    showLoginModal();
    throw new Error(data.error || '请先登录');
  }
  if (!resp.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

// ============================================================
// Toast 提示
// ============================================================
function toast(msg, type) {
  let wrap = document.getElementById('toastWrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'toast-wrap';
    wrap.id = 'toastWrap';
    document.body.appendChild(wrap);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.classList.add('show'), 10);
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 300);
  }, 2500);
}

// ============================================================
// 确认弹窗
// ============================================================
function confirmModal(title, msg) {
  return new Promise(resolve => {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `
      <div class="modal">
        <div class="modal-h">${esc(title)}</div>
        <div class="modal-b" style="font-size:13px;color:var(--text-2);line-height:1.7">${esc(msg)}</div>
        <div class="modal-f">
          <button class="btn ghost" data-act="cancel">取消</button>
          <button class="btn primary" data-act="ok">确定</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.addEventListener('click', e => {
      const act = e.target.closest('[data-act]');
      if (act || e.target === mask) {
        mask.remove();
        resolve(act && act.dataset.act === 'ok');
      }
    });
  });
}

// ============================================================
// 登录弹窗
// ============================================================
let _loginModal = null;
function showLoginModal() {
  if (_loginModal && document.body.contains(_loginModal)) return;
  fetch('/api/auth/me').then(r => r.json()).then(data => {
    if (!data.auth_enabled) return;
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    _loginModal = mask;
    const loginUrl = 'https://open.feishu.cn/open-apis/authen/v1/index'
      + '?app_id=' + encodeURIComponent(data.login.app_id)
      + '&redirect_uri=' + encodeURIComponent(data.login.redirect_uri)
      + '&state=dsp_login';
    mask.innerHTML = `
      <div class="modal" style="max-width:380px;text-align:center">
        <div class="modal-h">请先登录</div>
        <div class="modal-b">
          <p style="font-size:13px;color:var(--text-2);margin-bottom:18px">使用飞书扫码登录数据调度平台</p>
          <a class="btn primary" href="${esc(loginUrl)}" target="_blank" rel="noopener">
            飞书扫码登录
          </a>
          <p style="font-size:11px;color:var(--sub);margin-top:14px">
            登录后自动刷新当前页面
          </p>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const onMsg = e => {
      if (e.data && e.data.type === 'dsp-auth' && e.data.ok) {
        mask.remove();
        window.removeEventListener('message', onMsg);
        location.reload();
      }
    };
    window.addEventListener('message', onMsg);
    mask.addEventListener('click', e => {
      if (e.target === mask) {
        mask.remove();
        window.removeEventListener('message', onMsg);
        _loginModal = null;
      }
    });
  });
}

// ============================================================
// 侧边栏
// ============================================================
function mountSidebar(active) {
  const items = [
    { k: 'overview', label: '执行总览', href: '/index.html', icon: 'dashboard' },
    { k: 'console', label: '任务控制台', href: '/console.html', icon: 'task' },
    { k: 'monitor', label: '运行监控', href: '/monitor.html', icon: 'log' },
    { k: 'mytasks', label: '我的任务', href: '/mytasks.html', icon: 'me' },
    { k: 'datasources', label: '数据源', href: '/datasources.html', icon: 'db' },
    { k: 'audit', label: '操作审计', href: '/audit.html', icon: 'audit' },
    { k: 'users', label: '用户管理', href: '/users.html', icon: 'users' },
    { k: 'settings', label: '系统设置', href: '/settings.html', icon: 'settings' },
  ];

  function link(it) {
    const isActive = it.k === active;
    return `<a class="nav-item ${isActive ? 'active' : ''}" href="${it.href}">
      <span class="nav-ico">${navIcon(it.icon)}</span>
      <span>${it.label}</span>
    </a>`;
  }

  function navIcon(name) {
    const icons = {
      dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>',
      task: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
      log: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/></svg>',
      me: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
      db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
      audit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/><path d="M3 10h4M3 14h4M3 18h4"/></svg>',
      users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
      settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 2 2 0 1 1 0-4 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.9-1.5 2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9 2 2 0 1 1 0 4z"/></svg>',
    };
    return icons[name] || '';
  }

  const html = `
    <div class="brand">
      <div class="brand-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>
      </div>
      <div class="brand-txt"><b>数据调度平台</b><span>Data Scheduler Platform</span></div>
    </div>
    <nav class="nav">
      <div class="nav-lbl">工作台</div>
      ${items.slice(0, 5).map(link).join('')}
      <div class="nav-lbl">通知与配置</div>
      ${items.slice(5).map(link).join('')}
    </nav>
    <div style="padding:0 10px 10px;margin-top:auto">
      <div class="user-card" id="userBlock" style="display:none;cursor:default">
        <div class="user-av" id="userAv">U</div>
        <div class="user-info" style="flex:1;min-width:0">
          <b id="userName">—</b>
          <span id="userRole">—</span>
        </div>
        <button class="icon-btn" id="logoutBtn" title="退出登录">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </button>
      </div>
      <div class="svc-pill on" id="heartbeat" style="margin: 0 0 4px 0; width: calc(100% - 2px); justify-content: center">
        <span class="svc-dot"></span><span id="hbText">检测中…</span>
      </div>
    </div>`;

  const el = document.getElementById('sidebarMount');
  if (el) {
    el.className = 'sidebar';
    el.innerHTML = html;
  } else {
    const sd = document.createElement('div');
    sd.className = 'sidebar';
    sd.id = 'sidebarMount';
    sd.innerHTML = html;
    document.querySelector('.app').insertBefore(sd, document.querySelector('.main'));
  }

  loadUserInfo();
  startHeartbeat();
}

// ============================================================
// 用户信息
// ============================================================
function loadUserInfo() {
  fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => {
      const block = document.getElementById('userBlock');
      if (!block) return;
      if (data.logged_in && data.user) {
        block.style.display = 'flex';
        document.getElementById('userName').textContent = data.user.name || '—';
        const roleLbl = { admin: '管理员', senior: '高级用户', member: '普通成员', pending: '无权限' };
        document.getElementById('userRole').textContent = roleLbl[data.role] || data.role || '—';
        const av = document.getElementById('userAv');
        if (data.user.avatar_url) {
          av.innerHTML = `<img src="${esc(data.user.avatar_url)}" alt="">`;
        } else {
          av.textContent = (data.user.name || 'U')[0];
        }
        document.getElementById('logoutBtn').onclick = async () => {
          try {
            await api('/api/auth/logout', { method: 'POST' });
            location.reload();
          } catch (e) { toast(e.message, 'err'); }
        };
      } else if (data.auth_enabled) {
        block.style.display = 'flex';
        document.getElementById('userName').textContent = '未登录';
        document.getElementById('userRole').textContent = '点击登录';
        const av = document.getElementById('userAv');
        av.textContent = '?';
        block.style.cursor = 'pointer';
        block.onclick = showLoginModal;
      }
    })
    .catch(() => {});
}

// ============================================================
// 心跳
// ============================================================
let _hbTimer = null;
function startHeartbeat() {
  if (_hbTimer) return;
  const tick = () => {
    fetch('/api/heartbeat', { cache: 'no-store' })
      .then(r => r.json())
      .then(d => {
        const el = document.getElementById('hbText');
        if (!el) return;
        const pill = document.getElementById('heartbeat');
        if (d.status === 'alive') {
          el.textContent = '服务正常';
          pill.className = 'svc-pill on';
        } else {
          el.textContent = '服务异常';
          pill.className = 'svc-pill off';
        }
      })
      .catch(() => {
        const el = document.getElementById('hbText');
        if (!el) return;
        const pill = document.getElementById('heartbeat');
        el.textContent = '连接断开';
        pill.className = 'svc-pill off';
      });
  };
  tick();
  _hbTimer = setInterval(tick, (window.FRONTEND_HEARTBEAT_INTERVAL || 15) * 1000);
}
