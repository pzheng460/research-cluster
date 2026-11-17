"use strict";

const proofLabels={off:'未启用',advisory:'附加验证',required:'必须通过',passed:'已证明',unproved:'未完成证明',repairing:'证明修复中',unassessed:'待评估',not_applicable:'无需形式证明',recommended:'建议证明',error:'验证错误'};
const proofLabel=value=>proofLabels[value]||labels[value]||value;
const proofBadge=value=>`<span class="badge ${esc(value)}">${esc(proofLabel(value))}</span>`;

function verificationFields(){
  return `<div class="lean-config"><div class="section-label"><h3>Lean 形式验证</h3><span>独立 CPU 队列 · 固定命题与工具链</span></div><label class="field">验证策略<select name="verification_mode" id="lean-mode"><option value="off">未启用 · 先评估证明义务</option><option value="advisory">附加验证 · 记录结果</option><option value="required">必须通过 · 未证明的候选不能晋级</option></select></label><div id="lean-policy-fields" hidden><p class="form-note">在创建研究时冻结命题与假设。候选在 JSON 文件中提供证明项；模型可修复证明，规格保持固定。</p><label class="field">冻结的 ClusterSpec.lean<textarea name="lean_spec_source" class="mono" rows="7" spellcheck="false" placeholder="import Init&#10;namespace ClusterSpec&#10;def Conserves : Prop := ∀ (a b k : Nat), k ≤ a → (a-k)+(b+k)=a+b&#10;end ClusterSpec"></textarea></label><label class="field">证明目标（JSON）<textarea name="lean_targets" class="mono" rows="4" spellcheck="false" placeholder='[{"id":"conservation","proposition":"ClusterSpec.Conserves","description":"总额守恒"}]'></textarea><small>proposition 引用固定规格中定义的 Prop。每个目标都必须完成并通过公理审核与内核重检。</small></label><div class="form-row"><label class="field">候选证明 JSON 路径<input name="lean_proof_path" value="formal/proofs.json"><small>内容示例：{"conservation":"by intro a b k h; omega"}</small></label><label class="field">验证 CPU 池<select name="lean_pool">${state.pools.filter(pool=>pool.kind==='cpu').map(pool=>`<option value="${esc(pool.id)}" ${pool.id==='lean-cpu'?'selected':''}>${esc(pool.name)}</option>`).join('')}</select></label></div><label class="field">证明覆盖范围与实现对应关系<textarea name="lean_coverage" rows="2">仅覆盖登记的 Lean 数学规格；实际实现与规格的对应关系仍需审核。</textarea></label><div class="form-row three"><label class="field">验证超时（秒）<input name="lean_timeout" type="number" value="120" min="5" max="1800"></label><label class="field">模型修复次数<input name="lean_repairs" type="number" value="1" min="0" max="4"></label><label class="field">每次最大输出 token<input name="lean_tokens" type="number" value="2048" min="128" max="8000"></label></div><p class="form-note">默认 Lean 4.35.0，1 CPU / 1024 MiB；每次模型修复默认最多 60 秒。当前支持 core / Std 的受限证明项。数学模型通过证明后，仍需评估它与实际代码的对应关系。</p></div></div>`;
}

function updateLeanFields(){
  if(!$('#lean-mode'))return;
  const enabled=$('#lean-mode').value!=='off';$('#lean-policy-fields').hidden=!enabled;
  for(const name of ['lean_spec_source','lean_targets'])$(`[name="${name}"]`).required=enabled;
}

function readLeanFields(values){
  if(!values.verification_mode||values.verification_mode==='off')return {mode:'off'};
  let targets;try{targets=JSON.parse(values.lean_targets);}catch{throw new Error('证明目标需要有效的 JSON 数组');}
  return {mode:values.verification_mode,spec_source:values.lean_spec_source,targets,
    proof_path:values.lean_proof_path,pool_id:values.lean_pool,coverage:values.lean_coverage,
    timeout_seconds:Number(values.lean_timeout),repair_attempts:Number(values.lean_repairs),repair_max_tokens:Number(values.lean_tokens)};
}

