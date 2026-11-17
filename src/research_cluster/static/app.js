"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const paths = {
  dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  flask:'<path d="M9 3h6m-5 0v6L4.8 18a2 2 0 0 0 1.8 3h10.8a2 2 0 0 0 1.8-3L14 9V3M8 13h8"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 17l9 5 9-5"/>',
  server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3M15 17.5h3"/>',
  activity:'<path d="M2 12h4l3-8 6 16 3-8h4"/>',
  settings:'<path d="m10 3-1 3-3 1-3-1-1 4 3 2v3l-2 2 3 3 3-1 3 2 2-3h3l2 1 3-4-2-2v-3l1-2-3-3-3 1-2-3Z"/><circle cx="12" cy="12" r="3"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  refresh:'<path d="M20 7a8 8 0 1 0 1 8M20 3v5h-5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 1v5m6-5v5M9 18v5m6-5v5M1 9h5m-5 6h5m12-6h5m-5 6h5M9 9h6v6H9z"/>',
  gpu:'<rect x="2" y="5" width="20" height="13" rx="2"/><circle cx="9" cy="11.5" r="3.5"/><path d="M16 9h3m-3 4h3M6 18v3m4-3v3m4-3v3"/>',
  chart:'<path d="M3 3v18h18M6 15l5-6 4 3 6-7"/>',
  pause:'<path d="M8 5v14M16 5v14"/>',
  play:'<path d="m7 4 14 8-14 8V4Z"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.layers}</svg>`;
