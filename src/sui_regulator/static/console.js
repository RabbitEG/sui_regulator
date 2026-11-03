'use strict';

// All ledger text is escaped. No model, log or handoff content becomes markup.
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = (v) => v ? String(v).slice(0, 16) : '—';
const fmt = (t) => t ? new Date(t * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
const clock = (t) => t ? new Date(t * 1000).toLocaleTimeString('zh-CN', {hour12:false}) : '—';
const duration = (n) => { n=Math.max(0,Math.floor(n || 0)); return n<60 ? n+' 秒' : n<3600 ? Math.floor(n/60)+' 分 '+n%60+' 秒' : Math.floor(n/3600)+' 小时 '+Math.floor(n%3600/60)+' 分'; };
const sum = (v) => Object.values(v || {}).reduce((a,b) => a+b,0);
const pickCount = (v, names) => names.reduce((n,k) => n+(v?.[k] || 0),0);
const paths = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  sessions:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m7 10 3 2-3 2m6 0h4"/>',
  tasks:'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M9 10h6m-6 5h6"/>',
  incidents:'<path d="M12 3 2 20h20L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
  receipt:'<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Zm3 5h6m-6 4h6"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5m10-3a8 8 0 0 0-14-3m0 9a8 8 0 0 0 14 3"/>',
  pause:'<path d="M8 5v14M16 5v14"/>', play:'<path d="m8 4 12 8-12 8V4Z"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>', back:'<path d="M19 12H5m5-5-5 5 5 5"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  chart:'<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
};
const icon = (name) => `<svg class='icon' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>${paths[name] || paths.tasks}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML=icon(el.dataset.icon));
const actionNames = {observe:'记录',warn:'告警',probe:'只读探测',bounded_retry:'有限重试',fresh_session:'新 Sui Proxy',wait:'等待',quarantine:'隔离'};
const statusNames = {HEALTHY:'心跳正常',FROZEN:'进程冻结',NOT_SENT:'未发送',LOST:'失联',DELAYED:'心跳延迟',UNVERIFIED:'未核验',HISTORICAL:'历史记录',RUNNING:'运行中',STARTING:'启动中',READY:'就绪',ROTATING:'交接中',HOLD:'已暂停',WAITING:'等待恢复',QUARANTINED:'已隔离',COMPLETED:'已完成',FAILED:'故障退出',RETIRED:'已轮换',REVOKED:'已撤销',EXITED:'已退出',UNKNOWN:'结果未知',BLOCKED:'资源阻塞',QUEUED:'已接收',SUCCEEDED:'已应用',REJECTED:'已拒绝',PENDING:'待处理',APPLIED:'已执行',OBSERVED:'已记录',STALE:'过时代际',IGNORED:'已忽略',SENDING:'发送中',UNCONFIRMED:'送达未确认'};
const badge = (v, live=false) => {
  const color = ['RUNNING','HEALTHY','SUCCEEDED'].includes(v) ? 'green' : ['COMPLETED','APPLIED','ACTIVE'].includes(v) ? 'blue' : ['FAILED','LOST','FROZEN','QUARANTINED','REJECTED'].includes(v) ? 'red' : ['UNKNOWN','BLOCKED','WAITING','DELAYED','UNCONFIRMED','PENDING','ROTATING','STARTING','QUEUED'].includes(v) ? 'orange' : 'neutral';
  return `<span class='badge ${color}' data-live='${live}'>${live ? '<span class="dot"></span>' : ''}${esc(statusNames[v] || v)}</span>`;
};
const taskLink = (id, title) => `<a href='#/tasks/${encodeURIComponent(id)}'>${esc(title || short(id))}</a>`;
const proxyLink = (p) => `#/sessions/${encodeURIComponent(p.task_id)}/${encodeURIComponent(p.id)}`;
const incidentLink = (i) => `#/incidents/${encodeURIComponent(i.task_id)}/${i.sequence}`;
const empty = (title, message='') => `<div class='empty'>${icon('overview')}<p>${esc(title)}</p>${esc(message)}</div>`;
const details = (name, value, key=name) => `<details class='json-details' data-preserve='${esc(key)}'><summary>${esc(name)}</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;
const panel = (title, body, subtitle='', action='') => `<section class='panel'><div class='panel-head'><h2>${title}${subtitle ? `<span>${subtitle}</span>` : ''}</h2>${action}</div>${body}</section>`;
const state={snapshot:null,transport:null,readMono:0,offset:0,limit:20,search:'',filter:'',task:null,incident:null,events:[],jobId:null,jobData:null,receipts:[],workspaceId:null,paused:false,interval:5000,loading:false,pending:false,error:null,failures:0,modal:null,submitting:false};
Object.assign(state,{transcript:null,transcriptItems:[],transcriptAfter:0,transcriptError:null,transcriptDiscarded:0,transcriptFilter:'all',followTranscript:true});
try {const size=localStorage.getItem('yan-console-text-size');if(['normal','large','xlarge'].includes(size)) document.documentElement.dataset.textSize=size;}catch(_){}
$('reading-size').value=document.documentElement.dataset.textSize || 'normal';
$('reading-size').addEventListener('change',e=>{document.documentElement.dataset.textSize=e.target.value;try{localStorage.setItem('yan-console-text-size',e.target.value);}catch(_){}});
let timer, toastTimer;
function selectWorkspace(id){
  if(state.workspaceId===id) return;
  state.workspaceId=id;
  try {const data=JSON.parse(localStorage.getItem('yan-console-receipts:'+id) || '[]');state.receipts=Array.isArray(data) ? data.slice(0,20).map(i=>i.status==='SENDING' ? {...i,status:'UNCONFIRMED'}:i):[];}catch(_){state.receipts=[];}
}

const route = () => {
  try { const p=(location.hash.replace(/^#\/?/,'') || 'overview').split('/').map(decodeURIComponent); return ['overview','sessions','tasks','incidents'].includes(p[0]) ? p : ['overview']; }
  catch (_) { return ['overview']; }
};
const healthFresh = () => {
  const c=state.snapshot?.controller;
  return !!(state.transport?.available && c?.state==='ONLINE' && performance.now()-state.readMono < state.interval+c.stale_after_seconds*1000);
};
const live = () => healthFresh() && !state.paused && !document.hidden;
const canControl = () => healthFresh() && !state.submitting;
const storeReceipts = () => {try {localStorage.setItem('yan-console-receipts:'+state.workspaceId,JSON.stringify(state.receipts.slice(0,20)));} catch (_) {/* A private browser may disable storage. */}};
function notice(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4000);}

async function http(path, body){
  const abort=new AbortController(), timeout=setTimeout(()=>abort.abort(),4500);
  try {
    const response=await fetch(path,{method:body ? 'POST':'GET',cache:'no-store',signal:abort.signal,headers:body ? {'Content-Type':'application/json','X-Yan-Console':'1'} : {},body:body ? JSON.stringify(body) : undefined});
    const data=await response.json();
    if(data.error) {const e=new Error(data.error.message);e.code=data.error.code;throw e;}
    return data;
  } finally {clearTimeout(timeout);}
}
function result(data){
  if(data.connection && !data.connection.available) state.transport=data.connection;
  if(data.response?.error){const e=new Error(data.response.error.message);e.code=data.response.error.code;throw e;}
  if(!data.response) throw new Error('Controller 不可达，尚无此查询的历史快照。');
  return data.response.result;
}
async function rpc(method,params){return result(await http('/api/rpc',{request:{method:'yan.'+method,params}}));}
function schedule(){clearTimeout(timer);if(!state.paused && !document.hidden) timer=setTimeout(refresh,Math.min(30000,state.interval*Math.max(1,2**Math.min(state.failures,3))));}
async function refresh(){
  if(state.loading){state.pending=true;return;}
  clearTimeout(timer);state.loading=true;const key=location.hash, r=route();$('main').setAttribute('aria-busy','true');
  try {
    const data=await http(`/api/snapshot?section=${r[0]}&offset=${state.offset}&limit=${r[0]==='overview' ? 6:state.limit}`);
    if(key!==location.hash) return;
    state.transport=data.connection;state.snapshot=result(data);selectWorkspace(state.snapshot.workspace_id);state.readMono=performance.now();state.error=null;
    if(r.length>1){
      if(r[0]==='incidents'){
        const rows=await rpc('get_incidents',{task_id:r[1],after:Number(r[2])-1,limit:1});
        if(rows[0]?.sequence!==Number(r[2])) throw new Error('此 Incident 不存在。');state.incident=rows[0];
      }
      const task=await rpc('get_task',{task_id:r[1]});if(key!==location.hash) return;state.task=task;
      if(r[0]==='sessions' && r[2]){
        try {
          const transcript=await rpc('get_proxy_transcript',{task_id:r[1],proxy_id:r[2],after:state.transcriptAfter,limit:40});
          if(key!==location.hash) return;
          state.transcript=transcript;state.transcriptError=null;
          const known=new Set(state.transcriptItems.map(item=>item.id));
          state.transcriptItems.push(...transcript.items.filter(item=>!known.has(item.id)));
          const excess=Math.max(0,state.transcriptItems.length-400);state.transcriptDiscarded+=excess;
          if(excess) state.transcriptItems.splice(0,excess);
          state.transcriptAfter=transcript.next_after;
        }catch(e){if(key!==location.hash) return;state.transcriptError=e.message;}
      }
      if(r[0]==='tasks' || r[0]==='sessions'){
        const after=state.events.length ? state.events[state.events.length-1].id:0;
        const events=await rpc('get_events',{task_id:r[1],after,limit:50});
        state.events=[...state.events,...events].slice(-500);
      }
      if(state.jobId){const metric=await rpc('get_metrics',{job_id:state.jobId});const logs=await rpc('get_logs',{job_id:state.jobId,limit:60});state.jobData={metric,logs};}
    }
    await pollReceipts();state.failures=data.connection.available ? 0:state.failures+1;
  } catch(e){
    if(key===location.hash){state.error=e.name==='AbortError' ? '查询超时，当前数据未重新核验。':e.message;if(!e.code || ['CONTROLLER_UNREACHABLE','DELIVERY_UNCONFIRMED'].includes(e.code)) state.transport={available:false,error:{message:state.error}};state.failures++;}
  } finally {
    state.loading=false;if(key===location.hash) render();$('main').setAttribute('aria-busy','false');
    if(state.pending || key!==location.hash){state.pending=false;queueMicrotask(refresh);}else schedule();
  }
}
async function pollReceipts(){
  if(!state.transport?.available) return;
  for(const item of state.receipts.filter(i=>i.id && ['QUEUED','EXECUTING'].includes(i.status)).slice(0,3)){
    try {const receipt=await rpc('get_command_status',{command_id:item.id});Object.assign(item,{status:receipt.status,result:receipt.result,error:receipt.error,updated_at:receipt.updated_at});} catch(e){item.query_error=e.message;}
  }
  storeReceipts();renderReceipts();
}
function updateHealth(){
  const c=state.snapshot?.controller, healthy=live();document.body.classList.toggle('stale',!healthy);
  let label='连接中', color='neutral', message='';
  if(!state.transport?.available && (state.snapshot || state.error)){label='Controller 离线';color='red';message='Controller 不可达。以下保留最后成功读取的快照，运行状态未重新核验，人工操作已停用。';}
  else if(c && c.state!=='ONLINE'){label=c.state==='STARTING' ? 'Controller 启动中':'Controller tick 过期';color='orange';message='Yan RPC 可达，但 Controller 尚无新近完成的 tick。当前执行状态未获确认，人工操作已停用。';}
  else if(state.paused || document.hidden){label='刷新已暂停';message='自动刷新已暂停。以下为历史快照；恢复刷新或立即刷新以重新核验。';}
  else if(c && !healthFresh()){label='状态待核验';color='orange';message='已超过刷新时限，当前运行状态未重新核验。';}
  else if(healthy){label='Candleholder 在线';color='green';}
  $('controller-badge').className='badge '+color;$('controller-badge').innerHTML=(healthy ? '<span class="dot green"></span>':'')+esc(label);
  $('side-state').textContent=healthy ? 'Candleholder 正常':label;$('side-dot').className='dot '+(healthy ? 'green':color==='red' ? 'red':'');
  const banner=$('connection-banner');banner.hidden=!message;banner.textContent=message+(state.error ? ' '+state.error:'');
  $('updated-at').textContent=state.snapshot ? '最后读取 '+fmt(state.snapshot.queried_at)+(healthy ? ' · 自动同步':' · 快照'):'尚未读取账本';
  const evidenceState=document.querySelector('[data-evidence-fresh]');if(evidenceState) evidenceState.textContent=healthy ? '查询时核验':'历史快照 · 待重新核验';
  document.querySelectorAll('[data-requires-live]').forEach(el=>el.disabled=!canControl() || el.dataset.eligible==='false');
}
function pager(){const s=state.snapshot;return `<div class='pagination'><span>${s.total ? `${s.offset+1}–${Math.min(s.offset+s.limit,s.total)}`:'0'} / 共 ${s.total} 条 · 新记录优先</span><div class='actions'><button class='button secondary' data-page='prev' ${s.offset===0 ? 'disabled':''}>上一页</button><button class='button secondary' data-page='next' ${s.offset+s.limit>=s.total ? 'disabled':''}>下一页</button></div></div>`;}
function table(headers,rows){return `<div class='table-wrap'><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan='${headers.length}'>${empty('当前没有记录')}</td></tr>`}</tbody></table></div>`;}
function taskRows(items){return items.map(t=>`<tr><td><a class='row-title' href='#/tasks/${encodeURIComponent(t.id)}'>${esc(t.title)}</a><span class='sub'>${esc(short(t.id))}</span></td><td>${badge(t.has_unknown_job ? 'UNKNOWN':t.status,t.status==='RUNNING' && !t.has_unknown_job)}${t.has_unknown_job ? '<span class="sub">Task '+esc(t.status)+'</span>':''}</td><td><span class='mono'>G${t.generation}</span><span class='sub'>${esc(t.adapter)}</span></td><td><span class='count-text'>${t.job_count} Job / ${t.execution_count} execution</span></td><td>${badge(t.desired)}</td><td><a class='table-link' href='#/tasks/${encodeURIComponent(t.id)}'>查看 ${icon('arrow')}</a></td></tr>`).join('');}
function incidentPreview(i){const p=i.payload;return `<article class='incident-preview'><div class='alert-mark'>${icon('incidents')}</div><div class='incident-body'><div class='incident-title'><a href='${incidentLink(i)}'>${esc(i.rule_id)} <span class='muted'>/ v${p.rule_version}</span></a>${badge(i.status)}</div><p>${esc(p.symptom)}</p><div class='decision-line'><span>建议 ${esc(actionNames[p.action] || p.action)}</span><span class='arrow'>→</span><span>最终 ${esc(actionNames[i.action] || i.action)}</span><span>· G${i.generation}</span><span>${clock(i.created_at)}</span></div></div></article>`;}
const eventNames={CONTROLLER_STARTED:'Candleholder 已启动',WORKER_CLAIMED:'新 Sui Proxy 领取任务',WORKER_FAILED:'Sui Proxy 故障，所有权撤销',WORKER_ROTATED:'Sui Proxy 完成交接轮换',JOB_STARTED:'Runner 已开始执行',JOB_COMPLETED:'Runner 观测 Job 完成',INCIDENT_DETECTED:'故障模式检出',INCIDENT_ACTION:'Candleholder 应用故障决策',PROGRESS_REPORT:'Sui Proxy 更新声明',COMMAND_ACCEPTED:'Yan 已接收指令',COMMAND_SUCCEEDED:'指令效果已应用',INSTRUCTION_RECORDED:'人工指令已记录',JOB_ATTACHED:'连接已有 Job',TASK_COMPLETED:'Task 已完成',TASK_QUARANTINED:'Task 自动恢复已隔离'};
function eventList(events,raw=false){return events.length ? `<div class='timeline'>${events.map(e=>`<article class='event'><span class='event-marker ${e.kind.includes('INCIDENT') || e.kind.includes('FAILED') ? 'red':''}'></span><div class='event-text'><div><span>${esc(eventNames[e.kind] || e.kind)}</span><time class='event-time'>${clock(e.created_at)}</time></div><p>${esc(e.data?.rule_id || e.data?.reason || e.data?.generation && 'Generation '+e.data.generation || e.source)} · ${esc(short(e.task_id || e.actor))}</p>${raw ? details('事件 #'+e.id+' · '+e.kind,e.data,'event-'+e.id):''}</div></article>`).join('')}</div>`:empty('暂无历史事件');}
function overview(){
  const s=state.snapshot,c=s.counts,active=pickCount(c.workers,['RUNNING','ROTATING','STARTING']),jobs=sum(c.jobs);
  const stats=[['活动代理会话',active,(sum(c.workers)-active)+' 个历史 Sui Proxy','sessions',''],['持久任务',sum(c.tasks),pickCount(c.tasks,['RUNNING','READY'])+' 个任务待推进','tasks',''],['运行中的 Job',c.jobs.RUNNING || 0,c.executions+' 条独立执行记录','chart','green'],['故障事件',sum(c.incidents),(c.incidents.APPLIED || 0)+' 次决策已执行','incidents','orange']];
  const statHtml=`<div class='stats'>${stats.map(([label,n,foot,ic,color])=>`<div class='stat'><div class='stat-top'>${label}<span class='stat-icon ${color}'>${icon(ic)}</span></div><div class='stat-number'>${n}</div><div class='stat-foot'>${foot}</div></div>`).join('')}</div>`;
  const controller=`<div class='controller-card'><div class='controller-title'><span class='controller-symbol'>${icon('shield')}</span><div><h3>Candleholder</h3><p>秉烛人 · 确定性监控与恢复</p></div></div><dl class='kv compact'><dt>最近完成 tick</dt><dd>${clock(s.controller.last_tick_at)}</dd><dt>运行时长</dt><dd>${duration(s.queried_at-s.controller.started_at)}</dd><dt>进程 PID</dt><dd class='mono'>${esc(s.controller.pid)}</dd><dt>恢复决策来源</dt><dd>版本规则 + 持久账本</dd></dl><div class='mini-divider'></div><p class='note'>代理会话更替时，Task 与独立 Runner 保留。结果未知的 Job 不会自动重新启动。</p><div class='bar-distribution'><svg viewBox='0 0 100 6' preserveAspectRatio='none'><rect width='${jobs ? (c.jobs.RUNNING || 0)/jobs*100:0}' height='6' fill='#7189eb'/><rect x='${jobs ? (c.jobs.RUNNING || 0)/jobs*100:0}' width='${jobs ? (c.jobs.UNKNOWN || 0)/jobs*100:0}' height='6' fill='#e3b36d'/></svg></div><div class='legend'><span><i></i>运行 ${c.jobs.RUNNING || 0}</span><span><i class='amber'></i>未知 ${c.jobs.UNKNOWN || 0}</span><span><i class='gray'></i>Job 总数 ${jobs}</span></div></div>`;
  return `<div class='page-heading'><div><div class='eyebrow'>CONTROL PLANE</div><h1>工作台</h1><p>代理会话、持久任务与自动恢复的当前状态。</p></div><a class='button secondary' href='#/sessions'>${icon('sessions')}查看 Sui Proxy</a></div>${statHtml}<div class='grid-main'>${panel('最近故障',s.recent_incidents.length ? s.recent_incidents.map(incidentPreview).join(''):empty('未检出故障','已有运行将持续接受确定性检测。'),'Recent incidents',`<a href='#/incidents'>全部故障 →</a>`)}${panel('监控与恢复',controller,'Candleholder')}</div>${panel('任务运行概览',table(['Task','执行视图','代理代际','执行账本','Desired',''],taskRows(s.items)),'Persistent tasks',`<a href='#/tasks'>全部任务 →</a>`)}${panel('最近控制事件',eventList(s.recent_events),'Audit trail')}`;
}
function listView(section){
  const s=state.snapshot, labels={sessions:['代理会话','SUI PROXIES','每个 Sui Proxy 是可替换的岁兽代理人；历史代际保留可追踪身份。'],tasks:['持久任务','PERSISTENT TASKS','用户意图、代理声明与 Runner 观测分别记录。'],incidents:['故障事件','INCIDENT REGISTRY','区分检测建议、Candleholder 最终动作与应用结果。']};
  let items=s.items.filter(i=>(JSON.stringify([i.id,i.rule_id,i.task_title,i.title,i.status,i.generation]).toLowerCase().includes(state.search.toLowerCase())) && (!state.filter || (section==='sessions' ? i.health:i.status)===state.filter));
  let headers,rows;
  if(section==='sessions'){
    headers=['Sui Proxy / Session','关联 Task','Generation','运行状态','最近心跳','运行时长',''];
    rows=items.map(p=>`<tr><td><a class='row-title mono' href='${proxyLink(p)}'>${esc(short(p.id))}</a><span class='sub'>${esc(short(p.session_id))} · ${esc(p.adapter)}</span></td><td>${taskLink(p.task_id,p.task_title)}<span class='sub'>${p.incident_count} Incident</span></td><td class='mono'>G${p.generation}${p.is_current ? '<span class="sub">CURRENT</span>':''}</td><td>${badge(p.health==='HISTORICAL' ? p.status:p.health,p.health==='HEALTHY')}</td><td class='nowrap'>${clock(p.heartbeat_at)}<span class='sub'>${duration(p.heartbeat_age_seconds)}前</span></td><td class='nowrap'>${duration(p.runtime_seconds)}</td><td><a class='table-link' href='${proxyLink(p)}'>查看对话 ${icon('arrow')}</a></td></tr>`).join('');
  }else if(section==='tasks') {headers=['Task','执行视图','代理代际','Job / execution','Desired',''];rows=taskRows(items);}
  else {headers=['Incident / 规则','关联 Task','Generation','检测建议','最终动作','应用结果',''];rows=items.map(i=>`<tr><td><a class='row-title mono' href='${incidentLink(i)}'>${esc(i.rule_id)} / v${i.payload.rule_version}</a><span class='sub'>${clock(i.created_at)} · ${esc(i.payload.category)}</span></td><td>${taskLink(i.task_id,i.task_title)}</td><td class='mono'>G${i.generation}</td><td>${esc(actionNames[i.payload.action] || i.payload.action)}</td><td>${esc(actionNames[i.action] || i.action)}</td><td>${badge(i.status)}</td><td><a class='table-link' href='${incidentLink(i)}'>证据 ${icon('arrow')}</a></td></tr>`).join('');}
  const options=section==='sessions' ? ['HEALTHY','LOST','FROZEN','DELAYED','HISTORICAL']:section==='tasks' ? ['RUNNING','HOLD','WAITING','QUARANTINED','COMPLETED']:['PENDING','APPLIED','OBSERVED','STALE','IGNORED'];
  const tools=`<div class='table-tools'><input id='list-search' class='filter-input' aria-label='本页搜索' placeholder='搜索本页名称或 ID…' value='${esc(state.search)}'><select id='list-filter' class='filter-select' aria-label='本页状态筛选'><option value=''>本页全部状态</option>${options.map(v=>`<option value='${v}' ${state.filter===v ? 'selected':''}>${esc(statusNames[v])}</option>`).join('')}</select></div>`;
  return `<div class='page-heading'><div><div class='eyebrow'>${labels[section][1]}</div><h1>${labels[section][0]}</h1><p>${labels[section][2]}</p></div><div class='actions'>${section==='tasks' ? `<button class='button primary' data-create-task data-requires-live data-eligible='true'>创建任务</button>`:''}<span class='heading-meta'>${s.total} 条账本记录</span></div></div>${panel(labels[section][0]+'列表',table(headers,rows)+pager(),'Ledger records',tools)}`;
}
function generationList(task){return `<div class='generation-list'>${[...(task.proxies || task.workers)].reverse().map(p=>`<div class='generation ${task.current_proxy===p.id || task.current_worker===p.id ? 'current':''}'><span class='generation-num'>G${p.generation}</span><div><a href='${proxyLink(p)}'>${esc(short(p.id))}</a><p>${fmt(p.created_at)} · ${esc(p.error || '已领取 Task')}</p></div>${badge(p.status,p.status==='RUNNING')}</div>`).join('')}</div>`;}
function intervention(task){const eligible=!['COMPLETED','CANCELLED'].includes(task.desired);return `<div class='actions'><button class='button secondary' data-command='hold_task' data-requires-live data-eligible='${eligible && task.desired==='ACTIVE' && !!task.current_worker}'>${icon('pause')}Hold</button><button class='button secondary' data-command='resume_task' data-requires-live data-eligible='${eligible && (task.desired==='HOLD' || ['WAITING','QUARANTINED'].includes(task.status))}'>${icon('play')}Resume</button><button class='button primary' data-command='instruct_task' data-requires-live data-eligible='${eligible}'>追加指令 ${icon('arrow')}</button></div>`;}
function metricChart(data){
  const points=data.logs.lines.map(line=>{try{return JSON.parse(line);}catch(_){return null;}}).filter(v=>v && Number.isFinite(v.step) && Number.isFinite(v.loss));
  if(points.length<2) return empty('尚无足够指标点','图表仅绘制 Runner 的实际记录。');
  const xs=points.map(v=>v.step),ys=points.map(v=>v.loss),lo=Math.min(...ys),hi=Math.max(...ys),xmin=Math.min(...xs),xmax=Math.max(...xs);
  const line=points.map(v=>`${10+(v.step-xmin)/Math.max(1,xmax-xmin)*780},${135-(v.loss-lo)/Math.max(1e-10,hi-lo)*120}`).join(' ');
  return `<div class='metrics-box'><div class='chart-labels'><span>loss ${hi.toPrecision(4)}</span><span>${points.length} 个实际采样点</span></div><svg class='metric-chart' viewBox='0 0 800 150' role='img' aria-label='Runner loss 指标曲线'><path d='M10 135h780' stroke='#e8edf7'/><polygon class='chart-area' points='10,135 ${line} 790,135'/><polyline class='chart-line' points='${line}'/></svg><div class='chart-labels'><span>step ${xmin}</span><span>step ${xmax} · loss ${lo.toPrecision(4)}</span></div>${details('当前指标 JSON',data.metric.metric,'metrics')}</div>`;
}
function jobsView(task){return task.jobs.length ? task.jobs.map(j=>{const m=j.metric || {},share=m.total_steps ? Math.min(100,m.step/m.total_steps*100):0;return `<article class='job-card'><div class='job-title'><div><h3 class='mono'>${esc(j.logical_key)}</h3><span class='sub mono break'>${esc(j.id)}</span></div>${badge(j.status,j.status==='RUNNING')}</div><div class='job-facts'><span>PID<b>${esc(j.pid)}</b></span><span>step<b>${esc(m.step || 0)} / ${esc(m.total_steps || '—')}</b></span><span>loss<b>${esc(m.loss)}</b></span><span>观测<b>${clock(j.observed_at)}</b></span></div><div class='progress'><svg viewBox='0 0 100 5' preserveAspectRatio='none'><rect width='${share}' height='5' fill='#7189e7'/></svg></div><div class='job-foot'><span>${j.status==='UNKNOWN' ? '结果未确认 · 禁止自动重启':j.stale ? '观测已陈旧':'来源：独立 Runner'} · exit ${esc(j.exit_code)}</span><button class='text-button' data-job='${esc(j.id)}'>${icon('chart')}查看指标</button></div>${state.jobId===j.id ? (state.jobData ? metricChart(state.jobData):empty('读取指标中…')):''}</article>`;}).join(''):empty('尚未登记 Job');}
function taskDetail(task){
  const d=task.declared || {},proxies=task.proxies || task.workers;
  const observed=task.jobs.length ? task.jobs.map(j=>badge(j.status,j.status==='RUNNING')).join(' '):'<span class="muted">尚无 Job 观测</span>';
  return `<a class='back-link' href='#/tasks'>${icon('back')}返回任务</a><div class='page-heading detail-heading'><div><div class='eyebrow'>TASK DETAIL</div><h1>${esc(task.title)}</h1><span class='sub'>${esc(task.id)} · ${esc(task.adapter)} · Generation ${task.generation}</span></div>${intervention(task)}</div><div class='state-grid'><div class='state-card desired'><div class='eyebrow'>DESIRED STATE</div><h3>用户意图</h3><div class='large-value'>${esc(task.desired)}</div><p>来源：Yan Command<br>执行视图 ${esc(task.status)}</p></div><div class='state-card declared'><div class='eyebrow'>DECLARED STATE · 未核验</div><h3>Sui Proxy 声明</h3><div class='large-value'>${esc(d.phase || '尚无声明')}</div><p class='summary'>${esc(d.summary || '代理尚未提交进度报告。')}</p><p>${fmt(d.updated_at)}</p></div><div class='state-card observed'><div class='eyebrow'>OBSERVED STATE</div><h3>Runner 观测</h3><div>${observed}</div><p>${task.jobs.length} 个 Job · ${task.execution_count} 条 execution<br>Proxy 更替不等于重新执行 Job。</p></div></div>${panel('任务书与验收',`<div class='task-brief'>${esc(task.objective)}</div><h3>验收条件</h3><div class='task-brief'>${esc(task.completion_criteria || '未单独定义；按任务书要求核验')}</div><dl class='kv'><dt>项目目录</dt><dd class='break'>${esc(task.workspace)}</dd><dt>执行能力</dt><dd>${esc(task.execution_profile || 'managed')}</dd><dt>完成前核验</dt><dd>${esc(task.verification_template || '未配置；语义完成为代理声明')}</dd></dl>`,'Operator requirements')}<div class='detail-columns'><div>${panel('Job 与指标',jobsView(task),'Runner observations')}${panel('历史事件',eventList([...state.events].reverse(),true)+`<div class='source-note'>已加载 ${state.events.length} 条；按 Event ID 增量读取，最多保留 500 条。<button class='text-button' data-action='events'>刷新 / 加载后续</button></div>`,'Audit trail')}</div><div>${panel('代理换代历史',generationList(task),'Generations')}${panel('最近故障',task.incidents.length ? [...task.incidents].slice(0,5).map(incidentPreview).join(''):empty('暂无故障'),'Incidents')}${panel('Handoff',proxies.filter(p=>p.handoff).map(p=>`<div class='handoff'><span class='mono'>G${p.generation} · ${esc(short(p.id))}</span>${details('查看结构化交接',p.handoff,'handoff-'+p.id)}</div>`).join('') || empty('尚无 Handoff','异常退出时可直接从账本恢复。'))}</div></div>`;
}
function proxyDetail(task,id){
  const p=(task.proxies || task.workers).find(p=>p.id===id);if(!p) return empty('此 Sui Proxy 不存在');
  const entries=[['Proxy ID',p.id],['Session UUID',p.session_id],['Generation','G'+p.generation],['运行时',p.adapter],['PID',p.pid],['心跳',fmt(p.heartbeat_at)],['最后活动',fmt(p.activity_at)],['运行时长',duration(p.runtime_seconds)],['创建',fmt(p.created_at)],['退出',fmt(p.ended_at)],['Stream',p.stream_observation],['关联 Task',task.title]];
  const incidents=task.incidents.filter(i=>(i.proxy_id || i.worker_id)===p.id);
  return `<a class='back-link' href='#/sessions'>${icon('back')}返回代理会话</a><div class='page-heading detail-heading'><div><div class='eyebrow'>SUI PROXY · 岁兽代理人</div><h1>Generation ${p.generation} ${badge(p.health==='HISTORICAL' ? p.status:p.health,p.health==='HEALTHY')}</h1><span class='sub'>${esc(p.id)}</span></div><a class='button secondary' href='#/tasks/${encodeURIComponent(task.id)}'>管理关联 Task ${icon('arrow')}</a></div>${executionEvidence(task,p)}${transcriptView(p)}<div class='detail-columns'><div>${panel('代理身份与活动',`<div class='panel-body'><dl class='kv'>${entries.map(([k,v])=>`<dt>${k}</dt><dd class='mono'>${esc(v)}</dd>`).join('')}</dl><div class='mini-divider'></div><p class='note'>最后活动由 Wrapper / 协议采集，不能单独证明科研进展。进程健康依据登记身份与 lease；离线时仅显示最后快照。</p>${details('登记的进程身份',{proxy:p.identity,runtime:p.child_identity},'identity')}</div>`,'Identity & heartbeat')}${panel('最近故障与处理',incidents.length ? incidents.map(incidentPreview).join(''):empty('此代际暂无故障'))}${p.handoff ? panel('Handoff',`<div class='panel-body'>${details('查看交接内容',p.handoff,'proxy-handoff')}</div>`):''}</div><div>${panel('同一 Task 的代际历史',generationList(task))}${panel('关联 Job',jobsView(task),'Persistent jobs')}</div></div>`;
}
function executionEvidence(task,p){
  const verified=live(), processLabel=(exists,osState)=>!verified ? '快照 · 待重新核验':['T','t'].includes(osState) ? '进程冻结':exists===true ? '进程身份匹配':exists===false ? '进程已退出':'没有进程观测';
  const job=task.jobs.find(j=>j.status==='RUNNING') || task.jobs[0], runtime=p.child_identity;
  return `<section class='panel execution-evidence' data-execution-evidence><div class='panel-head'><h2>实际运行证据</h2><span><b data-evidence-fresh>${verified ? '查询时核验':'历史快照'}</b> · ${fmt(task.queried_at)}</span></div><div class='evidence-grid'>
    <article><h3>代理包装进程</h3><strong>${processLabel(p.process_alive,p.process_state)}</strong><p>PID ${esc(p.pid)} · OS ${esc(p.process_state)}</p><span>来源：boot ID / PID / start ticks</span></article>
    <article><h3>模型运行时</h3><strong>${p.adapter==='mock' ? 'Mock · 未调用真实模型':processLabel(p.runtime_process_alive,p.runtime_process_state)}</strong><p>${p.adapter==='mock' ? '此会话只执行合成演示':'CLI PID '+esc(runtime?.pid)+' · OS '+esc(p.runtime_process_state)}</p><span>Stream：${esc(p.stream_observation)}</span></article>
    <article><h3>独立 Job</h3><strong>${job ? esc(statusNames[job.status] || job.status):'尚未登记 Job'}</strong><p>${job ? 'PID '+esc(job.pid)+' · '+processLabel(job.process_alive):'巡检 Task 可只调用工具，请核对下方实际返回'}</p><span>Task 共 ${task.execution_count} 条 execution</span></article>
    <article><h3>Runner 最近观测</h3><strong>${job?.metric?.step!=null ? 'step '+esc(job.metric.step):'尚无指标'}</strong><p>${job ? 'loss '+esc(job.metric?.loss)+' · '+fmt(job.observed_at):'请核对工具返回与实际运行结果'}</p><span>来源：独立 Runner；AI 自述单独记录</span></article>
    </div><p class='source-note'>进程存在、收到文本、工具返回与 Job 完成是不同事实。以下对话按实际采集内容展示，助手声称“完成”不会改写 Runner 的观测。暂停或离线时所有证据仅为快照。</p></section>`;
}
function transcriptView(proxy){
  const t=state.transcript, roles={user:'任务输入',assistant:'助手',tool:'工具返回',system:'运行时'};
  const groups=[];
  for(const item of state.transcriptItems){
    const previous=groups[groups.length-1];
    if(item.kind==='text' && item.message_id && previous?.kind==='text' && previous.message_id===item.message_id && previous.role===item.role){previous.text+=item.text;previous.truncated ||= item.truncated;}
    else groups.push({...item});
  }
  const filtered=groups.filter(item=>state.transcriptFilter==='all' || (state.transcriptFilter==='dialogue' ? item.kind==='text':['tool_call','tool_result'].includes(item.kind)));
  let content=filtered.map(item=>{
    const title=item.kind==='tool_call' ? '调用 '+(item.tool_name || '工具'):item.kind==='tool_result' ? (item.is_error ? '工具返回 · 失败':'工具返回')+(item.tool_name ? ' · '+item.tool_name:''):roles[item.role] || item.role;
    const body=['tool_call','tool_result','exit','runtime'].includes(item.kind) ? `<details class='message-tool' data-preserve='message-${item.id}'><summary>${item.kind==='tool_call' ? '查看调用参数':item.kind==='tool_result' ? '查看实际返回':'查看运行时记录'}</summary><pre>${esc(item.text)}</pre>${item.model ? `<p>CLI 报告模型：${esc(item.model)}</p>`:''}</details>`:`<div class='message-text'>${esc(item.text)}</div>`;
    return `<article class='session-message role-${esc(item.role)}' data-message-role='${esc(item.role)}'><header><strong>${esc(title)}</strong><time>${fmt(item.time)}</time></header>${body}${item.truncated ? '<p class="message-limit">本条内容已截断。</p>':''}<footer>${esc(item.source)}${item.call_id ? ' · call '+esc(item.call_id):''}</footer></article>`;
  }).join('');
  if(!content){const missing=t?.availability==='not_recorded';content=empty(missing ? '此会话没有留存对话':t?.availability==='unreadable' ? '会话记录无法读取':state.transcriptFilter!=='all' ? '当前筛选下没有记录':'等待会话输出',missing ? '记录功能只采集升级后启动的新代理。历史元数据仍可查看，不会根据事件编造对话。':'新记录会随自动刷新读取；仅在代理有实际输出时出现。');}
  const latest=state.transcriptItems.at(-1);
  return `<section class='panel session-transcript' data-session-transcript><div class='panel-head'><h2>会话对话与工具轨迹</h2><span>${proxy.adapter==='mock' ? '合成演示 · 非真实模型':'Claude CLI 可见输出 · 只读'}</span></div><div class='transcript-toolbar'><label>查看 <select id='transcript-filter'><option value='all' ${state.transcriptFilter==='all' ? 'selected':''}>全部记录</option><option value='dialogue' ${state.transcriptFilter==='dialogue' ? 'selected':''}>任务输入与对话</option><option value='tools' ${state.transcriptFilter==='tools' ? 'selected':''}>工具调用与返回</option></select></label><label><input id='transcript-follow' type='checkbox' ${state.followTranscript ? 'checked':''}> 跟随新消息</label><button class='button secondary small' data-action='transcript'>${t?.has_more ? '继续加载后续记录':'刷新会话记录'}</button></div>
    ${state.transcriptError ? `<p class='notice warning'>会话记录未刷新：${esc(state.transcriptError)}</p>`:''}${t?.limited ? '<p class="notice warning">本代际记录已达 4 MiB 上限，后续内容未保存。</p>':''}
    <div class='transcript-list' data-transcript-list tabindex='0' aria-label='会话记录阅读区'>${content}</div><div class='source-note'>最近采集 ${latest ? fmt(latest.time):'—'} · 已加载 ${state.transcriptItems.length} 条${state.transcriptDiscarded ? '（本页已释放更早 '+state.transcriptDiscarded+' 条，可重新进入从头加载）':''}。对话是会话记录，不是 Job 成功证明；工具内容可展开核验。记录为空不代表代理未启动。</div></section>`;
}
function incidentDetail(i){const p=i.payload;return `<a class='back-link' href='#/incidents'>${icon('back')}返回故障事件</a><div class='page-heading detail-heading'><div><div class='eyebrow'>INCIDENT DETAIL</div><h1>${esc(i.rule_id)} <span class='muted'>/ v${p.rule_version}</span></h1><span class='sub'>${esc(i.id)} · ${fmt(i.created_at)}</span></div>${badge(i.status)}</div>${panel('从检出到处理',`<div class='incident-flow'><div class='flow-step'><small>01 · Detection Engine 建议</small><h3>${esc(actionNames[p.action] || p.action)}</h3><p>规则建议，尚不代表已执行</p></div><div class='flow-step'><small>02 · Candleholder 最终动作</small><h3>${esc(actionNames[i.action] || i.action)}</h3><p>${i.applied_at ? fmt(i.applied_at):'等待处理'}</p></div><div class='flow-step'><small>03 · 应用结果</small><h3>${esc(i.status)}</h3><p>${esc(statusNames[i.status])} · ${i.status==='APPLIED' ? '控制状态已落盘':'以持久处理回执为准'}</p></div></div>`)}<div class='detail-columns'><div>${panel('检测证据',`<div class='panel-body'><p class='summary'>${esc(p.symptom)}</p><div class='mini-divider'></div><pre class='evidence'>${esc(JSON.stringify(p.evidence,null,2))}</pre><p class='note'>证据保存计数与摘要。confidence 为确定性启发式分数，不是经校准的概率。</p></div>`,'Evidence')}${panel('应用回执',`<div class='panel-body'><pre class='evidence'>${esc(JSON.stringify(i.outcome,null,2))}</pre>${details('完整检测建议',p,'incident-payload')}</div>`,'Outcome')}</div><div>${panel('关联与保护',`<div class='panel-body'><dl class='kv compact'><dt>Task</dt><dd>${taskLink(i.task_id,state.task?.title)}</dd><dt>Sui Proxy</dt><dd><a class='mono' href='#/sessions/${encodeURIComponent(i.task_id)}/${encodeURIComponent(i.proxy_id || i.worker_id)}'>${esc(short(i.proxy_id || i.worker_id))}</a></dd><dt>Generation</dt><dd>G${i.generation}</dd><dt>类别 / 严重度</dt><dd>${esc(p.category)} / ${esc(p.severity)}</dd><dt>推测来源</dt><dd>${esc(p.suspected_origin)}</dd><dt>根因已确认</dt><dd>${p.cause_confirmed ? '是':'否'}</dd><dt>保留 Task / Job</dt><dd>${p.preserve_task && p.preserve_running_jobs ? '是':'见规则'}</dd><dt>恢复预算</dt><dd>${p.max_attempts} 次 / 规则窗口</dd></dl></div>`)}<div class='notice info'>检测建议与最终动作可能不同：预算耗尽、结果未决或用户意图会阻止自动接管。</div></div></div>`;}
function render(){
  const r=route(),titles={overview:'工作台',sessions:'代理会话',tasks:'持久任务',incidents:'故障事件'};
  $('breadcrumb').textContent=titles[r[0]];document.title=titles[r[0]]+' · Yan Console';
  document.querySelectorAll('[data-nav]').forEach(el=>{el.classList.toggle('active',el.dataset.nav===r[0]);if(el.dataset.nav===r[0]) el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  if(state.snapshot){const c=state.snapshot.counts;$('nav-sessions').textContent=sum(c.workers);$('nav-tasks').textContent=sum(c.tasks);$('nav-incidents').textContent=sum(c.incidents);}
  const focused=document.activeElement,focusId=focused?.id,selection=focused?.selectionStart;
  const open=[...document.querySelectorAll('details[open][data-preserve]')].map(el=>el.dataset.preserve);
  const transcriptScroll=document.querySelector('[data-transcript-list]')?.scrollTop || 0;
  let html;
  if(!state.snapshot) html=empty(state.error ? '无法读取账本':'等待 Controller 连接',state.error || '启动同一状态目录的 Controller 后，点击立即刷新。');
  else if(r.length>1) html=state.task ? (r[0]==='tasks' ? taskDetail(state.task):r[0]==='sessions' ? proxyDetail(state.task,r[2]):state.incident ? incidentDetail(state.incident):empty('正在读取故障详情…')):empty('正在读取详情…',state.error || '');
  else html=r[0]==='overview' ? overview():listView(r[0]);
  $('main').innerHTML=(state.error && state.snapshot ? `<div class='notice warning'>${esc(state.error)}</div>`:'')+html;
  document.querySelectorAll('[data-preserve]').forEach(el=>{if(open.includes(el.dataset.preserve)) el.open=true;});
  const reader=document.querySelector('[data-transcript-list]');if(reader) reader.scrollTop=state.followTranscript ? reader.scrollHeight:transcriptScroll;
  if(focusId && $(focusId) && ['list-search','list-filter'].includes(focusId)){ $(focusId).focus({preventScroll:true});if(selection!=null && $(focusId).setSelectionRange) $(focusId).setSelectionRange(selection,selection);}
  updateHealth();renderReceipts();
}
function renderReceipts(){
  $('receipt-count').textContent=state.receipts.length;
  $('receipt-list').innerHTML=state.receipts.length ? state.receipts.map(i=>`<article class='receipt-item ${['REJECTED','UNCONFIRMED'].includes(i.status) ? 'error':''}'><div><h3>${esc({submit_task:'创建任务',hold_task:'Hold',resume_task:'Resume',instruct_task:'追加指令'}[i.kind])}</h3>${badge(i.status)}</div><p>${esc(i.title || short(i.task_id))}</p><code>${esc(i.id || i.key)}</code><span class='sub'>${fmt(i.updated_at || i.created_at)}</span>${i.error ? `<p>${esc(i.error)}</p>`:''}${i.result ? details('应用结果',i.result,'receipt-'+i.key):''}${['UNCONFIRMED','NOT_SENT'].includes(i.status) ? `<button class='text-button' data-retry='${esc(i.key)}'>使用原幂等键核验 / 重试</button>`:''}</article>`).join(''):empty('尚无本浏览器提交的指令','历史操作仍保留在 Task 的审计事件中。');
}
function openCommand(kind,old=null){
  if(kind==='submit_task'){openTask(old);return;}
  if(!old && !state.task) return;
  state.modal=old || {key:crypto.randomUUID(),kind,task_id:state.task.id,title:state.task.title,created_at:Date.now()/1000};
  const names={hold_task:'暂停代理 · Hold',resume_task:'恢复任务 · Resume',instruct_task:'向 Task 追加指令'};
  $('dialog-title').textContent=names[kind];$('dialog-description').textContent=kind==='hold_task' ? 'Candleholder 将撤销当前代理所有权并停止代理会话。已启动的 Job 继续运行。':kind==='resume_task' ? '恢复 Task 的 ACTIVE 意图。Candleholder 将按状态与预算分配新 Sui Proxy，并核验已有 Job。':'指令写入持久 Task；回执成功表示已记录要求，不代表 AI 已完成分析。';
  $('dialog-target').innerHTML=esc(state.modal.title)+`<code>${esc(state.modal.task_id)}</code>`;
  $('instruction').hidden=kind!=='instruct_task';$('instruction-label').hidden=kind!=='instruct_task';$('instruction').value=old?.instruction || '';
  $('confirm-label').hidden=kind!=='hold_task';$('hold-confirm').checked=false;$('dialog-error').hidden=true;$('dialog-submit').textContent=kind==='hold_task' ? '确认 Hold':kind==='resume_task' ? '确认 Resume':'提交指令';$('dialog-submit').disabled=false;
  $('command-dialog').showModal();if(kind==='instruct_task') $('instruction').focus();
}
async function submitCommand(e){
  e.preventDefault();if(state.submitting || !state.modal) return;
  const item=state.modal, kind=item.kind;
  if(kind==='hold_task' && !$('hold-confirm').checked){$('dialog-error').textContent='请明确确认停止当前 Sui Proxy；Job 将保留。';$('dialog-error').hidden=false;return;}
  if(kind==='instruct_task' && !$('instruction').value.trim()){ $('instruction').focus();return;}
  item.instruction=kind==='instruct_task' ? $('instruction').value.trim():undefined;
  if(!state.receipts.includes(item)) state.receipts.unshift(item);state.receipts=state.receipts.slice(0,20);
  item.status='SENDING';state.submitting=true;$('dialog-submit').disabled=true;$('dialog-error').hidden=true;storeReceipts();renderReceipts();
  try {
    const data=await http('/api/rpc',{request:{method:'yan.'+kind,params:{task_id:item.task_id,idempotency_key:item.key,payload:kind==='instruct_task' ? {instruction:item.instruction}:{}}},confirmed:kind==='hold_task'});
    const receipt=result(data);Object.assign(item,{id:receipt.id,status:receipt.status,error:receipt.error,result:receipt.result,updated_at:receipt.updated_at});
    $('command-dialog').close();$('receipts').hidden=false;notice('指令已接收，可通过 Command Receipt 核验效果。');
  }catch(err){item.status=err.code && err.code!=='DELIVERY_UNCONFIRMED' ? 'NOT_SENT':'UNCONFIRMED';item.error=err.code ? err.code+': '+err.message:err.message;$('dialog-error').textContent=item.error+'。保留原幂等键，避免重复提交。';$('dialog-error').hidden=false;}
  finally{state.submitting=false;$('dialog-submit').disabled=false;storeReceipts();renderReceipts();refresh();}
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-create-task]') && canControl()) openTask();
  const command=e.target.closest('[data-command]');if(command && !command.disabled) openCommand(command.dataset.command);
  const retry=e.target.closest('[data-retry]');if(retry){const old=state.receipts.find(i=>i.key===retry.dataset.retry);if(old) openCommand(old.kind,old);}
  const page=e.target.closest('[data-page]');if(page && !page.disabled){state.offset=Math.max(0,state.offset+(page.dataset.page==='next' ? state.limit:-state.limit));refresh();}
  const job=e.target.closest('[data-job]');if(job){state.jobId=job.dataset.job;state.jobData=null;refresh();}
  if(e.target.closest('[data-action="events"]')) refresh();
  if(e.target.closest('[data-action="transcript"]')) refresh();
});
document.addEventListener('input',e=>{if(e.target.id==='list-search'){state.search=e.target.value;render();}});
document.addEventListener('change',e=>{if(e.target.id==='list-filter'){state.filter=e.target.value;render();}});
document.addEventListener('change',e=>{if(e.target.id==='transcript-filter'){state.transcriptFilter=e.target.value;render();}if(e.target.id==='transcript-follow'){state.followTranscript=e.target.checked;render();}});
document.addEventListener('scroll',e=>{const el=e.target;if(el.matches?.('[data-transcript-list]') && el.scrollTop+el.clientHeight<el.scrollHeight-40){state.followTranscript=false;if($('transcript-follow')) $('transcript-follow').checked=false;}},true);
$('refresh-now').addEventListener('click',refresh);
$('pause-refresh').addEventListener('click',()=>{state.paused=!state.paused;$('pause-refresh').innerHTML=icon(state.paused ? 'play':'pause')+`<span>${state.paused ? '恢复':'暂停'}</span>`;if(state.paused) clearTimeout(timer);else refresh();updateHealth();});
$('refresh-interval').addEventListener('change',e=>{state.interval=Number(e.target.value);schedule();updateHealth();});
$('receipt-toggle').addEventListener('click',()=>$('receipts').hidden=!$('receipts').hidden);$('receipt-close').addEventListener('click',()=>$('receipts').hidden=true);
const closeDialog=()=>{if(!state.submitting) $('command-dialog').close();};$('dialog-close').addEventListener('click',closeDialog);$('dialog-cancel').addEventListener('click',closeDialog);$('command-dialog').addEventListener('cancel',e=>{if(state.submitting)e.preventDefault();});$('command-form').addEventListener('submit',submitCommand);
window.addEventListener('hashchange',()=>{state.offset=0;state.search='';state.filter='';state.task=null;state.incident=null;state.events=[];state.jobId=null;state.jobData=null;state.error=null;state.transcript=null;state.transcriptItems=[];state.transcriptAfter=0;state.transcriptError=null;state.transcriptDiscarded=0;state.transcriptFilter='all';state.followTranscript=true;$('main').innerHTML='<div class="loading"><span class="loading-dot"></span>正在读取账本…</div>';refresh();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);updateHealth();}else if(!state.paused) refresh();});
setInterval(updateHealth,1000);renderReceipts();refresh();

