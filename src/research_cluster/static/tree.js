"use strict";

const treeView = {rootId:'',data:null,selected:null,collapsed:new Set(),zoom:1,x:0,y:0,autoFit:true,dragging:false};
const treeKinds = {research:'研究目标',execution:'执行分支',agent:'角色步骤',proposal:'候选任务',candidate:'候选版本',result:'评测记录',paper:'论文',evidence:'证据集合',verification:'形式验证',obligation:'证明目标'};
const treeRoles = {coordinator:'研究协调器',retriever:'论文检索',synthesizer:'证据综合',developer:'代码 Agent',parameter_search:'参数搜索',executor:'计算执行',evaluator:'指标评测',lean_checker:'Lean 核验器',prover:'证明 Agent',smoke_setup:'自检准备'};
const treeStatus = {waiting:'等待实验',stale:'租约过期',missing:'指标缺失',recorded:'已收录',passed:'已证明',unproved:'未完成证明',repairing:'证明修复中',error:'错误'};

function selectedTreeRoot() {
  const roots=state.studies.filter(study=>!study.config.parent_research_id);
  return roots.find(study=>study.id===treeView.rootId)||roots.find(study=>study.mode!=='benchmark')||roots[0];
}

async function refreshResearchTreeData() {
  const root=selectedTreeRoot();
  if(!root){treeView.data=null;return;}
  const identity=root.id,data=await api(`/studies/${identity}/tree`);
  if(page!=='tree'||selectedTreeRoot()?.id!==identity)return;
  if(treeView.data?.root_id!==data.root_id){
    treeView.collapsed=new Set(data.nodes.filter(node=>node.kind==='evidence').map(node=>node.id));
    treeView.selected=data.root_id;treeView.autoFit=true;
  }
  treeView.rootId=identity;treeView.data=data;
}

function treeTone(status) {
  if(['running','collecting','synthesizing','repairing'].includes(status))return 'running';
  if(['completed','succeeded','recorded','passed'].includes(status))return 'done';
  if(['failed','error','stale','missing','unproved'].includes(status))return 'error';
  if(['canceled','deferred','paused'].includes(status))return 'muted';
  return 'waiting';
}

function treeLabel(status) {return treeStatus[status]||labels[status]||status;}

function treeLayout(data) {
  const map=new Map(data.nodes.map(node=>[node.id,node])),children=new Map(),positions=new Map(),seen=new Set();
  for(const edge of data.edges){if(!children.has(edge.source))children.set(edge.source,[]);children.get(edge.source).push(edge.target);}
  let row=0,maxDepth=0;
  const walk=(id,depth)=>{
    if(seen.has(id)||!map.has(id))return null;seen.add(id);maxDepth=Math.max(maxDepth,depth);
    const offspring=treeView.collapsed.has(id)?[]:(children.get(id)||[]);
    const placed=offspring.map(child=>walk(child,depth+1)).filter(Boolean);
    const y=placed.length?(placed[0].y+placed[placed.length-1].y)/2:30+(row++)*124;
    const position={x:30+depth*300,y,id};positions.set(id,position);return position;
  };
  walk(data.root_id,0);
  return {map,children,positions,width:60+maxDepth*300+248,height:60+Math.max(1,row)*124-30};
}

function treeLines(label) {
  const lines=[];let current='',weight=0;
  for(const char of label){
    const size=char.charCodeAt(0)>255?1:.55;
    if(weight+size>18){lines.push(current);current='';weight=0;if(lines.length===2)break;}
    current+=char;weight+=size;
  }
  if(lines.length<2&&current)lines.push(current);
  const displayed=lines.join('');
  if(displayed.length<label.length&&lines.length)lines[lines.length-1]=lines.at(-1).slice(0,-1)+'…';
  return lines;
}

