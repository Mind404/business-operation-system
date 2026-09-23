'use strict';

/* ==========================================================
   项目工作进度表 - 甘特图工具 v2  前端逻辑
   ========================================================== */

let state = { settings: { start_date: '', end_date: '', auto_range: true }, projects: [] };
let editingId = null;      // 正在编辑的项目 id，null 表示新增
let selected = '全部';     // 当前筛选：'全部' 或某个分类名
let editable = true;        // 当前用户是否可编辑（admin/product可编辑，其他角色只读）

const CATEGORIES = ['产品研发情况', '产品测试情况', '产品生产情况'];
const CATEGORY_META = {
  '产品研发情况': { color: '#3b76ff', short: '研发' },
  '产品测试情况': { color: '#ff9f43', short: '测试' },
  '产品生产情况': { color: '#22b573', short: '生产' }
};

// 甘特图每日宽度从 CSS 变量 --day-w 读取（rem 随屏幕等比缩放，保证与表格对齐）
function unitDayW() {
  const rs = getComputedStyle(document.documentElement);
  const rem = parseFloat(rs.fontSize) || 14;
  const raw = String(rs.getPropertyValue('--day-w') || '').trim();
  const val = parseFloat(raw);
  if (!isFinite(val)) return 36;
  return /rem$/.test(raw) ? val * rem : val;
}

/* ---------------- 工具函数 ---------------- */
function $(sel) { return document.querySelector(sel); }

function pad2(n) { return n < 10 ? '0' + n : '' + n; }

function parseDate(s) {
  if (!s) return null;
  const m = String(s).match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3]);
}