let taskDraft=null;
const taskFields={title:'task-title',objective:'task-objective',completion_criteria:'task-criteria',workspace:'task-workspace',adapter:'task-adapter',execution_profile:'task-profile',verification_template:'task-verifier'};
function openTask(old=null){
  if(!canControl()) return;
  taskDraft=old || {kind:'submit_task',key:crypto.randomUUID(),created_at:Date.now()/1000};
  for(const [field,id] of Object.entries(taskFields)){
    if(old?.payload) $(id).value=old.payload[field] || (field==='adapter' ? 'claude':field==='execution_profile' ? 'managed':'');
    $(id).disabled=!!old?.payload;
  }
  $('task-error').hidden=true;$('task-submit').disabled=false;$('task-dialog').showModal();
}
async function submitTask(event){
  event.preventDefault();if(state.submitting || !taskDraft) return;
  const item=taskDraft;
  if(!item.payload){
    item.payload=Object.fromEntries(Object.entries(taskFields).map(([field,id])=>[field,$(id).value]));
    if(!item.payload.workspace.trim()) delete item.payload.workspace;
    if(item.payload.adapter==='mock') item.payload.execution_profile='managed';
    item.title=item.payload.title;
  }
  if(!state.receipts.includes(item)) state.receipts.unshift(item);
  item.status='SENDING';state.submitting=true;$('task-submit').disabled=true;
  for(const id of Object.values(taskFields)) $(id).disabled=true;
  storeReceipts();renderReceipts();
  try{
    const receipt=result(await http('/api/rpc',{request:{method:'yan.submit_task',params:{idempotency_key:item.key,payload:item.payload}}}));
    Object.assign(item,{id:receipt.id,task_id:receipt.task_id,status:receipt.status,error:receipt.error,result:receipt.result,updated_at:receipt.updated_at});
    $('task-dialog').close();$('receipts').hidden=false;notice('任务已提交；通过回执确认应用结果。');
  }catch(err){
    item.status=err.code && err.code!=='DELIVERY_UNCONFIRMED' ? 'NOT_SENT':'UNCONFIRMED';item.error=err.message;
    $('task-error').textContent=err.message+'；重试保留原任务书和幂等键。';$('task-error').hidden=false;
  }finally{state.submitting=false;$('task-submit').disabled=false;storeReceipts();renderReceipts();refresh();}
}
$('task-form').addEventListener('submit',submitTask);
for(const id of ['task-close','task-cancel']) $(id).addEventListener('click',()=>{if(!state.submitting)$('task-dialog').close();});
$('task-dialog').addEventListener('cancel',e=>{if(state.submitting)e.preventDefault();});