function verificationPage(){
  const checks=state.verifications||[],online=state.workers.filter(worker=>worker.online&&worker.pool_id==='lean-cpu');
  const count=status=>checks.filter(check=>check.status===status).length;
  return heading('形式验证','将固定命题、候选源码和证明结果绑定；验证状态与实验指标分别记录。')+`<div class="stats"><div class="stat"><div class="stat-title">已证明</div><div class="stat-value">${count('passed')}</div><div class="stat-sub">公理审核与内核重检均通过</div></div><div class="stat"><div class="stat-title">等待 / 检查 / 修复</div><div class="stat-value">${checks.filter(check=>['queued','running','repairing'].includes(check.status)).length}</div><div class="stat-sub">独立 CPU 验证队列</div></div><div class="stat"><div class="stat-title">未证明 / 验证错误</div><div class="stat-value">${count('unproved')+count('error')}</div><div class="stat-sub">保留诊断；不表示命题已被反驳</div></div><div class="stat"><div class="stat-title">在线验证节点</div><div class="stat-value">${online.length}</div><div class="stat-sub">${online.reduce((sum,worker)=>sum+worker.concurrency,0)} 个任务槽 · Lean 4.35.0</div></div></div><section class="panel proof-intro"><div><h2>让需要证明的性质成为晋级条件</h2><p>在研究提案中评估证明义务，在新建研究或配置实验时选择验证策略、冻结规格和证明目标。证明 Agent 使用独立的修复预算。</p><small>Lean 证明覆盖登记的数学规格；Python / CUDA 实现的对应关系需要另行审核。</small></div><button class="button" data-action="proof-smoke">运行 Lean 验证自检</button></section><div class="section-label"><h2>候选证明记录</h2><span>${checks.length} 条记录</span></div><div class="proof-list">${checks.map(check=>{
    const candidate=state.jobs.find(job=>job.id===check.candidate_id),study=state.studies.find(item=>item.id===check.study_id);
    return `<section class="panel proof-card"><div class="proof-card-head"><div><span class="eyebrow">${esc(study?.name||check.study_id.slice(0,8))}</span><h3>${esc(candidate?.name||check.candidate_id.slice(0,12))}</h3></div>${proofBadge(check.status)}</div><p>${esc(check.policy.coverage)}</p><div class="proof-card-meta"><span>${esc(proofLabel(check.policy.mode))} · ${check.policy.targets.length} 个目标</span><span class="mono">源码 ${check.source_hash.slice(0,12)} · 规格 ${check.spec_hash.slice(0,12)}</span></div><div class="proof-card-actions"><button class="button small" data-action="proof-open" data-id="${check.candidate_id}">查看证明报告</button><button class="button small ghost" data-action="proof-tree" data-id="${check.study_id}">在树中查看</button></div></section>`;
  }).join('')||`<section class="panel">${empty('还没有候选证明记录','先评估提案的证明义务，或运行 CPU 验证自检来检查工具链和执行队列。')}</section>`}</div>`;
}