function toISO(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

function fmtDate(s) {           // 2026-09-01 -> 2026/9/1
  const d = parseDate(s);
  if (!d) return s;
  return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate();
}

function fmtShortDate(s) {      // 2026-09-01 -> 9/1
  const d = parseDate(s);
  if (!d) return s;
  return (d.getMonth() + 1) + '/' + d.getDate();
}

function diffDays(a, b) {       // b - a 的天数
  const MS = 86400000;
  return Math.round((b.getTime() - a.getTime()) / MS);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function pctText(n, d) {
  return d ? (n / d * 100).toFixed(2) + '%' : '0.00%';
}

function colorOf(cat) {
  return CATEGORY_META[cat] ? CATEGORY_META[cat].color : '#3b76ff';
}

function filterProjects() {
  if (selected === '全部') return state.projects;
  return state.projects.filter(function (p) { return p.category === selected; });
}

/* ---------------- 只读模式 ---------------- */
function applyReadonlyMode() {
  // 只读模式：隐藏添加/导入/重置按钮，禁用编辑操作
  const body = document.body;
  if (editable) {
    body.classList.remove('readonly-mode');
  } else {
    body.classList.add('readonly-mode');
  }
}

/* ---------------- 数据加载与保存 ---------------- */
async function loadData() {
  try {
    const res = await fetch('/api/gantt/data');
    const j = await res.json();
    if (j.ok) {
      state = j.data;
      if (!state.settings) state.settings = { start_date: '', end_date: '', auto_range: true };
      if (!state.projects) state.projects = [];
      editable = j.data.editable !== false;  // 默认可编辑，后端明确返回false时只读
      if (!selected || (selected !== '全部' && CATEGORIES.indexOf(selected) < 0)) selected = '全部';
      applyReadonlyMode();
      renderAll();
    } else {
      alert('加载数据失败：' + (j.error || '未知错误'));
    }
  } catch (e) {
    alert('无法连接服务器，请确认服务正在运行');
  }
}

async function saveData(silent) {
  try {
    const res = await fetch('/api/gantt/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: state.settings, projects: state.projects })
    });
    const j = await res.json();
    if (!j.ok) {
      alert('保存失败：' + (j.error || '未知错误'));
      return false;
    }
    if (!silent) toast('已自动保存');
    return true;
  } catch (e) {
    alert('保存失败：无法连接服务器');
    return false;
  }
}

/* ---------------- 统计 ---------------- */
function renderStats() {
  const wrap = $('#statCards');
  const total = state.projects.length;
  const tDone = state.projects.filter(function (p) { return p.done; }).length;
  const groups = [
    { key: '全部', name: '全部项目', color: '#3b76ff', list: state.projects },
    { key: '产品研发情况', name: '产品研发情况', color: colorOf('产品研发情况'), list: [] },
    { key: '产品测试情况', name: '产品测试情况', color: colorOf('产品测试情况'), list: [] },
    { key: '产品生产情况', name: '产品生产情况', color: colorOf('产品生产情况'), list: [] }
  ];
  CATEGORIES.forEach(function (c) {
    const g = groups.find(function (x) { return x.key === c; });
    g.list = state.projects.filter(function (p) { return p.category === c; });
  });

  let html = '';
  groups.forEach(function (g) {
    const done = g.list.filter(function (p) { return p.done; }).length;
    html +=
      '<div class="stat ' + (selected === g.key ? 'active' : '') + '" data-filter="' + g.key + '" style="--card-color:' + g.color + '">' +
      '<div class="stat-name">' + g.name + '</div>' +
      '<div class="stat-num">' + g.list.length + '</div>' +
      '<div class="stat-pct">' + pctText(done, g.list.length) + '</div>' +
      '</div>';
  });
  wrap.innerHTML = html;
}

/* ---------------- 环形图 ---------------- */
function renderDonut() {
  const total = state.projects.length;
  const done = state.projects.filter(function (p) { return p.done; }).length;
  const pct = total ? done / total * 100 : 0;
  const svg = $('#donutSvg');
  svg.innerHTML =
    '<circle cx="70" cy="70" r="52" fill="none" stroke="#e6ebf4" stroke-width="13"/>' +
    '<circle cx="70" cy="70" r="52" fill="none" stroke="#3b76ff" stroke-width="13" stroke-linecap="round" ' +
    'stroke-dasharray="' + pct.toFixed(2) + ' ' + (100 - pct.toFixed(2)) + '" transform="rotate(-90 70 70)"/>' +
    '<text x="70" y="64" text-anchor="middle" class="donut-pct">' + pct.toFixed(2) + '%</text>' +
    '<text x="70" y="86" text-anchor="middle" class="donut-sub">项目完成率</text>';
}

/* ---------------- 左侧分类面板 ---------------- */
function renderSidebar() {
  const list = $('#sidebarList');
  let html =
    '<div class="sb-all ' + (selected === '全部' ? 'active' : '') + '" data-filter="全部">' +
    '<span class="sb-icon">全</span><span>全部项目</span>' +
    '<span class="sb-count">' + state.projects.length + ' 项</span></div>';

  CATEGORIES.forEach(function (c) {
    const tasks = state.projects.filter(function (p) { return p.category === c; });
    const done = tasks.filter(function (p) { return p.done; }).length;
    const color = colorOf(c);
    html +=
      '<div class="cat-block">' +
      '<div class="cat-head ' + (selected === c ? 'active' : '') + '" data-filter="' + c + '">' +
      '<span class="cat-dot" style="background:' + color + '"></span>' +
      '<span class="cat-name">' + c + '</span>' +
      '<span class="cat-count">' + done + '/' + tasks.length + '</span>' +
      '<button class="cat-add" data-addcat="' + c + '" title="新增任务">＋ 新增</button>' +
      '</div>' +
      '<div class="cat-tasks">';
    if (!tasks.length) {
      html += '<div class="cat-empty">暂无任务，点「＋ 新增」添加</div>';
    } else {
      tasks.forEach(function (p) {
        html +=
          '<div class="cat-task ' + (p.done ? 'done' : '') + '" data-task="' + p.id + '">' +
          (p.done ? '<span class="task-check">✓</span>' : '') +
          '<span class="task-name">' + esc(p.name) + '</span>' +
          '<span class="task-date">' + fmtShortDate(p.start) + '~' + fmtShortDate(p.end) + '</span>' +
          '</div>';
      });
    }
    html += '</div></div>';
  });
  list.innerHTML = html;
}

/* ---------------- 时间范围 ---------------- */
function computeRange() {
  let s = parseDate(state.settings.start_date);
  let e = parseDate(state.settings.end_date);
  if (state.settings.auto_range && state.projects.length) {
    let ms = null, me = null;
    state.projects.forEach(function (p) {
      const ds = parseDate(p.start), de = parseDate(p.end);
      if (ds && (!ms || ds < ms)) ms = ds;
      if (de && (!me || de > me)) me = de;
    });
    if (ms && me) { s = ms; e = me; }
  }
  if (!s || !e || e < s) {
    const today = new Date();
    s = s || today;
    if (!e || e < s) e = new Date(s.getTime() + 30 * 86400000);
  }
  return { start: s, end: e };
}

function renderRange() {
  const range = computeRange();
  const startEl = $('#startDate');
  const endEl = $('#endDate');
  const autoEl = $('#autoRange');
  autoEl.checked = !!state.settings.auto_range;
  startEl.value = toISO(range.start);
  endEl.value = toISO(range.end);
  const days = diffDays(range.start, range.end) + 1;
  const hint = state.settings.auto_range
    ? '共 ' + days + ' 天（自动跟随项目起止日期）'
    : '共 ' + days + ' 天（手动范围）';
  $('#rangeHint').textContent = hint;
}

/* ---------------- 表格 ---------------- */
function renderTable() {
  const tb = $('#tbody');
  const list = filterProjects();
  if (!list.length) {
    const msg = selected === '全部'
      ? '暂无项目，点击右上角「＋ 添加项目」开始制定工作计划'
      : '「' + selected + '」暂无任务，点击左侧该分类的「＋ 新增」添加';
    tb.innerHTML = '<tr class="empty-row"><td colspan="7">' + msg + '</td></tr>';
    return;
  }
  let html = '';
  list.forEach(function (p, i) {
    const color = colorOf(p.category);
    const isSource = !!(p.source_table && p.source_id !== null && p.source_id !== undefined);
    // 操作列：源项目不显示删除按钮（源项目在对应业务页面删除）；只读模式禁用所有操作
    let opHtml = '';
    if (editable) {
      opHtml += '<button data-action="edit">编辑</button>';
      if (!isSource) {
        opHtml += '<button data-action="del" class="danger">删除</button>';
      } else {
        opHtml += '<span class="source-tag" title="来源于业务系统，不可在此删除">源</span>';
      }
    } else {
      opHtml += '<span class="readonly-tag">只读</span>';
    }
    // 完成状态切换：只读模式禁用
    const doneBtn = editable
      ? '<button class="done-toggle ' + (p.done ? 'on' : '') + '" data-action="toggle" title="点击切换完成状态">' + (p.done ? '☑' : '☐') + '</button>'
      : '<span class="done-toggle ' + (p.done ? 'on' : '') + '">' + (p.done ? '☑' : '☐') + '</span>';
    html +=
      '<tr data-id="' + p.id + '">' +
      '<td class="c-no">' + (i + 1) + '</td>' +
      '<td class="c-done">' + doneBtn + '</td>' +
      '<td class="c-name ' + (p.done ? 'done-text' : '') + '">' + esc(p.name) + '</td>' +
      '<td class="c-date">' + fmtDate(p.start) + '</td>' +
      '<td class="c-date">' + fmtDate(p.end) + '</td>' +
      '<td class="c-type"><span class="type-badge" style="--t:' + color + '">' + p.category + '</span></td>' +
      '<td class="c-op">' + opHtml + '</td>' +
      '</tr>';
  });
  tb.innerHTML = html;
}

/* ---------------- 甘特图 ---------------- */
function renderGantt() {
  const DAY_W = unitDayW();
  const range = computeRange();
  const days = diffDays(range.start, range.end) + 1;
  const totalW = days * DAY_W;
  const today = new Date();
  const rangeStartMid = new Date(range.start.getFullYear(), range.start.getMonth(), range.start.getDate());
  const rangeEndMid = new Date(range.end.getFullYear(), range.end.getMonth(), range.end.getDate());
  const todayIdx = (today >= rangeStartMid && today <= rangeEndMid) ? diffDays(range.start, today) : -1;
  const list = filterProjects();

  // 日期轴
  let axisHtml = '';
  for (let i = 0; i < days; i++) {
    const d = new Date(range.start.getTime() + i * 86400000);
    const isMonthStart = d.getDate() === 1;
    const isWeekend = d.getDay() === 0 || d.getDay() === 6;
    const label = isMonthStart ? (d.getMonth() + 1) + '/' + d.getDate() : d.getDate();
    let cls = 'axis-cell';
    if (isWeekend) cls += ' weekend';
    if (isMonthStart) cls += ' month-start';
    axisHtml += '<div class="' + cls + '" style="width:' + DAY_W + 'px">' + label + '</div>';
  }
  const axis = $('#ganttAxis');
  axis.innerHTML = axisHtml;
  axis.style.width = totalW + 'px';

  // 行与条
  let bodyHtml = '';
  list.forEach(function (p) {
    const ds = parseDate(p.start), de = parseDate(p.end);
    let left = 0, w = 0;
    if (ds && de) {
      left = Math.max(0, diffDays(range.start, ds)) * DAY_W + 2;
      w = (diffDays(ds, de) + 1) * DAY_W - 4;
      if (left + w > totalW) w = totalW - left - 2;
      if (w < 6) w = 6;
    }
    const color = colorOf(p.category);
    const doneMark = p.done ? '<span class="bar-check">✓</span>' : '';
    bodyHtml +=
      '<div class="gantt-row" data-id="' + p.id + '">' +
      '<div class="bar ' + (p.done ? 'done' : 'todo') + '" style="left:' + left + 'px;width:' + w + 'px;--bar:' + color + '" ' +
      'title="' + esc(p.name) + '（' + fmtDate(p.start) + ' ~ ' + fmtDate(p.end) + '）' + (p.done ? ' 已完成' : '') + '">' +
      doneMark +
      '</div></div>';
  });
  const rows = $('#ganttRows');
  rows.innerHTML = bodyHtml;
  rows.style.width = totalW + 'px';

  // 今日线
  const inner = $('#ganttInner');
  const old = inner.querySelectorAll('.today-line');
  old.forEach(function (el) { el.remove(); });
  if (todayIdx >= 0) {
    const line = document.createElement('div');
    line.className = 'today-line';
    line.style.left = (todayIdx * DAY_W + DAY_W / 2) + 'px';
    line.title = '今天';
    inner.appendChild(line);
  }
}

/* ---------------- 弹窗 ---------------- */
function openModal(id) {
  editingId = (id === undefined || id === null) ? null : id;
  $('#modalTitle').textContent = editingId === null ? '添加项目' : '编辑项目';
  if (editingId === null) {
    $('#fName').value = '';
    $('#fStart').value = '';
    $('#fEnd').value = '';
    $('#fDone').checked = false;
    // 分类默认跟随当前筛选
    $('#fCategory').value = (selected && selected !== '全部') ? selected : '产品研发情况';
    const range = computeRange();
    $('#fStart').value = toISO(range.start);
    $('#fEnd').value = toISO(range.end);
  } else {
    const p = state.projects.find(function (x) { return x.id === editingId; });
    if (!p) return;
    $('#fName').value = p.name;
    $('#fStart').value = p.start;
    $('#fEnd').value = p.end;
    $('#fCategory').value = p.category;
    $('#fDone').checked = !!p.done;
  }
  $('#modalMask').hidden = false;
  setTimeout(function () { $('#fName').focus(); }, 50);
}

function closeModal() {
  $('#modalMask').hidden = true;
  editingId = null;
}

function saveForm() {
  const name = $('#fName').value.trim();
  const start = $('#fStart').value;
  const end = $('#fEnd').value;
  const category = $('#fCategory').value;
  const done = $('#fDone').checked;

  if (!name) { alert('请填写工作项目名称'); return; }
  if (!start || !end) { alert('请填写开始日期和结束日期'); return; }
  const ds = parseDate(start), de = parseDate(end);
  if (!ds || !de) { alert('日期格式不正确'); return; }
  if (de < ds) { alert('结束日期不能早于开始日期'); return; }

  if (editingId === null) {
    let maxId = 0;
    state.projects.forEach(function (p) { if (p.id > maxId) maxId = p.id; });
    state.projects.push({ id: maxId + 1, name: name, start: toISO(ds), end: toISO(de), category: category, done: done });
  } else {
    const p = state.projects.find(function (x) { return x.id === editingId; });
    if (p) {
      p.name = name; p.start = toISO(ds); p.end = toISO(de); p.category = category; p.done = done;
    }
  }
  closeModal();
  renderAll();
  saveData();
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  $('#btnAdd').addEventListener('click', function () { openModal(null); });
  $('#btnCancel').addEventListener('click', closeModal);
  $('#btnSave').addEventListener('click', saveForm);

  $('#modalMask').addEventListener('click', function (e) {
    if (e.target === $('#modalMask')) closeModal();
  });
  // 弹窗内按回车直接保存
  $('#modalMask').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT' && !$('#modalMask').hidden) {
      e.preventDefault();
      saveForm();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !$('#modalMask').hidden) closeModal();
  });

  // 使用教程
  $('#btnTutorial').addEventListener('click', function () {
    window.open('/gantt_tool/tutorial.html', '_blank');
  });

  // 左侧分类面板：切换筛选 / 新增任务
  $('#sidebarList').addEventListener('click', function (e) {
    const addBtn = e.target.closest('.cat-add');
    if (addBtn) {
      openModal(null);
      $('#fCategory').value = addBtn.getAttribute('data-addcat');
      return;
    }
    const taskEl = e.target.closest('.cat-task');
    if (taskEl) {
      const id = Number(taskEl.getAttribute('data-task'));
      const p = state.projects.find(function (x) { return x.id === id; });
      if (p) {
        if (selected !== p.category) {
          selected = p.category;
          renderAll();
        }
        highlightRow(id);
      }
      return;
    }
    const head = e.target.closest('[data-filter]');
    if (head) {
      selected = head.getAttribute('data-filter');
      renderAll();
    }
  });

  // 统计卡片点击切换筛选
  $('#statCards').addEventListener('click', function (e) {
    const card = e.target.closest('.stat[data-filter]');
    if (card) {
      selected = card.getAttribute('data-filter');
      renderAll();
    }
  });

  // 表格：完成状态、编辑、删除（事件委托）
  $('#tbody').addEventListener('click', function (e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const id = Number(tr.getAttribute('data-id'));
    const action = btn.getAttribute('data-action');
    const p = state.projects.find(function (x) { return x.id === id; });
    if (!p) return;
    if (action === 'toggle') {
      p.done = !p.done;
      renderAll();
      saveData(true);
    } else if (action === 'edit') {
      openModal(id);
    } else if (action === 'del') {
      if (confirm('确定删除项目「' + p.name + '」吗？')) {
        state.projects = state.projects.filter(function (x) { return x.id !== id; });
        renderAll();
        saveData(true);
      }
    }
  });

  // 时间范围手动修改
  $('#startDate').addEventListener('change', function () {
    const d = parseDate(this.value);
    if (!d) return;
    state.settings.start_date = toISO(d);
    state.settings.auto_range = false;
    renderAll();
    saveData(true);
  });
  $('#endDate').addEventListener('change', function () {
    const d = parseDate(this.value);
    if (!d) return;
    state.settings.end_date = toISO(d);
    state.settings.auto_range = false;
    renderAll();
    saveData(true);
  });
  $('#autoRange').addEventListener('change', function () {
    state.settings.auto_range = this.checked;
    if (this.checked) {
      state.settings.start_date = '';
      state.settings.end_date = '';
    }
    renderAll();
    saveData(true);
  });

  // 导出（下拉选择 JSON / Excel）
  $('#btnExport').addEventListener('click', function (e) {
    e.stopPropagation();
    $('#exportMenu').hidden = !$('#exportMenu').hidden;
  });
  $('#exportMenu').addEventListener('click', function (e) {
    const b = e.target.closest('button[data-fmt]');
    if (!b) return;
    const fmt = b.getAttribute('data-fmt');
    $('#exportMenu').hidden = true;
    window.location.href = (fmt === 'excel') ? '/api/gantt/export?fmt=excel' : '/api/gantt/export';
    toast('正在导出 ' + (fmt === 'excel' ? 'Excel' : 'JSON') + ' 文件…');
  });
  document.addEventListener('click', function () { $('#exportMenu').hidden = true; });

  // 导入（支持 JSON / Excel .xlsx）
  $('#btnImport').addEventListener('click', function () { $('#importFile').click(); });
  $('#importFile').addEventListener('change', function () {
    const file = this.files[0];
    this.value = '';
    if (!file) return;

    // Excel 文件：二进制 -> base64 -> 上传服务端解析
    if (/\.xlsx$/i.test(file.name)) {
      const reader = new FileReader();
      reader.onload = function () {
        if (!confirm('导入将覆盖当前全部数据，确定继续吗？')) return;
        const bytes = new Uint8Array(reader.result);
        let bin = '';
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        const b64 = btoa(bin);
        fetch('/api/gantt/import_excel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: b64, name: file.name })
        }).then(function (res) { return res.json(); }).then(function (j) {
          if (j.ok) {
            state = j.data;
            if (!state.settings) state.settings = { start_date: '', end_date: '', auto_range: true };
            if (!state.projects) state.projects = [];
            selected = '全部';
            renderAll();
            toast('Excel 导入成功');
          } else {
            alert('导入失败：' + (j.error || '未知错误'));
          }
        }).catch(function () { alert('导入失败：无法连接服务器'); });
      };
      reader.onerror = function () { alert('读取 Excel 文件失败'); };
      reader.readAsArrayBuffer(file);
      return;
    }

    // JSON 文件：原流程
    const reader = new FileReader();
    reader.onload = function () {
      let raw;
      try {
        raw = JSON.parse(reader.result);
      } catch (err) {
        alert('导入失败：文件不是有效的 JSON 格式（如需 Excel 请选择 .xlsx 文件）');
        return;
      }
      if (!confirm('导入将覆盖当前全部数据，确定继续吗？')) return;
      fetch('/api/gantt/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(raw)
      }).then(function (res) { return res.json(); }).then(function (j) {
        if (j.ok) {
          state = j.data;
          if (!state.settings) state.settings = { start_date: '', end_date: '', auto_range: true };
          if (!state.projects) state.projects = [];
          selected = '全部';
          renderAll();
          toast('导入成功');
        } else {
          alert('导入失败：' + (j.error || '未知错误'));
        }
      }).catch(function () { alert('导入失败：无法连接服务器'); });
    };
    reader.readAsText(file, 'utf-8');
  });

  // 示例 / 清空
  $('#btnDemo').addEventListener('click', function () {
    if (!confirm('将使用示例数据替换当前全部数据，确定吗？')) return;
    fetch('/api/gantt/reset?mode=demo').then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { state = j.data; selected = '全部'; renderAll(); toast('已加载示例数据'); }
    });
  });
  $('#btnClear').addEventListener('click', function () {
    if (!confirm('确定清空全部项目吗？此操作会覆盖当前数据（建议先导出备份）。')) return;
    fetch('/api/gantt/reset?mode=empty').then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { state = j.data; selected = '全部'; renderAll(); toast('已清空'); }
    });
  });
}