function researchTreePage() {
  const root=selectedTreeRoot(),roots=state.studies.filter(study=>!study.config.parent_research_id);
  const top=heading('研究树','追踪研究如何分支、哪些角色正在工作，以及每个候选版本的来源与结果。');
  if(!root)return top+`<section class="panel">${empty('创建研究后，这里会长出第一棵树','真实的文献、任务、执行分支和评测结果会逐步加入。')}</section>`;
  if(!treeView.data||treeView.data.root_id!=='study:'+root.id)return top+'<div class="loading-state"><span class="spinner"></span>读取研究树…</div>';
  const data=treeView.data,r=data.runtime,s=data.summary,layout=treeLayout(data);
  const edges=data.edges.filter(edge=>layout.positions.has(edge.source)&&layout.positions.has(edge.target)).map(edge=>{
    const a=layout.positions.get(edge.source),b=layout.positions.get(edge.target),x=a.x+248,y=a.y+48;
    return `<path class="research-tree-edge ${edge.relation==='derived_from'?'lineage':''}" d="M ${x} ${y} C ${x+26} ${y}, ${b.x-26} ${b.y+48}, ${b.x} ${b.y+48}"/>`;
  }).join('');
  const nodes=[...layout.positions.values()].map(position=>{
    const node=layout.map.get(position.id),kids=layout.children.get(node.id)||[],collapsed=treeView.collapsed.has(node.id),tone=treeTone(node.status);
    const kind=node.kind==='agent'?(treeRoles[node.role]||treeKinds.agent):treeKinds[node.kind];
    const match=search&&(node.label+' '+node.subtitle).toLowerCase().includes(search.toLowerCase());
    return `<g class="research-tree-node ${tone} ${treeView.selected===node.id?'selected':''} ${match?'search-match':''}" transform="translate(${position.x},${position.y})" data-tree-node="${esc(node.id)}" data-kind="${esc(node.kind)}" role="button" tabindex="0" aria-label="${esc(node.label+'，'+treeLabel(node.status))}"><title>${esc(node.label+' · '+treeLabel(node.status))}</title><rect class="tree-node-box" width="248" height="96" rx="9"/><circle class="tree-node-dot" cx="17" cy="17" r="3"/><text class="tree-node-kind" x="27" y="20">${esc(kind)}</text><text class="tree-node-status" x="232" y="20" text-anchor="end">${esc(treeLabel(node.status))}</text>${treeLines(node.label).map((line,i)=>`<text class="tree-node-title" x="15" y="${43+i*17}">${esc(line)}</text>`).join('')}<text class="tree-node-subtitle" x="15" y="81">${esc(node.subtitle.length>34?node.subtitle.slice(0,33)+'…':node.subtitle)}</text>${kids.length?`<g class="tree-toggle" transform="translate(248,48)" data-action="tree-toggle" data-id="${esc(node.id)}" role="button" tabindex="0" aria-label="${collapsed?'展开':'收起'} ${esc(node.label)}"><circle r="10"/><text text-anchor="middle" y="4">${collapsed?'+':'−'}</text></g>`:''}</g>`;
  }).join('');
  return top+`<div class="tree-runtime"><div><span class="runtime-dot ${r.model_ready?'':'off'}"></span><strong>${r.model_ready?'模型已配置':'模型未配置'}</strong><small>运行中的模型步骤 ${s.running_model_steps}</small></div><div><strong>${r.research_concurrency_per_orchestrator}</strong><small>单编排器研究并发上限</small></div><div><strong>CPU 节点 ${r.cpu_workers} · GPU 节点 ${r.gpu_workers}</strong><small>在线执行节点 · ${r.cpu_execution_slots+r.gpu_execution_slots} 个任务槽</small></div><div><strong>${s.running_experiments}</strong><small>本研究正在执行的实验</small></div></div><div class="filters"><select id="tree-root" aria-label="选择研究树">${roots.map(study=>`<option value="${study.id}" ${study.id===root.id?'selected':''}>${esc(study.name)}</option>`).join('')}</select><span class="tree-count">${s.papers} 篇论文 · ${s.proposals} 个候选任务 · ${s.studies-1} 个执行分支 · ${s.candidates} 个候选版本</span></div>${!r.model_ready?'<div class="notice tree-notice">当前展示真实调研和任务分支。接入模型并为已采纳任务配置实验后，多个分支可并行推进；检索、规则综合和计算 worker 不会被标成运行中的模型 Agent。</div>':''}${data.warnings.length?`<div class="banner-error">${data.warnings.map(esc).join('<br>')}</div>`:''}<section class="panel research-tree-panel"><div class="tree-toolbar"><div class="tree-legend"><span class="done">已完成</span><span class="running">运行中</span><span class="waiting">待评审 / 等待</span><span class="error">失败 / 异常</span></div><div class="tree-tools"><button class="button small ghost" data-action="tree-focus">聚焦任务</button><button class="button small ghost" data-action="tree-expand">展开全部</button><button class="icon-button" data-action="tree-zoom-out" aria-label="缩小研究树">−</button><span id="tree-zoom-label">100%</span><button class="icon-button" data-action="tree-zoom-in" aria-label="放大研究树">＋</button><button class="button small ghost" data-action="tree-fit">适应画布</button></div></div><div id="research-tree-canvas" class="research-tree-canvas" aria-label="可拖动和缩放的研究树"><svg id="research-tree-svg" width="100%" height="100%" role="group" aria-label="研究任务与候选谱系"><g id="research-tree-transform">${edges}${nodes}</g></svg><span class="tree-canvas-hint">拖动画布 · 滚轮缩放 · 点击节点查看详情</span></div><div class="tree-bottom"><span>实线：任务与执行关系 · 虚线：候选版本衍生</span><span>${esc(date(data.generated_at))} · 3 秒更新</span></div></section>${treeNodeDetails(data)}`;
}