$$('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {active:'研究中',paused:'已暂停',completed:'已完成',failed:'失败',canceled:'已取消',queued:'排队中',running:'运行中',succeeded:'成功',cancel_requested:'正在取消',draft:'待评审',accepted:'已采纳',deferred:'已暂缓',queued_literature:'待调研',collecting:'检索论文',synthesizing:'综合任务',awaiting_review:'待评审',execution_ready:'实验阶段',error:'调研失败'};
const domainNames = {ml:'算法 / 模型',finance:'金融研发',general:'通用研发'};
const titles = {overview:'工作台',studies:'研究项目',jobs:'实验队列',resources:'计算资源',literature:'文献与任务',tree:'研究树',verification:'形式验证',events:'活动记录',settings:'连接与配置'};
const badge = status => `<span class="badge ${esc(status)}">${esc(labels[status] || status)}</span>`;
const kindTag = kind => `<span class="kind-tag ${esc(kind)}">${esc(kind.toUpperCase())}</span>`;
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-US',{maximumFractionDigits:4});
const ago = timestamp => { if(!timestamp) return '—'; const d = Math.max(0,Date.now()/1000-timestamp); return d < 60 ? '刚刚' : d < 3600 ? `${Math.floor(d/60)} 分钟前` : d < 86400 ? `${Math.floor(d/3600)} 小时前` : `${Math.floor(d/86400)} 天前`; };
const date = timestamp => timestamp ? new Date(timestamp*1000).toLocaleString('zh-CN',{hour12:false}) : '—';
const empty = (message, note='', action='') => `<div class="empty">${icon('flask')}<span>${esc(message)}</span>${note?`<small>${esc(note)}</small>`:''}${action}</div>`;
const terminal = status => ['succeeded','failed','canceled'].includes(status);
let state = {studies:[],jobs:[],pools:[],workers:[],events:[]};
let health = {}, page = 'overview', loading = false, activeJob = null, chartStudy = '', search = '', filters = {status:'',kind:''};
let token = localStorage.getItem('forge-token') || '';

async function api(path, options={}) {
  const response = await fetch('/api'+path, {...options, headers:{...(options.body && !(options.body instanceof Blob) ? {'Content-Type':'application/json'} : {}), ...(token?{Authorization:`Bearer ${token}`} : {}), ...options.headers}});
  if(!response.ok) {
    let value; try { value = await response.json(); } catch { value = {}; }
    if(response.status === 401) connectDialog();
    const detail = Array.isArray(value.detail) ? value.detail.map(x=>x.msg).join('；') : value.detail;
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

function toast(message, error=false) {
  const el = document.createElement('div'); el.className = `toast${error?' error':''}`; el.textContent = message;
  $('#toasts').append(el); setTimeout(()=>el.remove(),5000);
}

async function refresh() {
  if(loading) return;
  loading = true;
  try {
    state = await api('/state');
    $('#connection-dot').classList.remove('offline'); $('#connection-label').textContent = '控制面已连接';
    $('#study-count').textContent = state.studies.filter(x=>x.status==='active').length;
    $('#job-count').textContent = state.jobs.filter(x=>!terminal(x.status)).length;
    $('#last-updated').textContent = `最近同步 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})} · 每 3 秒更新`;
    if(page==='tree')await refreshResearchTreeData();
    render();
    if(activeJob) await refreshDrawer();
  } catch(error) {
    $('#connection-dot').classList.add('offline'); $('#connection-label').textContent = '控制面未连接';
    if(!state.server_time) $('#main').innerHTML = `<div class="banner-error">${esc(error.message)}<br>确认控制服务已启动，或在连接设置中填写访问令牌。</div><button class="button" data-action="connect">连接控制面</button>`;
  } finally { loading = false; }
}

function heading(title, description, actions=true) {
  return `<div class="page-heading"><div><span class="eyebrow">RESEARCH / ${esc(page.toUpperCase())}</span><h1>${title}</h1><p>${description}</p></div>${actions?`<div class="heading-actions"><button class="button ghost" data-action="new-job">${icon('layers')}提交实验</button><button class="button primary" data-action="new-study">${icon('plus')}新建研究</button></div>`:''}</div>`;
}

function poolUsage(kind, poolId=null) {
  const workers = state.workers.filter(w=>w.kind===kind && w.online && (!poolId || w.pool_id===poolId));
  const ids = new Set(workers.map(w=>w.id));
  const jobs = state.jobs.filter(j=>['running','cancel_requested'].includes(j.status) && ids.has(j.worker_id));
  return {workers, jobs, cpus:workers.reduce((n,w)=>n+w.cpus,0), usedCpus:jobs.reduce((n,j)=>n+j.spec.resources.cpus,0),
    gpus:workers.reduce((n,w)=>n+w.gpu_ids.length,0), usedGpus:jobs.reduce((n,j)=>n+j.spec.resources.gpus,0),
    memory:workers.reduce((n,w)=>n+w.memory_mb,0),usedMemory:jobs.reduce((n,j)=>n+j.spec.resources.memory_mb,0)};
}

function resourceBlock(kind) {
  const p = poolUsage(kind), total = kind==='cpu'?p.cpus:p.gpus, used = kind==='cpu'?p.usedCpus:p.usedGpus;
  const queued = state.jobs.filter(j=>j.kind===kind&&j.status==='queued').length;
  const hardware = state.hardware || {}, count = kind==='cpu'?hardware.cpu_logical:hardware.gpu_count;
  const description = kind==='cpu' ? `${fmt(hardware.cpu_physical)} 个物理核` : (hardware.gpu_complete?'GPU UUID 去重':'部分主机尚未完成探测');
  return `<div class="resource-block ${kind}"><div class="resource-top"><span class="resource-name ${kind}">${icon(kind)}${kind.toUpperCase()} 硬件</span><strong data-testid="overview-${kind}-count">${count==null?'未探测':fmt(count)} <small>${kind==='cpu'?'逻辑 CPU':'张 GPU'}</small></strong></div><div class="resource-meta" style="margin-top:10px"><span>${esc(description)}</span><span>${p.workers.length} 个执行节点</span></div><div class="meter"><span style="width:${total?Math.min(100,used/total*100):0}%"></span></div><div class="resource-meta"><span>平台占用 ${used} / 已接入 ${total} ${kind==='cpu'?'CPU':'GPU'}</span><span>${queued} 个排队</span></div></div>`;
}

function hardwarePanel() {
  const hardware = state.hardware || {}, nodes = hardware.nodes || [];
  return `<section class="panel hardware-panel"><div class="panel-head"><div><h2>硬件清单</h2><small>主机与 GPU UUID 去重；硬件数量、容器限制和执行配额分别统计</small></div><span class="text-link">${hardware.last_seen?esc(ago(hardware.last_seen)):'等待监控上报'}</span></div><div class="hardware-grid"><div class="hardware-stat"><span>${icon('cpu')} 主机逻辑 CPU</span><strong data-testid="hardware-cpu-total">${fmt(hardware.cpu_logical)}</strong><small>${fmt(hardware.cpu_physical)} 个物理核 · ${nodes.length} 台已探测主机</small></div><div class="hardware-stat gpu"><span>${icon('gpu')} 已探测 GPU</span><strong data-testid="hardware-gpu-total">${hardware.gpu_count==null?'未探测':fmt(hardware.gpu_count)}</strong><small>${hardware.gpu_complete?'已完成当前主机清单探测':'有主机尚未上报 GPU 信息'} · 接入配额在下方单列</small></div></div>${nodes.length?`<div class="inventory-nodes">${nodes.map(node=>`<div class="inventory-node"><div class="inventory-node-head"><h3>${icon('server')} ${esc(node.hostname)}</h3><span class="badge active">监控在线</span></div><div class="inventory-facts"><span>逻辑 CPU <b>${fmt(node.cpu_logical)}</b></span><span>物理核 <b>${fmt(node.cpu_physical)}</b></span><span>已探测 GPU <b>${node.gpu_known?node.gpus.length:'未探测'}</b></span></div>${node.scopes.map(scope=>`<p class="inventory-limit" data-testid="hardware-cpu-limit">环境 CPU affinity：${fmt(scope.cpu_affinity)} · 容器 CPU 配额：${scope.cpu_quota==null?'未限制':fmt(scope.cpu_quota)} · 当前环境可用上限：${fmt(scope.cpu_effective)} CPU</p>`).join('')}${node.gpus.length?`<div class="device-grid">${node.gpus.map(gpu=>`<span class="device-chip" title="${esc(gpu.uuid)}">${icon('gpu')} #${esc(gpu.index)} ${esc(gpu.name)}<small>${gpu.memory_mb==null?'显存未知':fmt(gpu.memory_mb/1024)+' GiB'}</small></span>`).join('')}</div>`:!node.gpu_known?`<p class="inventory-limit">GPU 探测未就绪：${esc(node.gpu_errors[0] || '可运行独立 monitor 上报 GPU 清单')}</p>`:''}</div>`).join('')}</div>`:empty('尚无硬件清单','启动 research-cluster monitor --url '+location.origin+'，只读探测不会领取实验任务。')}</section>`;
}

function chart() {
  const study = state.studies.find(s=>s.id===chartStudy) || state.studies[0];
  if(study) chartStudy=study.id;
  const metric = study?.config.metric || 'score';
  const jobs = state.jobs.filter(j=>j.study_id===study?.id && j.status==='succeeded' && j.metrics[metric]!=null).sort((a,b)=>a.round-b.round);
  if(!jobs.length) return `<div class="chart-empty">${icon('chart')}<span>实验完成后，这里将展示真实评测曲线</span><button class="button small ghost" data-action="benchmark">运行 CPU 基准研究 ${icon('arrow')}</button></div>`;
  const values = jobs.map(j=>j.metrics[metric]);
  let low = Math.min(...values), high = Math.max(...values);
  const span = high-low || Math.max(Math.abs(high)*.1,1); low-=span*.2; high+=span*.2;
  const width = document.documentElement.clientWidth <= 650 ? 330 : 660;
  const x = i => 45+(jobs.length===1?0:i/(jobs.length-1))*(width-70), y = v=>160-(v-low)/(high-low)*140;
  const points = jobs.map((j,i)=>`${x(i)},${y(values[i])}`).join(' ');
  const area = `M ${x(0)} 160 L ${jobs.map((j,i)=>`${x(i)} ${y(values[i])}`).join(' L ')} L ${x(jobs.length-1)} 160 Z`;
  return `<div class="chart-legend"><span><i class="legend-dot"></i>${esc(metric)} · ${esc(study.config.direction==='max'?'越高越好':'越低越好')}</span><span>${jobs.length} 次有效实验</span></div><svg class="chart" viewBox="0 0 ${width} 190" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(metric)} 评测曲线"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#6de2bb" stop-opacity=".17"/><stop offset="100%" stop-color="#6de2bb" stop-opacity="0"/></linearGradient></defs>${[0,1,2,3].map(i=>{const v=low+(high-low)*i/3;return `<line class="grid" x1="45" x2="${width-20}" y1="${y(v)}" y2="${y(v)}"/><text x="35" y="${y(v)+3}" text-anchor="end">${fmt(v)}</text>`;}).join('')}<path d="${area}" fill="url(#chart-fill)"/><polyline points="${points}" fill="none" stroke="#6de2bb" stroke-width="2"/>${jobs.map((j,i)=>`<circle cx="${x(i)}" cy="${y(values[i])}" r="3.5" fill="#6de2bb" stroke="#122c25" stroke-width="3"><title>第 ${j.round} 轮 · ${esc(metric)}: ${fmt(values[i])}</title></circle><text x="${x(i)}" y="183" text-anchor="middle">#${String(j.round).padStart(2,'0')}</text>`).join('')}</svg>`;
}

function studyTable(studies) {
  if(!studies.length) return empty(search?'没有匹配的研究':'开始你的第一项研究','创建研究后，系统会生成候选方案、提交实验、比较指标，并记录每一轮结果。','<button class="button small primary" data-action="new-study">新建研究</button>');
  return `<div class="table-wrap"><table><thead><tr><th>研究项目</th><th>状态</th><th>实验进度</th><th>最佳指标</th><th>计算资源</th><th>操作</th></tr></thead><tbody>${studies.map(s=>{
    const jobs=state.jobs.filter(j=>j.study_id===s.id), count=jobs.filter(j=>terminal(j.status)).length;
    return `<tr data-study="${s.id}"><td><div class="cell-title">${icon('flask')}${esc(s.name)}</div><div class="cell-sub">${esc(domainNames[s.domain])} · ${s.mode==='benchmark'?'可复现基准':'文献驱动研究'}</div></td><td>${badge(s.status==='active'&&s.config.literature?s.config.literature.phase:s.status)}</td><td><div class="progress-row"><div class="progress"><span style="width:${count/s.rounds*100}%"></span></div>${count} / ${s.rounds}</div></td><td><span class="score mono">${fmt(s.best_score)}</span></td><td>${kindTag(s.config.kind)}</td><td>${['active','paused'].includes(s.status)?`<button class="icon-button" title="${s.status==='active'?'暂停研究':'恢复研究'}" data-action="study-${s.status==='active'?'pause':'resume'}" data-id="${s.id}">${icon(s.status==='active'?'pause':'play')}</button><button class="icon-button" title="取消研究" data-action="study-cancel" data-id="${s.id}">${icon('close')}</button>`:`<span class="text-link">查看实验 →</span>`}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function eventsList(events) {
  if(!events.length) return empty('暂无活动记录','研究、实验与执行节点的变化会记录在这里。');
  return `<div class="event-list">${events.map(e=>`<div class="event-item"><span class="event-symbol">${icon(e.kind.includes('failed')||e.kind.includes('error')?'close':e.kind.includes('running')?'play':e.kind.includes('promoted')?'chart':'check')}</span><div><div class="event-message">${esc(e.message)}</div><div class="event-time">${esc(ago(e.created_at))} · ${esc(e.kind)}</div></div></div>`).join('')}</div>`;
}

function overview() {
  const succeeded=state.jobs.filter(j=>j.status==='succeeded').length, finished=state.jobs.filter(j=>['succeeded','failed'].includes(j.status)).length;
  const active=state.studies.filter(s=>s.status==='active').length, running=state.jobs.filter(j=>j.status==='running').length, queued=state.jobs.filter(j=>j.status==='queued').length;
  const cpu=poolUsage('cpu'), gpu=poolUsage('gpu');
  return heading('研究工作台','管理研究循环、追踪实验结果，独立调度每一份计算资源。') +
  `<div class="stats"><div class="stat"><div class="stat-title">进行中的研究 ${icon('flask')}</div><div class="stat-value">${active}<small>项</small></div><div class="stat-sub"><em>${state.studies.filter(s=>s.status==='completed').length}</em> 项研究已完成</div></div><div class="stat"><div class="stat-title">正在执行的实验 ${icon('layers')}</div><div class="stat-value">${running}<small>个</small></div><div class="stat-sub"><span class="status-dot"></span>${queued} 个实验等待资源</div></div><div class="stat"><div class="stat-title">实验成功率 ${icon('chart')}</div><div class="stat-value">${finished?Math.round(succeeded/finished*100):'—'}<small>${finished?'%':''}</small></div><div class="stat-sub">基于 ${finished} 次已结束实验</div></div><div class="stat"><div class="stat-title">在线执行节点 ${icon('server')}</div><div class="stat-value">${cpu.workers.length+gpu.workers.length}<small>个</small></div><div class="stat-sub">已探测 <em>${fmt(state.hardware?.cpu_logical)}</em> 逻辑 CPU · <em>${fmt(state.hardware?.gpu_count)}</em> GPU</div></div></div>
  <div class="grid-main"><section class="panel"><div class="panel-head"><div><h2>实验表现</h2><small>每一轮改进，都有评测依据</small></div><select class="chart-select" id="chart-study" aria-label="选择曲线对应的研究">${state.studies.length?state.studies.map(s=>`<option value="${s.id}" ${s.id===chartStudy?'selected':''}>${esc(s.name)}</option>`).join(''):'<option>暂无研究</option>'}</select></div><div class="chart-wrap">${chart()}</div></section><section class="panel"><div class="panel-head"><div><h2>硬件与执行配额</h2><small>主机探测 · 独立资源池</small></div><a class="text-link" href="#resources">管理 ${icon('arrow')}</a></div><div class="resource-summary">${resourceBlock('cpu')}${resourceBlock('gpu')}</div><div class="resource-foot"><span>CONTROL PLANE</span><span class="live-tag"><span class="status-dot"></span>CPU ONLINE</span></div></section></div>
  <div class="grid-main"><section class="panel"><div class="panel-head"><h2>最近的研究</h2><a class="text-link" href="#studies">全部研究 →</a></div>${studyTable(filteredStudies().slice(0,5))}</section><section class="panel"><div class="panel-head"><h2>最近活动</h2><a class="text-link" href="#events">查看全部 →</a></div>${eventsList(state.events.slice(0,5))}</section></div>`;
}

function filteredStudies() {return state.studies.filter(s=>(!search||`${s.name} ${s.objective}`.toLowerCase().includes(search.toLowerCase()))&&(!filters.status||page!=='studies'||s.status===filters.status));}

function studiesPage() {
  return heading('研究项目','从一个可验证的假设开始，持续积累候选方案和实验依据。')+`<div class="filters"><select id="status-filter" aria-label="研究状态"><option value="">全部状态</option>${['active','paused','completed','failed','canceled'].map(s=>`<option value="${s}" ${filters.status===s?'selected':''}>${labels[s]}</option>`).join('')}</select><span class="count">${filteredStudies().length} 项研究</span></div><section class="panel">${studyTable(filteredStudies())}</section>`;
}

function jobsPage() {
  const jobs=state.jobs.filter(j=>(!search||`${j.name} ${j.id}`.toLowerCase().includes(search.toLowerCase()))&&(!filters.status||j.status===filters.status)&&(!filters.kind||j.kind===filters.kind)&&(!filters.study||j.study_id===filters.study));
  return heading('实验队列','提交即返回，按资源池独立排队；打开实验查看实时日志和产物。')+`<div class="filters"><select id="status-filter" aria-label="实验状态"><option value="">全部状态</option>${['queued','running','succeeded','failed','canceled','cancel_requested'].map(s=>`<option value="${s}" ${filters.status===s?'selected':''}>${labels[s]}</option>`).join('')}</select><select id="kind-filter" aria-label="资源类型"><option value="">全部资源</option><option value="cpu" ${filters.kind==='cpu'?'selected':''}>CPU</option><option value="gpu" ${filters.kind==='gpu'?'selected':''}>GPU</option></select>${filters.study?'<button class="button small ghost" data-action="clear-study">清除研究筛选 ×</button>':''}<span class="count">${jobs.length} 个实验</span></div><section class="panel">${jobs.length?`<div class="table-wrap"><table><thead><tr><th>实验</th><th>状态</th><th>执行资源</th><th>指标</th><th>提交时间</th><th></th></tr></thead><tbody>${jobs.map(j=>`<tr data-job="${j.id}"><td><div class="cell-title">${icon('layers')}${esc(j.name)}</div><div class="cell-sub mono">${j.id.slice(0,12)} · attempt ${j.attempt}</div></td><td>${badge(j.status)}</td><td>${kindTag(j.kind)} <span class="mono">${j.spec.resources.cpus}c / ${j.spec.resources.memory_mb}M${j.kind==='gpu'?` / ${j.spec.resources.gpus} GPU`:''}</span></td><td><span class="score mono">${fmt(j.metrics.score ?? Object.values(j.metrics)[0])}</span></td><td>${esc(ago(j.created_at))}</td><td>${icon('arrow')}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无匹配的实验','创建研究可自动生成实验，也可以直接提交一段代码。','<button class="button small primary" data-action="new-job">提交实验</button>')}</section>`;
}

function workerCommand(pool) {
  return `research-cluster worker --url ${location.origin} --kind ${pool.kind} --pool ${pool.id}${pool.kind==='gpu'?' --gpu-ids 0':''} --cpus 4 --memory-mb 4096`;
}

function resourcesPage() {
  return heading('计算资源','CPU 与 GPU 分别管理配额和运行环境，通过任务协议连接控制面。',false).replace('</div></div>','</div><button class="button primary" data-action="new-pool">'+icon('plus')+'新建资源池</button></div>')+hardwarePanel()+`<div class="notice">下方数值是已接入执行池的调度配额，不等于硬件总量。暂停资源池会停止分发新任务，已运行的实验继续执行。GPU 无可用节点时，其任务独立排队，CPU 研究与管理台仍可正常工作。</div><div class="pool-grid">${state.pools.map(p=>{
    const usage=poolUsage(p.kind,p.id), allWorkers=state.workers.filter(w=>w.pool_id===p.id), queued=state.jobs.filter(j=>j.pool_id===p.id&&j.status==='queued').length;
    return `<section class="panel pool-card ${p.kind}"><div class="pool-title"><div><h2>${icon(p.kind)}${esc(p.name)}</h2><p class="pool-id mono">${esc(p.id)}</p></div><button class="button small ghost" data-action="toggle-pool" data-id="${esc(p.id)}">${icon(p.enabled?'pause':'play')}${p.enabled?'暂停调度':'恢复调度'}</button></div><div class="pool-stats"><div><strong>${usage.workers.length}</strong><span>在线节点</span></div><div><strong>${p.kind==='cpu'?usage.usedCpus:usage.usedGpus} <small style="color:var(--muted);font-size:12px">/ ${p.kind==='cpu'?usage.cpus:usage.gpus}</small></strong><span>${p.kind==='cpu'?'平台占用 / 已接入 CPU 配额':'平台占用 / 已接入 GPU 配额'}</span></div><div><strong>${queued}</strong><span>排队实验</span></div></div>${allWorkers.length?allWorkers.map(w=>`<div class="worker-row"><div><span class="status-dot ${w.online?'':'offline'}"></span> ${esc(w.name)}<small>执行配额 ${w.cpus} CPU · ${fmt(w.memory_mb/1024)} GiB${w.kind==='gpu'?` · ${w.gpu_ids.length} GPU`:''}</small></div><span class="badge ${w.online?'active':'canceled'}">${w.online?'在线':'离线'}</span></div>`).join(''):'<div class="worker-row"><span style="color:var(--muted)">尚未接入执行节点</span>'+kindTag(p.kind)+'</div>'}<div class="pool-command"><code>${esc(workerCommand(p))}</code><button class="icon-button" title="复制节点接入命令" data-action="copy-worker" data-id="${esc(p.id)}">${icon('copy')}</button></div></section>`;
  }).join('')}</div>`;
}

function settingsPage() {
  return heading('连接与配置','管理控制面连接，检查编排引擎与模型接入状态。',false)+`<div class="settings-grid"><section class="panel settings-card"><h2>控制面</h2><div class="config-row"><span>默认研究流程</span><b>论文检索 → 综合 → 任务评审 → 实验</b></div><div class="config-row"><span>服务地址</span><b class="mono">${esc(location.origin)}</b></div><div class="config-row"><span>编排引擎</span><b>${health.orchestrator==='temporal'?'Temporal':'Local · 持久化研究循环'}</b></div><div class="config-row"><span>数据库</span><b>${esc(health.database || '—')}</b></div><div class="config-row"><span>访问认证</span><b>${health.authentication?'已启用令牌':'本机访问模式'}</b></div><p>令牌仅保存在当前浏览器中。服务端配置通过环境变量管理。</p><button class="button small" data-action="connect">设置访问令牌</button></section><section class="panel settings-card"><h2>研究 Agent</h2><div class="config-row"><span>可复现 CPU 基准</span><b>可直接运行</b></div><div class="config-row"><span>OpenHands SDK</span><b>${health.openhands_installed?'已安装':'尚未安装'}</b></div><div class="config-row"><span>模型 API</span><b>${health.llm_configured?'已配置':'尚未配置'}</b></div><p>接入模型后，可在指定 Git 仓库中提出并实现候选方案。API 密钥只由研究进程读取，不会传给实验节点。</p><pre class="code-block">uv pip install -e '.[agent]'
export LLM_MODEL='your-provider/model'
export LLM_API_KEY='…'
# 可选：LLM_BASE_URL</pre></section><section class="panel settings-card"><h2>CPU 节点接入</h2><p>在 CPU 主机中安装本项目，然后启动独立执行节点。</p><pre class="code-block">${esc(workerCommand({id:'cpu-default',kind:'cpu'}))}</pre><p>native 模式用于可信代码；Docker 模式可设置单个作业的 CPU、内存和容器镜像。</p></section><section class="panel settings-card"><h2>GPU 节点接入</h2><p>GPU 主机单独维护驱动、训练镜像与设备清单。控制面无需安装 CUDA。</p><pre class="code-block">${esc(workerCommand({id:'gpu-default',kind:'gpu'}))} \
  --runtime docker --image your-training-image</pre><p>GPU 编号由执行主机配置，按任务租约独占分配。</p></section></div>`;
}

const synthesisNames = {model:'模型跨论文综合',assistant_reviewed:'助手核验综合',evidence_checklist:'规则核验草案（未做模型综合）'};
function literatureStudy(id) {return state.studies.find(study=>study.id===id);}
function proposalFor(studyId,taskId) {return literatureStudy(studyId)?.config.literature?.tasks?.find(task=>task.id===taskId);}
function literaturePage() {
  const studies=state.studies.filter(study=>study.mode!=='benchmark'&&study.config.literature?.phase!=='execution_ready');
  const study=studies.find(item=>item.id===filters.research)||studies[0];
  const top=heading('文献与研究任务','默认先检索最新论文，核验证据，结合已有结果提出任务，再进入实验。');
  if(!study)return top+`<section class="panel">${empty('还没有文献驱动的研究','新建研究会自动检索论文，并将候选任务放入评审队列。')}</section>`;
  const l=study.config.literature||{},phase=l.phase||'queued',papers=l.papers||[],tasks=l.tasks||[];
  const matches=value=>!search||value.toLowerCase().includes(search.toLowerCase());
  const stages=['collecting','synthesizing','awaiting_review','execution_ready'],index=stages.indexOf(phase==='queued'?'collecting':phase);
  return top+`<div class="filters"><select id="literature-study" aria-label="选择文献研究">${studies.map(item=>`<option value="${item.id}" ${item.id===study.id?'selected':''}>${esc(item.name)}</option>`).join('')}</select>${badge(phase)}<button class="button small ghost" data-action="refresh-literature" data-id="${study.id}">${icon('refresh')}重新检索与综合</button><span class="count">${papers.length} 篇论文 · ${tasks.length} 项任务</span></div><div class="research-flow">${stages.map((item,i)=>`<div class="flow-step ${i===index?'current':i<index?'done':''}"><span>${i<index?'✓':i+1}</span>${['最新论文检索','证据归纳与综合','候选任务评审','配置与执行实验'][i]}</div>`).join('')}</div>${l.error?`<div class="banner-error">${esc(l.error)}</div>`:''}<div class="notice"><strong>${esc(synthesisNames[l.method]||'文献调研进行中')}</strong><br>${esc(l.summary||'正在收集和核验论文；这一阶段不会创建训练或推理作业。')}${study.config.known_context?'<br>已纳入本研究登记的既有发现、负结果与约束。':''}</div><section class="panel literature-papers"><div class="panel-head"><div><h2>论文证据库</h2><small>${esc(l.coverage||'默认来源：arXiv')} · ${l.searched_at?esc(date(l.searched_at)):'待检索'}</small></div><span class="text-link">近期 ${study.config.lookback_days||90} 天 + 显式基线</span></div>${papers.length?`<div class="table-wrap"><table><thead><tr><th>论文与阅读层次</th><th>首次发布</th><th>最近修订</th><th>来源</th></tr></thead><tbody>${papers.filter(paper=>matches(paper.title)).map(paper=>`<tr><td><button class="paper-title" data-action="read-paper" data-id="${study.id}" data-paper="${esc(paper.id)}">${esc(paper.title)}</button><div class="cell-sub" style="margin-left:0">${esc(paper.id)} · ${paper.foundational?'显式基线 · ':''}${paper.read_depth==='partial_full_text'?'已读取部分正文':'摘要级证据'}</div></td><td>${esc(paper.published.slice(0,10))}</td><td>${esc(paper.updated.slice(0,10))}</td><td><a class="text-link" href="https://arxiv.org/abs/${esc(paper.id)}" target="_blank" rel="noopener noreferrer">arXiv ↗</a></td></tr>`).join('')}</tbody></table></div>`:empty('正在等待论文检索结果','结果包含检索日期、首次发布日期、修订日期和证据来源。')}</section><div class="section-label"><h2>候选研究任务</h2><span>${tasks.filter(task=>task.status==='accepted').length} 已采纳 · 预算均为实施前预估</span></div><div class="proposal-grid">${tasks.filter(task=>matches(task.title+' '+task.hypothesis)).map(task=>`<section class="panel proposal-card"><div class="proposal-top"><span class="priority ${task.priority.toLowerCase()}">${esc(task.priority)}</span>${badge(task.status)}</div><h3>${esc(task.title)}</h3><p>${esc(task.hypothesis)}</p><div class="evidence-tags">${task.evidence_ids.map(id=>`<button data-action="read-paper" data-id="${study.id}" data-paper="${esc(id)}">${esc(id)}</button>`).join('')}</div><div class="proposal-budget">${icon(task.budget.gpus?'gpu':'cpu')} ${task.budget.gpus?`${task.budget.gpus} GPU · ${fmt(task.budget.gpu_hours)} GPU·h 预估上限`:`${task.budget.cpus} CPU · 无 GPU 预算`}</div>${formalAssessmentHtml(task)}<div class="proposal-actions"><button class="button small ghost" data-action="read-proposal" data-id="${study.id}" data-task="${task.id}">查看实验设计</button>${task.execution_study_id?`<a class="text-link" href="#studies">已创建实验研究 →</a>`:task.status==='draft'?`<button class="button small primary" data-action="proposal-accept" data-id="${study.id}" data-task="${task.id}">采纳任务</button><button class="icon-button" title="暂缓任务" data-action="proposal-defer" data-id="${study.id}" data-task="${task.id}">${icon('pause')}</button>`:task.status==='accepted'?`<button class="button small" data-action="launch-proposal" data-id="${study.id}" data-task="${task.id}">配置实验</button>`:`<button class="button small" data-action="proposal-restore" data-id="${study.id}" data-task="${task.id}">恢复评审</button>`}</div></section>`).join('')||empty('候选任务将在证据综合后出现','每个任务都会保留假设、来源、对照、验收标准和算力预算。')}</div>`;
}

function readPaper(studyId,paperId) {
  const paper=literatureStudy(studyId)?.config.literature?.papers?.find(item=>item.id===paperId);
  if(!paper)return;
  modal(paper.title,'PAPER / '+paper.id,`<div class="form-body"><div class="notice">元数据已由 arXiv 核验 · ${paper.read_depth==='partial_full_text'?'已读取部分正文，非整篇精读':'当前为摘要级证据，非全文结论'}</div><p class="paper-abstract">${esc(paper.abstract)}</p>${paper.note?`<p class="paper-abstract">${esc(paper.note)}</p>`:''}<div class="config-row"><span>首次发布 / 最近修订</span><b>${esc(paper.published.slice(0,10))} / ${esc(paper.updated.slice(0,10))}</b></div><a class="button primary" href="https://arxiv.org/abs/${esc(paper.id)}" target="_blank" rel="noopener noreferrer">打开原始论文 ↗</a></div>`);
}

function readProposal(studyId,taskId) {
  const task=proposalFor(studyId,taskId);if(!task)return;
  const list=items=>`<ul class="proposal-list">${items.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`;
  modal(task.title,'RESEARCH PROPOSAL / '+task.priority,`<div class="form-body"><div>${badge(task.status)}</div><label class="field">可检验假设<p class="paper-abstract">${esc(task.hypothesis)}</p></label><label class="field">综合依据<p class="paper-abstract">${esc(task.rationale)}</p></label><label class="field">对照与基线<p class="paper-abstract">${esc(task.baseline)}</p></label><label class="field">实验步骤${list(task.experiments)}</label><label class="field">评测指标${list(task.metrics)}</label><label class="field">验收与停止标准${list(task.acceptance)}</label><label class="field">风险与结论边界${list(task.risks)}</label><div class="form-note">预估资源：${task.budget.cpus} CPU · ${task.budget.gpus} GPU · ${fmt(task.budget.gpu_hours)} GPU·h<br>${esc(task.budget.note)}</div><div class="evidence-tags">${task.evidence_ids.map(id=>`<a href="https://arxiv.org/abs/${esc(id)}" target="_blank" rel="noopener noreferrer">arXiv:${esc(id)} ↗</a>`).join('')}</div>${formalAssessmentHtml(task)}<div class="form-footer"><button class="button" data-action="proof-assess" data-study="${studyId}" data-task="${taskId}">评估证明义务</button></div></div>`);
}

function updateStudyMode() {
  const form=$('#study-form');if(!form)return;
  const mode=$('#study-mode').value,launching=Boolean(form.dataset.parent);
  $('#repository-fields').hidden=mode!=='openhands';
  $('#literature-fields').hidden=mode==='benchmark'||launching;
  $('[name=metric]',form).readOnly=mode==='benchmark';$('[name=direction]',form).disabled=mode==='benchmark';
  $('#mode-note').textContent=launching?'从已采纳任务进入实验，沿用其论文证据、对照和验收标准。配置后才会提交计算任务。':mode==='benchmark'?'调度验证模式：实际运行 Rosenbrock CPU 基准，不属于科研任务流程。':'默认先检索最新论文，核验来源并综合候选任务，采纳后再配置实验。';
  $('#resource-kind option[value="gpu"]').disabled=mode==='benchmark';
  if(mode==='benchmark'){$('[name=metric]',form).value='score';$('[name=direction]',form).value='max';$('#resource-kind').value='cpu';$('#resource-pool').innerHTML=poolOptions();$('#gpu-field').hidden=true;}
}

function render() {
  if(page==='tree'&&treeView.dragging)return;
  $$('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  $('#breadcrumb').textContent=titles[page];
  const renderers={overview,studies:studiesPage,jobs:jobsPage,resources:resourcesPage,literature:literaturePage,tree:researchTreePage,verification:verificationPage,events:()=>heading('活动记录','研究决策、实验状态与节点事件，按时间顺序留存。',false)+`<section class="panel">${eventsList(state.events.filter(e=>!search||e.message.toLowerCase().includes(search.toLowerCase())))}</section>`,settings:settingsPage};
  $('#main').innerHTML=renderers[page]();
  if(page==='tree')bindResearchTree();
}

function modal(title, eyebrow, body) {
  $('#modal-title').textContent=title; $('#modal-eyebrow').textContent=eyebrow; $('#modal-body').innerHTML=body;
  if(!$('#modal').open) $('#modal').showModal();
}
const formFooter = label => `<div class="form-error" id="form-error" role="alert"></div><div class="form-footer"><button type="button" class="button ghost" data-action="close-modal">取消</button><button class="button primary" type="submit">${label}${icon('arrow')}</button></div>`;
function poolOptions(kind='cpu') {return state.pools.filter(p=>p.kind===kind).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.enabled?'':'（已暂停）'}</option>`).join('');}

function studyDialog(proposal=null,parent=null) {
  modal('新建研究','NEW RESEARCH',`<form id="study-form"><div class="form-body"><label class="field">研究名称<input name="name" required maxlength="160" placeholder="例如：优化推理调度策略"></label><label class="field">研究目标<textarea name="objective" required rows="3" placeholder="描述需要验证的假设，以及希望改进的指标…"></textarea></label><div class="form-row"><label class="field">研究领域<select name="domain"><option value="ml">算法 / 模型研究</option><option value="finance">金融系统研发</option><option value="general">通用研发</option></select></label><label class="field">研究模式<select name="mode" id="study-mode"><option value="literature">文献驱动研究 · 默认流程</option><option value="benchmark">CPU 优化基准 · 调度验证</option><option value="openhands" ${health.llm_configured&&health.openhands_installed?'':'disabled'}>文献调研 → OpenHands 实验</option></select></label></div><div class="form-note" id="mode-note">默认先检索最近论文，核验来源，结合已有结果综合候选任务。候选任务采纳后再配置实验；此阶段仅在 CPU 控制面运行。</div><div id="literature-fields"><label class="field">检索关键词（逗号分隔）<input name="query_terms" placeholder="DSpark, speculative decoding"><small>留空会从研究名称和目标中的英文术语提取。</small></label><div class="form-row" style="margin-top:15px"><label class="field">近期时间窗（天）<input name="lookback_days" type="number" value="90" min="7" max="730"></label><label class="field">论文数量上限<input name="paper_limit" type="number" value="20" min="3" max="40"></label></div><label class="field" style="margin-top:15px">基线论文 arXiv ID（可选）<input name="baseline_papers" placeholder="2607.05147"><small>基线论文可早于检索时间窗。</small></label><label class="field" style="margin-top:15px">参考仓库路径（可选）<input name="reference_repo_path" placeholder="/absolute/path/to/repo"></label><label class="field" style="margin-top:15px">既有证据文件（相对路径，逗号分隔）<input name="context_paths" placeholder="README.md, experiments/research_status.json"></label><label class="field" style="margin-top:15px">已有发现、负结果与约束<textarea name="known_context" rows="3" placeholder="例如：接受率改善尚未转化为吞吐收益，需匹配精度与计算预算。"></textarea></label></div><div id="repository-fields" hidden><label class="field">Git 仓库路径<input name="repo_path" placeholder="/absolute/path/to/experiment-repo"></label><label class="field" style="margin-top:15px">评测命令<input name="evaluation_command" value="python evaluate.py" class="mono"><small>评测脚本必须已提交至 Git，并输出 metrics.json。每一轮使用独立副本。</small></label><div class="form-row" style="margin-top:15px"><label class="field">每轮模型预算（USD）<input name="agent_budget_usd" type="number" min="0.01" step="0.01" value="2"></label><label class="field">每轮 Agent 最大步数<input name="agent_max_iterations" type="number" min="1" max="200" value="30"></label></div></div><div class="form-row three"><label class="field">后续实验轮数<input name="rounds" type="number" value="5" min="1" max="100" required></label><label class="field">计算类型<select name="kind" id="resource-kind"><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label><label class="field">资源池<select name="pool_id" id="resource-pool">${poolOptions()}</select></label></div><div class="form-row three"><label class="field">CPU 核<input name="cpus" type="number" value="1" min="1" required></label><label class="field">内存 MiB<input name="memory_mb" type="number" value="512" min="64" required></label><label class="field">超时（秒）<input name="timeout_seconds" type="number" value="300" min="1" required></label></div><label class="field" id="gpu-field" hidden>GPU 卡数<input name="gpus" type="number" value="1" min="1"></label><div class="form-row"><label class="field">选择指标<input name="metric" value="score" required></label><label class="field">改进方向<select name="direction"><option value="max">越高越好</option><option value="min">越低越好</option></select></label></div></div>${verificationFields()}${formFooter('创建研究')}</form>`);
  if(proposal&&parent){
    const form=$('#study-form');form.dataset.parent=parent.id;form.dataset.proposal=proposal.id;
    $('[name=name]',form).value=proposal.title;
    $('[name=objective]',form).value=proposal.hypothesis+'\n\n对照：'+proposal.baseline+'\n验收：'+proposal.acceptance.join('；');
    $('#study-mode').value='openhands';$('[name=repo_path]',form).value=parent.config.repo_path||'';
    $('[name=metric]',form).value=proposal.metrics[0]||'score';
    $('[name=cpus]',form).value=proposal.budget.cpus;
    $('#resource-kind').value=proposal.budget.gpus?'gpu':'cpu';$('#resource-pool').innerHTML=poolOptions($('#resource-kind').value);
    $('#gpu-field').hidden=!proposal.budget.gpus;$('[name=gpus]',form).value=proposal.budget.gpus||1;
  }
  if(proposal?.formal_verification?.status==='required')$('#lean-mode').value='required';
  updateLeanFields();updateStudyMode();
}

const exampleCode = `import json, math\nfrom pathlib import Path\n\n# Replace this with your experiment.\nterms = 200000\npi = 4 * sum((-1.0)**i / (2*i+1) for i in range(terms))\nmetrics = {"score": 1 / (1 + abs(pi-math.pi)), "pi_estimate": pi}\nPath("metrics.json").write_text(json.dumps(metrics))\nprint(json.dumps(metrics, indent=2))\n`;
function jobDialog() {
  modal('提交实验','NEW EXPERIMENT',`<form id="job-form"><div class="form-body"><label class="field">实验名称<input name="name" required placeholder="给这次实验起个名字" maxlength="160"></label><div class="form-row"><label class="field">计算类型<select name="kind" id="resource-kind"><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label><label class="field">资源池<select name="pool_id" id="resource-pool">${poolOptions()}</select></label></div><label class="field">main.py<textarea name="code" class="mono" rows="10" spellcheck="false" required>${esc(exampleCode)}</textarea><small>程序在独立目录执行。输出 metrics.json 保存指标，artifacts/ 下的文件会自动归档。</small></label><div class="form-row three"><label class="field">CPU 核<input name="cpus" type="number" value="1" min="1" required></label><label class="field">内存 MiB<input name="memory_mb" type="number" value="512" min="64" required></label><label class="field">超时（秒）<input name="timeout_seconds" type="number" value="300" min="1" required></label></div><label class="field" id="gpu-field" hidden>GPU 卡数<input name="gpus" type="number" value="1" min="1"></label><div class="form-note">任务会提交到选定资源池。没有匹配节点时会保持排队，其他资源池不受影响。</div></div>${formFooter('提交实验')}</form>`);
}

function poolDialog() {
  modal('新建资源池','NEW RESOURCE POOL',`<form id="pool-form"><div class="form-body"><label class="field">资源池名称<input name="name" required maxlength="160" placeholder="例如：GPU 训练集群"></label><label class="field">唯一 ID<input name="id" pattern="[a-zA-Z0-9_-]{1,80}" required placeholder="gpu-training"><small>使用字母、数字、下划线或连字符，创建后保持不变。</small></label><label class="field">资源类型<select name="kind"><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label></div>${formFooter('创建资源池')}</form>`);
}

function connectDialog() {
  if($('#connect-form') && $('#modal').open) return;
  modal('连接控制面','CONNECTION',`<form id="connect-form"><div class="form-body"><label class="field">访问令牌<input name="token" type="password" autocomplete="off" placeholder="服务端的 CLUSTER_TOKEN"><small>本机未配置认证时留空。令牌仅保存在当前浏览器。</small></label></div>${formFooter('连接')}</form>`);
}

function closeDrawer() {activeJob=null;$('#drawer').hidden=true;$('#drawer-overlay').hidden=true;}
async function openJob(id) {activeJob=id;$('#drawer').hidden=false;$('#drawer-overlay').hidden=false;$('#drawer').innerHTML='<div class="loading-state"><span class="spinner"></span>读取实验…</div>';await refreshDrawer();}
async function refreshDrawer() {
  const id=activeJob;
  const job=await api('/jobs/'+id);
  if(activeJob!==id)return;
  const oldScroll=$('#drawer').scrollTop, logScroll=$('.log-view', $('#drawer'))?.scrollTop||0;
  const worker=state.workers.find(w=>w.id===job.worker_id);
  $('#drawer').innerHTML=`<div class="drawer-head"><div><span class="eyebrow">EXPERIMENT / ${job.id.slice(0,12)}</span><h2>${esc(job.name)}</h2><div style="margin-top:12px">${badge(job.status)} ${kindTag(job.kind)}</div></div><button class="icon-button" data-action="close-drawer" aria-label="关闭实验详情">${icon('close')}</button></div><div class="drawer-body"><div class="detail-grid"><div><span>资源池</span><b class="mono">${esc(job.pool_id)}</b></div><div><span>执行节点</span><b>${esc(worker?.name || '等待分配')}</b></div><div><span>资源请求</span><b>${job.spec.resources.cpus} CPU · ${job.spec.resources.memory_mb} MiB${job.kind==='gpu'?` · ${job.spec.resources.gpus} GPU`:''}</b></div><div><span>尝试次数 / 超时</span><b>${job.attempt} 次 / ${job.spec.timeout_seconds} 秒</b></div><div><span>提交时间</span><b>${esc(date(job.created_at))}</b></div><div><span>完成时间</span><b>${esc(date(job.finished_at))}</b></div></div>${job.error?`<div class="banner-error">${esc(job.error)}</div>`:''}${jobProofAction(job)}<div class="detail-heading"><h3>评测指标</h3></div>${Object.keys(job.metrics).length?`<div class="metric-grid">${Object.entries(job.metrics).map(([key,value])=>`<div class="metric"><small>${esc(key)}</small><strong class="mono">${fmt(value)}</strong></div>`).join('')}</div>`:'<p style="color:var(--muted);font-size:11px">暂无评测指标</p>'}<div class="detail-heading"><h3>候选源码与命令</h3><button class="button small ghost" data-action="download-source" data-id="${job.id}">下载源码 ZIP</button></div><p style="font-size:10px;color:var(--muted);margin-bottom:12px">研究轮次：${job.round ?? "独立实验"} · 父候选：${job.parent_id ? job.parent_id.slice(0,12) : "基线"}</p><pre class="code-block">${esc(job.spec.command.join(' '))}</pre><div class="detail-heading"><h3>执行日志</h3><span class="text-link">${terminal(job.status)?'已结束':'每 3 秒更新'}</span></div><pre class="log-view">${esc(job.logs || '等待执行节点输出日志…')}</pre><div class="detail-heading"><h3>实验产物</h3><span class="text-link">${job.artifacts.length} 个文件</span></div>${job.artifacts.length?job.artifacts.map(a=>`<div class="artifact">${icon('layers')}<span>${esc(a.name)}<br><small>${fmt(a.size/1024)} KiB</small></span><button class="icon-button" title="下载产物" data-action="download" data-id="${job.id}" data-name="${esc(a.name)}">${icon('download')}</button></div>`).join(''):'<p style="color:var(--muted);font-size:11px">程序输出的产物将在这里归档。</p>'}<div style="display:flex;gap:10px;margin-top:28px">${!terminal(job.status)?`<button class="button danger" data-action="cancel-job" data-id="${job.id}" ${job.status==='cancel_requested'?'disabled':''}>取消实验</button>`:['failed','canceled'].includes(job.status)?`<button class="button" data-action="retry-job" data-id="${job.id}">${icon('refresh')}创建重试实验</button>`:''}<button class="button ghost" data-action="close-drawer">关闭详情</button></div></div>`;
  $('#drawer').scrollTop=oldScroll; if($('.log-view', $('#drawer'))) $('.log-view', $('#drawer')).scrollTop=logScroll;
}

function navigate() {
  const next=location.hash.slice(1)||'overview'; page=titles[next]?next:'overview'; filters={status:'',kind:''};
  closeDrawer(); render();
  if(page==='tree')refresh();
}
window.addEventListener('hashchange',navigate);
$('#search').addEventListener('input',event=>{search=event.target.value;render();});
$('#refresh').addEventListener('click',()=>refresh());
$('#close-modal').addEventListener('click',()=>$('#modal').close());
$('#drawer-overlay').addEventListener('click',closeDrawer);
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('#search').focus();}if(event.key==='Escape')closeDrawer();});
document.addEventListener('change',event=>{
  const el=event.target;
  if(el.id==='lean-mode')updateLeanFields();
  if(el.id==='tree-root'){treeView.rootId=el.value;treeView.data=null;treeView.autoFit=true;render();refresh();}
  if(el.id==='literature-study'){filters.research=el.value;render();}
  if(el.id==='chart-study'){chartStudy=el.value;render();}
  if(el.id==='status-filter'){filters.status=el.value;render();}
  if(el.id==='kind-filter'){filters.kind=el.value;render();}
  if(el.id==='resource-kind'){$('#resource-pool').innerHTML=poolOptions(el.value);$('#gpu-field').hidden=el.value!=='gpu';}
  if(el.id==='study-mode'){
    updateStudyMode();
  }
});

document.addEventListener('click',async event=>{
  const target=event.target.closest('[data-action]');
  if(target){event.preventDefault();event.stopPropagation();const {action,id}=target.dataset;
    try {
      if(action.startsWith('proof-'))await handleProofAction(action,target.dataset);
      else if(action.startsWith('tree-'))await handleTreeAction(action,target.dataset);
      else if(action==='new-study')studyDialog();
      else if(action==='new-job')jobDialog();
      else if(action==='new-pool')poolDialog();
      else if(action==='connect')connectDialog();
      else if(action==='read-paper')readPaper(id,target.dataset.paper);
      else if(action==='read-proposal')readProposal(id,target.dataset.task);
      else if(action==='refresh-literature'){await api(`/studies/${id}/literature/refresh`,{method:'POST'});toast('已排队重新检索和综合');await refresh();}
      else if(action.startsWith('proposal-')){await api(`/studies/${id}/proposals/${target.dataset.task}/${action.slice(9)}`,{method:'POST'});$('#modal').close();toast('候选任务状态已更新');await refresh();}
      else if(action==='launch-proposal'){
        if(!health.llm_configured||!health.openhands_installed)throw new Error('配置模型 API 和 OpenHands 后可进入实验；已采纳任务会继续保留。');
        studyDialog(proposalFor(id,target.dataset.task),literatureStudy(id));
      }
      else if(action==='close-modal')$('#modal').close();
      else if(action==='close-drawer')closeDrawer();
      else if(action==='clear-study'){filters.study='';render();}
      else if(action==='benchmark'){await api('/studies',{method:'POST',body:JSON.stringify({name:'Rosenbrock 优化基准',objective:'在固定 6000 步预算下改善优化算法的收敛效果，验证实验闭环。',mode:'benchmark',rounds:5})});toast('基准研究已启动，指标来自实际 CPU 执行');await refresh();}
      else if(action==='toggle-pool'){await api(`/pools/${id}/toggle`,{method:'POST'});await refresh();}
      else if(action.startsWith('study-')){await api(`/studies/${id}/${action.slice(6)}`,{method:'POST'});toast('研究状态已更新');await refresh();}
      else if(action==='cancel-job'){await api(`/jobs/${id}/cancel`,{method:'POST'});toast('取消请求已提交');await refresh();}
      else if(action==='retry-job'){const job=await api(`/jobs/${id}/retry`,{method:'POST'});toast('已创建新的重试实验');await refresh();await openJob(job.id);}
      else if(action==='copy-worker'){const pool=state.pools.find(p=>p.id===id);await navigator.clipboard.writeText(workerCommand(pool));toast('接入命令已复制');}
      else if(action==='download-source'){
        const response=await fetch(`/api/jobs/${id}/source`,{headers:token?{Authorization:`Bearer ${token}`}:{}});
        if(!response.ok)throw new Error('源码下载失败');
        const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`candidate-${id.slice(0,12)}.zip`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }
      else if(action==='download'){
        const response=await fetch(`/api/jobs/${id}/artifacts/${target.dataset.name.split('/').map(encodeURIComponent).join('/')}`,{headers:token?{Authorization:`Bearer ${token}`}:{}});
        if(!response.ok)throw new Error('产物下载失败');
        const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=target.dataset.name.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }
    }catch(error){toast(error.message,true);}return;
  }
  const treeNode=event.target.closest('[data-tree-node]');if(treeNode){selectTreeNode(treeNode.dataset.treeNode);return;}
  const row=event.target.closest('[data-job]');if(row){try{await openJob(row.dataset.job);}catch(error){toast(error.message,true);}return;}
  const study=event.target.closest('[data-study]');if(study){const id=study.dataset.study;if(literatureStudy(id)?.mode!=='benchmark'&&literatureStudy(id)?.config.literature?.phase!=='execution_ready'){page='literature';history.pushState(null,'','#literature');filters={research:id,status:'',kind:''};render();return;}if(page!=='jobs'){page='jobs';history.pushState(null,'','#jobs');}filters={status:'',kind:'',study:id};render();}
});

document.addEventListener('submit',async event=>{
  const form=event.target;if(!['study-form','job-form','pool-form','connect-form','formal-assessment-form'].includes(form.getAttribute('id')))return;
  event.preventDefault();const submit=$('[type="submit"]',form);submit.disabled=true;$('#form-error').textContent='';
  const values=Object.fromEntries(new FormData(form));
  try{
    if(form.getAttribute('id')==='formal-assessment-form'){
      await api(`/studies/${form.dataset.study}/proposals/${form.dataset.task}/formal-verification`,{method:'PUT',body:JSON.stringify({...values,obligations:values.obligations.split('\n').map(value=>value.trim()).filter(Boolean)})});$('#modal').close();toast('证明义务评估已保存');await refresh();return;
    }
    if(form.getAttribute('id')==='connect-form'){
      token=values.token;localStorage.setItem('forge-token',token);health=await api('/health');$('#modal').close();toast('已连接控制面');await refresh();return;
    }
    if(form.getAttribute('id')==='pool-form')await api('/pools',{method:'POST',body:JSON.stringify(values)});
    else{
      const base={name:values.name,kind:values.kind,pool_id:values.pool_id,timeout_seconds:Number(values.timeout_seconds),resources:{cpus:Number(values.cpus),memory_mb:Number(values.memory_mb),gpus:values.kind==='gpu'?Number(values.gpus):0}};
      if(form.getAttribute('id')==='study-form'){
        const command=values.evaluation_command.trim().match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g)?.map(x=>x.replace(/^(["'])(.*)\1$/,'$2'))||[];
        const result=await api(form.dataset.parent?`/studies/${form.dataset.parent}/proposals/${form.dataset.proposal}/execution`:'/studies',{method:form.dataset.parent?'PUT':'POST',body:JSON.stringify({...base,verification:readLeanFields(values),query_terms:(values.query_terms||'').split(/[,，]/).map(x=>x.trim()).filter(Boolean),lookback_days:Number(values.lookback_days||90),paper_limit:Number(values.paper_limit||20),baseline_papers:(values.baseline_papers||'').split(/[,，\s]+/).filter(Boolean),known_context:values.known_context||'',context_paths:(values.context_paths||'').split(/[,，]/).map(x=>x.trim()).filter(Boolean),objective:values.objective,domain:values.domain,mode:values.mode,rounds:Number(values.rounds),repo_path:values.mode==='openhands'?values.repo_path:(values.reference_repo_path||''),evaluation_command:command,agent_budget_usd:Number(values.agent_budget_usd||2),agent_max_iterations:Number(values.agent_max_iterations||30),metric:values.mode==='benchmark'?'score':values.metric,direction:values.mode==='benchmark'?'max':values.direction})});
        if(result.mode!=='benchmark'&&result.config.literature?.phase!=='execution_ready'){page='literature';history.pushState(null,'','#literature');filters.research=result.id;}
      }else await api('/jobs',{method:'POST',body:JSON.stringify({...base,files:{'main.py':values.code},idempotency_key:crypto.randomUUID()})});
    }
    $('#modal').close();toast(form.getAttribute('id')==='pool-form'?'资源池已创建':form.getAttribute('id')==='study-form'?'研究已启动':'实验已提交');await refresh();
  }catch(error){$('#form-error').textContent=error.message;}finally{submit.disabled=false;}
});

(async()=>{page=titles[location.hash.slice(1)]?location.hash.slice(1):'overview';try{health=await api('/health');}catch{}await refresh();setInterval(refresh,3000);})();