/* 高亮表格/甘特图某一行 */
function highlightRow(id) {
  const tr = document.querySelector('#tbody tr[data-id="' + id + '"]');
  const gr = document.querySelector('.gantt-row[data-id="' + id + '"]');
  if (tr) {
    tr.classList.add('active-row');
    if (tr.scrollIntoView) tr.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  if (gr) gr.classList.add('active-row');
  setTimeout(function () {
    if (tr) tr.classList.remove('active-row');
    if (gr) gr.classList.remove('active-row');
  }, 1600);
}

/* ---------------- 汇总渲染 ---------------- */
function renderAll() {
  renderRange();
  renderSidebar();
  renderStats();
  renderDonut();
  renderTable();
  renderGantt();
  updateHbar();
}

/* ---------------- 固定底部横向滚动条 ---------------- */
let hbarBound = false;

function updateHbar() {
  const flex = document.querySelector('.board-flex');
  const hbar = document.getElementById('boardHbar');
  const thumb = document.getElementById('boardHbarThumb');
  if (!flex || !hbar || !thumb) return;

  const cw = hbar.clientWidth;
  const sw = flex.scrollWidth;
  const visible = flex.clientWidth;
  if (sw <= visible + 1) {
    thumb.style.display = 'none';
    return;
  }
  thumb.style.display = 'block';
  const tw = Math.max(cw * visible / sw, 40);
  const maxLeft = cw - tw;
  const maxScroll = sw - visible;
  const ratio = maxScroll > 0 ? flex.scrollLeft / maxScroll : 0;
  thumb.style.width = tw + 'px';
  thumb.style.left = (ratio * maxLeft) + 'px';

  if (!hbarBound) {
    hbarBound = true;

    // 甘特图横向滚动时同步轨道
    flex.addEventListener('scroll', function () {
      const maxS = flex.scrollWidth - flex.clientWidth;
      const maxL = hbar.clientWidth - parseFloat(thumb.style.width || tw);
      const r = maxS > 0 ? flex.scrollLeft / maxS : 0;
      thumb.style.left = (r * maxL) + 'px';
    });

    // 拖动轨道滑块
    let dragging = false, startX = 0, startLeft = 0;
    thumb.addEventListener('mousedown', function (e) {
      e.preventDefault();
      dragging = true;
      startX = e.clientX;
      startLeft = flex.scrollLeft;
      document.body.style.cursor = 'grabbing';
    });
    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      const maxS = flex.scrollWidth - flex.clientWidth;
      const maxL = hbar.clientWidth - parseFloat(thumb.style.width || tw);
      const dx = e.clientX - startX;
      flex.scrollLeft = startLeft + (maxL > 0 ? dx / maxL : 0) * maxS;
    });
    document.addEventListener('mouseup', function () {
      dragging = false;
      document.body.style.cursor = '';
    });

    // 点击轨道空白处跳转
    hbar.addEventListener('click', function (e) {
      if (e.target === thumb) return;
      const rect = hbar.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const maxS = flex.scrollWidth - flex.clientWidth;
      const maxL = hbar.clientWidth - parseFloat(thumb.style.width || tw);
      const r = maxL > 0 ? (x - (parseFloat(thumb.style.width || tw) / 2)) / maxL : 0;
      flex.scrollLeft = Math.max(0, Math.min(1, r)) * maxS;
    });

    // 触摸设备支持（拖 thumb / 滑轨道）
    hbar.addEventListener('touchstart', function (e) {
      const t = e.touches[0];
      const rect = hbar.getBoundingClientRect();
      const x = t.clientX - rect.left;
      const maxS = flex.scrollWidth - flex.clientWidth;
      const maxL = hbar.clientWidth - parseFloat(thumb.style.width || tw);
      const r = maxL > 0 ? (x - (parseFloat(thumb.style.width || tw) / 2)) / maxL : 0;
      flex.scrollLeft = Math.max(0, Math.min(1, r)) * maxS;
    }, { passive: true });

    window.addEventListener('resize', updateHbar);
  }
}

/* ---------------- Toast ---------------- */
let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.classList.remove('show'); }, 1800);
}

/* ---------------- 启动 ---------------- */
// 窗口尺寸变化（含根字号等比变化）后重绘，保证甘特图对齐
let resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.projects) renderAll(); }, 150);
});

bindEvents();
loadData();