function treeNodeDetails(data) {
  const node=data.nodes.find(item=>item.id===treeView.selected)||data.nodes[0];
  if(!node)return '';
  const d=node.details;
  const fact=(key,value)=>value==null||value===''?'':`<div class="config-row"><span>${esc(key)}</span><b>${esc(value)}</b></div>`;
  let body='';
  if(node.kind==='proposal'){
    const task=d.proposal;
    body=`<p class="paper-abstract">${esc(task.hypothesis)}</p><div class="evidence-tags">${task.evidence_ids.map(id=>`<a href="https://arxiv.org/abs/${esc(id)}" target="_blank" rel="noopener noreferrer">arXiv:${esc(id)} ↗</a>`).join('')}</div><div class="tree-detail-actions"><button class="button small" data-action="tree-open-proposal" data-study="${d.study_id}" data-task="${d.task_id}">查看完整实验设计</button><span>预算 ${task.budget.cpus} CPU · ${task.budget.gpus} GPU · ${fmt(task.budget.gpu_hours)} GPU·h</span></div>`;
  }else if(node.kind==='candidate'||node.kind==='result'){
    body=`<div class="metric-grid">${Object.entries(d.metrics||{}).map(([key,value])=>`<div class="metric"><small>${esc(key)}</small><strong class="mono">${fmt(value)}</strong></div>`).join('')}</div>${fact('执行节点',d.worker_name)}${fact('执行资源',d.kind?.toUpperCase())}${fact('资源池',d.pool_id)}${d.best?'<div class="notice">该候选当前为本执行分支的最佳版本。</div>':''}${d.error?`<div class="banner-error">${esc(d.error)}</div>`:''}<div class="tree-detail-actions"><button class="button small" data-action="tree-open-job" data-id="${d.job_id}">查看日志、源码与产物</button></div>`;
  }else if(['verification','obligation'].includes(node.kind)||node.role==='prover'){
    body=`${d.coverage?`<p class="paper-abstract">${esc(d.coverage)}</p>`:''}${fact('证明策略',d.mode==='required'?'必须通过':d.mode==='advisory'?'附加验证':undefined)}${fact('规格',d.proposition)}${fact('候选源码 SHA-256',d.source_hash)}${fact('固定规格 SHA-256',d.spec_hash)}${fact('验证资源池',d.pool_id)}${fact('公理依赖',d.axioms?.join(', '))}${d.report?.error?`<div class="banner-error">${esc(d.report.error)}</div>`:''}<div class="tree-detail-actions"><button class="button small" data-action="proof-open" data-id="${d.candidate_id}">查看证明报告与日志</button></div>`;
  }else if(node.kind==='paper'){
    body=fact('首次发布',d.published?.slice(0,10))+fact('最近修订',d.updated?.slice(0,10))+fact('阅读层次',d.read_depth==='partial_full_text'?'已取得部分正文':'摘要级证据')+`<div class="tree-detail-actions"><button class="button small" data-action="tree-open-paper" data-study="${d.study_id}" data-paper="${d.paper_id}">查看论文证据</button></div>`;
  }else{
    body=`${d.objective?`<p class="paper-abstract">${esc(d.objective)}</p>`:''}${d.summary?`<p class="paper-abstract">${esc(d.summary)}</p>`:''}${fact('角色',treeRoles[node.role])}${fact('方法',d.method?synthesisNames[d.method]||d.method:undefined)}${fact('模型调用',d.model_backed==null?undefined:d.model_backed?'模型步骤':'当前角色不调用模型')}${fact('检索式',d.query)}${fact('主指标最佳值',d.best_score==null?null:fmt(d.best_score))}${d.local_evidence_paths?.length?`<p class="inventory-limit">既有证据：${d.local_evidence_paths.map(esc).join(' · ')}</p>`:''}<div class="tree-detail-actions"><button class="button small ghost" data-action="tree-open-study" data-id="${d.study_id}">打开所属研究</button></div>`;
  }
  return `<section class="panel tree-details" id="tree-node-details" aria-live="polite"><div class="panel-head"><div><span class="eyebrow">${esc(treeKinds[node.kind]||node.kind)} / ${esc(node.role?treeRoles[node.role]||node.role:'')}</span><h2>${esc(node.label)}</h2></div><span class="badge ${esc(node.status)}">${esc(treeLabel(node.status))}</span></div><div class="tree-details-body">${body}</div></section>`;
}