function formalAssessmentHtml(task){
  const assessment=task.formal_verification||{status:'unassessed',rationale:'实施前评估证明义务。',obligations:[]};
  return `<div class="formal-assessment"><div>${proofBadge(assessment.status)}<strong>形式验证评估</strong></div><p>${esc(assessment.rationale)}</p>${assessment.obligations?.length?`<ul>${assessment.obligations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}</div>`;
}

async function openVerification(identity){
  const data=await api(`/jobs/${identity}/verification`),check=data.verification;
  if(!check){toast('此候选尚未创建形式验证任务');return;}
  modal('Lean 证明报告','FORMAL VERIFICATION',`<div class="proof-report">${proofBadge(check.status)} ${proofBadge(check.policy.mode)}<p>${esc(check.policy.coverage)}</p><div class="config-row"><span>候选源码 SHA-256</span><b class="mono">${esc(check.source_hash)}</b></div><div class="config-row"><span>固定规格 SHA-256</span><b class="mono">${esc(check.spec_hash)}</b></div><div class="config-row"><span>工具链 / 内核重检</span><b>Lean ${esc(check.policy.toolchain)} · ${check.report.kernel_rechecked?'已重检':'尚未通过'}</b></div><div class="config-row"><span>模型修复预算</span><b>${check.model_attempts} / ${check.policy.repair_attempts} 次 · ${check.policy.repair_max_tokens} token/次</b></div><h3>证明目标</h3>${check.policy.targets.map(target=>{const report=check.report.targets?.find(item=>item.id===target.id);return `<div class="proof-target"><strong>${esc(target.description||target.id)}</strong>${proofBadge(check.status==='passed'?'passed':['unproved','error'].includes(check.status)?'unproved':check.status==='canceled'?'canceled':'queued')}<code>${esc(target.proposition)}</code><small>公理：${report?report.axioms?.map(esc).join(', ')||'无':'待核验'}</small></div>`;}).join('')}${check.report.error?`<pre class="code-block">${esc(check.report.error)}</pre>`:''}<h3>验证尝试与可下载产物</h3>${data.checks.map(job=>`<div class="proof-attempt">${badge(job.status)}<span>${esc(job.name)}</span><button class="button small ghost" data-action="proof-job" data-id="${job.id}">日志与证明源码</button></div>`).join('')}<div class="form-footer">${['unproved','error','canceled'].includes(check.status)&&state.studies.find(item=>item.id===check.study_id)?.status==='active'?`<button class="button" data-action="proof-retry" data-id="${identity}">重新验证</button>`:''}<button class="button primary" data-action="close-modal">关闭</button></div></div>`);
}

function formalAssessmentDialog(studyId,taskId){
  const task=proposalFor(studyId,taskId),assessment=task.formal_verification||{status:'unassessed',rationale:'',obligations:[]};
  modal('评估证明义务','FORMAL ASSESSMENT',`<form id="formal-assessment-form" data-study="${studyId}" data-task="${taskId}"><div class="form-body"><label class="field">是否需要形式证明<select name="status">${['unassessed','not_applicable','recommended','required'].map(value=>`<option value="${value}" ${assessment.status===value?'selected':''}>${esc(proofLabel(value))}</option>`).join('')}</select></label><label class="field">判断依据<textarea name="rationale" rows="3" required>${esc(assessment.rationale)}</textarea></label><label class="field">待形式化性质（每行一个）<textarea name="obligations" rows="4">${esc(assessment.obligations.join('\n'))}</textarea><small>登记明确的数学性质与假设；性能和业务效果另做实验评测。</small></label></div>${formFooter('保存评估')}</form>`);
}

async function handleProofAction(action,data){
  if(action==='proof-open')await openVerification(data.id);
  else if(action==='proof-job'){$('#modal').close();await openJob(data.id);}
  else if(action==='proof-retry'){await api(`/jobs/${data.id}/verification/retry`,{method:'POST'});toast('已排队重新验证');$('#modal').close();await refresh();}
  else if(action==='proof-assess')formalAssessmentDialog(data.study,data.task);
  else if(action==='proof-tree'){
    let study=state.studies.find(item=>item.id===data.id);const visited=new Set();
    while(study?.config.parent_research_id&&!visited.has(study.id)){visited.add(study.id);study=state.studies.find(item=>item.id===study.config.parent_research_id);}
    treeView.rootId=study?.id||data.id;treeView.data=null;location.hash='tree';
  }else if(action==='proof-smoke'){
    const study=await api('/verification/smoke',{method:'POST'});treeView.rootId=study.id;treeView.data=null;
    toast('CPU 验证自检已启动；不调用模型');location.hash='tree';await refresh();
  }
}

function jobProofAction(job){
  const candidate=job.spec.verification?.candidate_id||job.id;
  if(!job.spec.verification&&!(state.verifications||[]).some(check=>check.candidate_id===candidate))return '';
  return `<div class="detail-heading"><h3>Lean 形式验证</h3><button class="button small" data-action="proof-open" data-id="${candidate}">查看证明报告</button></div>`;
}