function applyTreeTransform() {
  const group=$('#research-tree-transform');if(!group)return;
  group.setAttribute('transform',`translate(${treeView.x},${treeView.y}) scale(${treeView.zoom})`);
  $('#tree-zoom-label').textContent=Math.round(treeView.zoom*100)+'%';
}

function fitResearchTree() {
  const canvas=$('#research-tree-canvas');if(!canvas||!treeView.data)return;
  const layout=treeLayout(treeView.data),width=canvas.clientWidth,height=canvas.clientHeight;
  treeView.zoom=Math.max(.18,Math.min(1,(width-40)/layout.width,(height-50)/layout.height));
  treeView.x=(width-layout.width*treeView.zoom)/2;treeView.y=(height-layout.height*treeView.zoom)/2;
  treeView.autoFit=true;applyTreeTransform();
}

function zoomResearchTree(factor,cx,cy) {
  const canvas=$('#research-tree-canvas');if(!canvas)return;
  cx=cx??canvas.clientWidth/2;cy=cy??canvas.clientHeight/2;
  const previous=treeView.zoom,next=Math.max(.15,Math.min(2.5,previous*factor)),ratio=next/previous;
  treeView.x=cx-(cx-treeView.x)*ratio;treeView.y=cy-(cy-treeView.y)*ratio;
  treeView.zoom=next;treeView.autoFit=false;applyTreeTransform();
}

function bindResearchTree() {
  const canvas=$('#research-tree-canvas');if(!canvas)return;
  if(treeView.autoFit)fitResearchTree();else applyTreeTransform();
  let drag=null;
  canvas.addEventListener('pointerdown',event=>{
    if(event.target.closest('[data-tree-node]')||event.button!==0)return;
    drag={x:event.clientX,y:event.clientY,panX:treeView.x,panY:treeView.y};treeView.dragging=true;treeView.autoFit=false;
    canvas.setPointerCapture(event.pointerId);canvas.classList.add('dragging');
  });
  canvas.addEventListener('pointermove',event=>{if(!drag)return;treeView.x=drag.panX+event.clientX-drag.x;treeView.y=drag.panY+event.clientY-drag.y;applyTreeTransform();});
  const stop=()=>{if(!drag)return;drag=null;treeView.dragging=false;canvas.classList.remove('dragging');render();};
  canvas.addEventListener('pointerup',stop);canvas.addEventListener('pointercancel',stop);
  canvas.addEventListener('wheel',event=>{event.preventDefault();const box=canvas.getBoundingClientRect();zoomResearchTree(Math.exp(-event.deltaY*.0015),event.clientX-box.left,event.clientY-box.top);},{passive:false});
  canvas.addEventListener('keydown',event=>{
    if(!['Enter',' '].includes(event.key))return;
    const toggle=event.target.closest('[data-action="tree-toggle"]');
    if(toggle){event.preventDefault();handleTreeAction('tree-toggle',toggle.dataset);return;}
    const node=event.target.closest('[data-tree-node]');
    if(node){event.preventDefault();selectTreeNode(node.dataset.treeNode);}
  });
}

function selectTreeNode(identity) {treeView.selected=identity;render();}

function handleTreeAction(action,data) {
  if(action==='tree-toggle'){
    if(treeView.collapsed.has(data.id))treeView.collapsed.delete(data.id);else treeView.collapsed.add(data.id);
    treeView.autoFit=true;render();
  }else if(action==='tree-fit')fitResearchTree();
  else if(action==='tree-zoom-in')zoomResearchTree(1.2);
  else if(action==='tree-zoom-out')zoomResearchTree(1/1.2);
  else if(action==='tree-expand'){treeView.collapsed.clear();treeView.autoFit=true;render();}
  else if(action==='tree-focus'){treeView.collapsed=new Set(treeView.data.nodes.filter(node=>node.kind==='evidence').map(node=>node.id));treeView.autoFit=true;render();}
  else if(action==='tree-open-job')return openJob(data.id);
  else if(action==='tree-open-proposal')readProposal(data.study,data.task);
  else if(action==='tree-open-paper')readPaper(data.study,data.paper);
  else if(action==='tree-open-study'){
    const study=state.studies.find(item=>item.id===data.id);if(!study)return;
    page=study.mode!=='benchmark'&&study.config.literature?.phase!=='execution_ready'?'literature':'jobs';
    history.pushState(null,'','#'+page);filters={status:'',kind:'',research:data.id,study:data.id};render();
  }
}

window.addEventListener('resize',()=>{if(page==='tree'&&treeView.autoFit)fitResearchTree();});
