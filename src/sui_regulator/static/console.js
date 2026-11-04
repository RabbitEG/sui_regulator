'use strict';

// UI copy is localized explicitly. Recorded Markdown uses the local sanitized renderer.
const tr = YanI18n.tr;
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = (v) => v ? String(v).slice(0, 16) : '—';
const fmt = (t) => t ? new Date(t * 1000).toLocaleString(YanI18n.locale, {hour12:false}) : '—';
const clock = (t) => t ? new Date(t * 1000).toLocaleTimeString(YanI18n.locale, {hour12:false}) : '—';
const duration = n => {n=Math.max(0,Math.floor(Number(n) || 0));return n<60 ? tr('{n} 秒','{n}s',{n}):n<3600 ? tr('{m} 分 {s} 秒','{m}m {s}s',{m:Math.floor(n/60),s:n%60}):tr('{h} 小时 {m} 分','{h}h {m}m',{h:Math.floor(n/3600),m:Math.floor(n%3600/60)});};
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
const statusNames={HEALTHY:['心跳正常','Healthy'],FROZEN:['进程冻结','Frozen'],NOT_SENT:['未发送','Not sent'],LOST:['失联','Lost'],DELAYED:['心跳延迟','Delayed'],UNVERIFIED:['未核验','Unverified'],HISTORICAL:['历史记录','Historical'],RUNNING:['运行中','Running'],STARTING:['启动中','Starting'],READY:['就绪','Ready'],ROTATING:['交接中','Handing off'],HOLD:['已暂停','On hold'],WAITING:['等待恢复','Waiting'],QUARANTINED:['已隔离','Quarantined'],COMPLETED:['已完成','Completed'],FAILED:['失败退出','Failed'],STOPPED:['已停止','Stopped'],RETIRED:['已轮换','Retired'],REVOKED:['已撤销','Revoked'],EXITED:['已退出','Exited'],UNKNOWN:['结果未知','Unknown'],BLOCKED:['资源阻塞','Blocked'],QUEUED:['已接收','Queued'],SUCCEEDED:['已应用','Applied'],REJECTED:['已拒绝','Rejected'],PENDING:['待处理','Pending'],APPLIED:['已执行','Applied'],OBSERVED:['已记录','Observed'],STALE:['过时代际','Stale generation'],IGNORED:['已忽略','Ignored'],SENDING:['发送中','Sending'],UNCONFIRMED:['送达未确认','Delivery unconfirmed'],RETRY_BACKOFF:['退避等待','Retry backoff'],WAITING_RESOURCE:['等待资源或凭据','Waiting for resource / credentials'],REPLAN:['调整策略','Replan'],ACTIVE:['继续执行','Active'],CANCELLED:['已取消','Cancelled'],EXECUTING:['应用中','Applying']};
const actionNames={observe:['记录','Observe'],warn:['告警','Warn'],probe:['只读探测','Read-only probe'],bounded_retry:['有限重试','Bounded retry'],fresh_session:['新代理会话','New Sui Proxy'],wait:['等待','Wait'],quarantine:['隔离','Quarantine'],replan:['策略建议','Replan advice'],advise:['任务观察','Task observation']};
const eventNames={CONTROLLER_STARTED:['秉烛人已启动','Candleholder started'],WORKER_CLAIMED:['新代理领取任务','New Sui Proxy claimed task'],WORKER_FAILED:['代理故障，所有权撤销','Proxy failed; ownership revoked'],WORKER_ROTATED:['代理完成交接轮换','Proxy handed off'],JOB_STARTED:['执行器已开始执行','Runner started'],JOB_COMPLETED:['观测到作业完成','Job completion observed'],INCIDENT_DETECTED:['故障模式检出','Incident detected'],INCIDENT_ACTION:['秉烛人应用故障决策','Candleholder applied decision'],PROGRESS_REPORT:['代理更新声明','Proxy declaration updated'],COMMAND_ACCEPTED:['已接收指令','Command accepted'],COMMAND_SUCCEEDED:['指令效果已应用','Command applied'],INSTRUCTION_RECORDED:['人工指令已记录','Instruction recorded'],JOB_ATTACHED:['连接已有作业','Attached existing job'],TASK_COMPLETED:['任务已完成','Task completed'],TASK_QUARANTINED:['任务自动恢复已隔离','Task recovery quarantined']};
const statusLabel=v=>statusNames[v] ? tr(...statusNames[v]):v;
const actionLabel=v=>actionNames[v] ? tr(...actionNames[v]):v;
const eventLabel=v=>eventNames[v] ? tr(...eventNames[v]):v;
const badge = (v, live=false) => {
  const color = ['RUNNING','HEALTHY','SUCCEEDED'].includes(v) ? 'green' : ['COMPLETED','APPLIED','ACTIVE'].includes(v) ? 'blue' : ['FAILED','LOST','FROZEN','QUARANTINED','REJECTED'].includes(v) ? 'red' : ['UNKNOWN','BLOCKED','WAITING','DELAYED','UNCONFIRMED','PENDING','ROTATING','STARTING','QUEUED'].includes(v) ? 'orange' : 'neutral';
  return `<span class='badge ${color}' data-live='${live}'>${live ? '<span class="dot"></span>' : ''}${esc(statusLabel(v))}</span>`;
};
const taskLink = (id, title) => `<a href='#/tasks/${encodeURIComponent(id)}'>${esc(title || short(id))}</a>`;
const proxyLink = (p) => `#/sessions/${encodeURIComponent(p.task_id)}/${encodeURIComponent(p.id)}`;
const incidentLink = (i) => `#/incidents/${encodeURIComponent(i.task_id)}/${i.sequence}`;
const empty = (title, message='') => `<div class='empty'>${icon('overview')}<p>${esc(title)}</p>${esc(message)}</div>`;
const details = (name, value, key=name) => `<details class='json-details' data-preserve='${esc(key)}'><summary>${esc(name)}</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;
const panel = (title, body, subtitle='', action='') => `<section class='panel'><div class='panel-head'><h2>${title}${subtitle ? `<span>${subtitle}</span>` : ''}</h2>${action}</div>${body}</section>`;
const state={snapshot:null,transport:null,readMono:0,offset:0,limit:20,search:'',filter:'',task:null,incident:null,events:[],jobId:null,jobData:null,receipts:[],workspaceId:null,paused:false,interval:5000,loading:false,pending:false,error:null,failures:0,modal:null,submitting:false};
Object.assign(state,{taskTab:'summary',historyPage:null,historyError:null,historyLoading:false,transcript:null,transcriptItems:[],transcriptAfter:0,transcriptError:null,transcriptDiscarded:0,transcriptFilter:'reader',transcriptSearch:'',readerOptions:false,followTranscript:true,proxyIncidents:[],incidentAfter:0,incidentHasMore:false,incidentError:null,incidentDiscarded:0});
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
  if(!data.response) throw new Error(tr("控制器不可达，尚无此查询的历史快照。","Controller unreachable; no snapshot exists for this query."));
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
        if(rows[0]?.sequence!==Number(r[2])) throw new Error(tr("此故障事件不存在。","Incident not found."));state.incident=rows[0];
      }
      const task=await rpc('get_task',{task_id:r[1]});if(key!==location.hash) return;state.task=task;
      if(r[0]==='sessions' && r[2]){
        if(!task.workers.some(p=>p.id===r[2])){
          const older=await rpc('get_task_records',{task_id:r[1],collection:'proxies',record_id:r[2]});
          if(key!==location.hash) return;task.workers.push(...older.items);
        }
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
        try {
          const rows=await rpc('get_incidents',{task_id:r[1],proxy_id:r[2],after:state.incidentAfter,limit:20});
          if(key!==location.hash) return;
          const known=new Map(state.proxyIncidents.map(i=>[i.id,i]));
          for(const row of rows) known.set(row.id,row);
          // get_task refreshes application outcomes for recently detected incidents.
          for(const row of task.incidents) if(known.has(row.id)) known.set(row.id,row);
          const all=[...known.values()].sort((a,b)=>a.sequence-b.sequence);
          state.incidentDiscarded+=Math.max(0,all.length-100);state.proxyIncidents=all.slice(-100);
          state.incidentHasMore=rows.length===20;
          if(rows.length) state.incidentAfter=rows.at(-1).sequence-(state.incidentHasMore ? 0:1);
          state.incidentError=null;
        }catch(e){if(key!==location.hash) return;state.incidentError=e.message;}
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
    if(key===location.hash){state.error=e.name==='AbortError' ? tr("查询超时，当前数据未重新核验。","Query timed out; current data is unverified."):e.message;if(!e.code || ['CONTROLLER_UNREACHABLE','DELIVERY_UNCONFIRMED'].includes(e.code)) state.transport={available:false,error:{message:state.error}};state.failures++;}
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
  const c=state.snapshot?.controller,healthy=live();document.body.classList.toggle('stale',!healthy);
  let label=tr('连接中','Connecting'),color='neutral',message='';
  if(!state.transport?.available && (state.snapshot || state.error)){label=tr('控制器离线','Controller offline');color='red';message=tr('控制器不可达。以下为最后成功读取的快照，运行状态未重新核验，人工操作已停用。','Controller unreachable. Showing the last successful snapshot; execution is unverified and controls are disabled.');}
  else if(c && c.state!=='ONLINE'){label=c.state==='STARTING' ? tr('控制器启动中','Controller starting'):tr('控制器巡检过期','Controller tick stale');color='orange';message=tr('Yan 接口可达，但控制器尚无新近完成的巡检。执行状态未确认，人工操作已停用。','Yan RPC is reachable but no recent Controller tick is confirmed. Controls are disabled.');}
  else if(state.paused || document.hidden){label=tr('刷新已暂停','Refresh paused');message=tr('以下为历史快照；恢复刷新或立即刷新以重新核验。','Historical snapshot. Resume or refresh to verify current state.');}
  else if(c && !healthFresh()){label=tr('状态待核验','State unverified');color='orange';message=tr('已超过刷新时限，当前运行状态未重新核验。','Refresh deadline exceeded. Current execution state is unverified.');}
  else if(healthy){label=tr('秉烛人在线','Candleholder online');color='green';}
  $('controller-badge').className='badge '+color;$('controller-badge').innerHTML=(healthy ? '<span class="dot green"></span>':'')+esc(label);
  $('side-state').textContent=healthy ? tr('秉烛人值守中','Candleholder on watch'):label;$('side-dot').className='dot '+(healthy ? 'green':color==='red' ? 'red':'');
  const banner=$('connection-banner');banner.hidden=!message;banner.textContent=message+(state.error ? ' '+state.error:'');
  $('updated-at').textContent=state.snapshot ? tr('最后读取 {time}','Last read {time}',{time:fmt(state.snapshot.queried_at)})+' · '+(healthy ? tr('自动同步','Auto refresh'):tr('快照','Snapshot')):tr('尚未读取账本','Ledger not read yet');
  const evidence=document.querySelector('[data-evidence-fresh]');if(evidence) evidence.textContent=healthy ? tr('查询时核验','Verified at query'):tr('历史快照 · 待重新核验','Snapshot · unverified');
  document.querySelectorAll('[data-requires-live]').forEach(el=>el.disabled=!canControl() || el.dataset.eligible==='false');
}
function pager(){const s=state.snapshot;return `<div class='pagination'><span>${s.total ? `${s.offset+1}–${Math.min(s.offset+s.limit,s.total)}`:'0'} / ${s.total} · ${tr('新记录优先','Newest first')}</span><div class='actions'><button class='button secondary' data-page='prev' ${s.offset===0 ? 'disabled':''}>${tr('上一页','Previous')}</button><button class='button secondary' data-page='next' ${s.offset+s.limit>=s.total ? 'disabled':''}>${tr('下一页','Next')}</button></div></div>`;}
function table(headers,rows){return `<div class='table-wrap'><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan='${headers.length}'>${empty(tr('当前没有记录','No records'))}</td></tr>`}</tbody></table></div>`;}
function taskRows(items){return items.map(t=>`<tr><td><a class='row-title' href='#/tasks/${encodeURIComponent(t.id)}'>${esc(t.title)}</a><span class='sub mono'>${esc(short(t.id))}</span></td><td>${badge(t.has_unknown_job ? 'UNKNOWN':t.status,t.status==='RUNNING' && !t.has_unknown_job)}${t.has_unknown_job ? `<span class='sub'>${tr('任务','Task')} ${esc(t.status)}</span>`:''}</td><td><span class='mono'>G${t.generation}</span><span class='sub'>${esc(t.adapter)}</span></td><td><span class='count-text'>${tr('{jobs} 作业 / {executions} 执行','{jobs} jobs / {executions} executions',{jobs:t.job_count,executions:t.execution_count})}</span></td><td>${badge(t.desired)}</td><td><a class='table-link' href='#/tasks/${encodeURIComponent(t.id)}'>${tr('查看','Open')} ${icon('arrow')}</a></td></tr>`).join('');}
function incidentPreview(i){const p=i.payload;return `<article class='incident-preview'><div class='alert-mark'>${icon('incidents')}</div><div class='incident-body'><div class='incident-title'><a href='${incidentLink(i)}'>${esc(i.rule_id)} <span class='muted'>/ v${p.rule_version}</span></a>${badge(i.status)}</div><p>${esc(p.symptom)}</p><div class='decision-line'><span>${tr('建议','Suggested')} ${esc(actionLabel(p.action))}</span><span>→</span><span>${tr('最终','Final')} ${esc(actionLabel(i.action))}</span><span>· G${i.generation}</span><span>${clock(i.created_at)}</span></div></div></article>`;}
function eventList(events,raw=false){return events.length ? `<div class='timeline'>${events.map(e=>`<article class='event'><span class='event-marker ${e.kind.includes('INCIDENT') || e.kind.includes('FAILED') ? 'red':''}'></span><div class='event-text'><div><span>${esc(eventLabel(e.kind))}</span><time class='event-time'>${clock(e.created_at)}</time></div><p>${esc(e.data?.rule_id || e.data?.reason || e.data?.generation && 'G'+e.data.generation || e.source)} · ${esc(short(e.task_id || e.actor))}</p>${raw ? details(tr('事件','Event')+' #'+e.id+' · '+e.kind,e.data,'event-'+e.id):''}</div></article>`).join('')}</div>`:empty(tr('暂无历史事件','No events yet'));}
function overview(){
  const s=state.snapshot,c=s.counts,active=pickCount(c.workers,['RUNNING','ROTATING','STARTING']);
  const stats=[[tr('活动代理','Active proxies'),active,tr('{n} 个历史会话','{n} historical sessions',{n:sum(c.workers)-active}),'sessions'],[tr('持久任务','Durable tasks'),sum(c.tasks),tr('{n} 个任务待推进','{n} tasks to advance',{n:pickCount(c.tasks,['RUNNING','READY'])}),'tasks'],[tr('运行中作业','Running jobs'),c.jobs.RUNNING || 0,tr('{n} 条独立执行记录','{n} independent executions',{n:c.executions}),'chart'],[tr('故障案录','Incidents'),sum(c.incidents),tr('{n} 次决策已执行','{n} decisions applied',{n:c.incidents.APPLIED || 0}),'incidents']];
  const statHtml=`<div class='stats'>${stats.map(([label,n,foot,ic])=>`<div class='stat'><div class='stat-top'>${label}<span class='stat-icon'>${icon(ic)}</span></div><div class='stat-number'>${n}</div><div class='stat-foot'>${foot}</div></div>`).join('')}</div>`;
  const controller=`<div class='controller-card'><div class='controller-title'><span class='controller-symbol'>${icon('shield')}</span><div><h3>${tr('秉烛人','Candleholder')}</h3><p>${tr('监控与恢复','Monitoring and recovery')}</p></div></div><dl class='kv compact'><dt>${tr('最近完成巡检','Last completed tick')}</dt><dd>${clock(s.controller.last_tick_at)}</dd><dt>${tr('运行时长','Uptime')}</dt><dd>${duration(s.queried_at-s.controller.started_at)}</dd><dt>${tr('进程','Process')}</dt><dd class='mono'>${esc(s.controller.pid)}</dd><dt>${tr('决策来源','Decision source')}</dt><dd>${tr('版本规则与持久账本','Versioned rules and ledger')}</dd></dl><div class='mini-divider'></div><p class='note'>${tr('代理会话更替时，任务与独立作业保留。结果未知的作业不会自动重新启动。','Tasks and independent jobs survive proxy replacement. Jobs with unknown outcomes are never restarted automatically.')}</p><div class='legend'><span>${tr('运行','Running')} ${c.jobs.RUNNING || 0}</span><span>${tr('未知','Unknown')} ${c.jobs.UNKNOWN || 0}</span><span>${tr('作业总数','Total jobs')} ${sum(c.jobs)}</span></div></div>`;
  const heading=`<header class='watch-heading'><div><h1>${tr('值守工作台','On watch')}</h1><p class='watch-motto'>${tr('诸相可散，案卷长存。','Forms may scatter. The dossier endures.')}</p><p class='watch-caption'>${tr('观代理之行，存任务之卷。记录会话、作业与秉烛人的恢复行动。','Follow Sui Proxies, inspect their work, and trace Candleholder recovery.')}</p><div class='watch-actions'><a class='button primary' href='#/tasks'>${tr('翻阅任务案卷','Browse tasks')} ${icon('arrow')}</a><a class='button secondary' href='#/sessions'>${icon('sessions')}${tr('查看代理会话','Read conversations')}</a></div></div></header>`;
  return `${heading}${statHtml}<div class='grid-main'>${panel(tr('近日故障案录','Recent incidents'),s.recent_incidents.length ? s.recent_incidents.map(incidentPreview).join(''):empty(tr('暂无故障记录','No incidents recorded')), '',`<a href='#/incidents'>${tr('全部故障','All incidents')} →</a>`)}${panel(tr('秉烛值守','Candleholder watch'),controller)}</div>${panel(tr('任务运行概览','Task overview'),table([tr('任务','Task'),tr('执行视图','Execution'),tr('代理代际','Generation'),tr('执行账本','Jobs'),tr('用户意图','Desired'),''],taskRows(s.items)),'',`<a href='#/tasks'>${tr('全部任务','All tasks')} →</a>`)}${panel(tr('值守纪事','Audit trail'),`<div class='overview-events' tabindex='0' aria-label='${tr('最近控制事件阅读区','Recent control events')}'>${eventList(s.recent_events)}</div>`)}`;
}
function listView(section){
  const s=state.snapshot,labels={sessions:[tr('代理会话','Sui Proxies'),tr('每位岁兽代理人都是可替换的会话；历史代际保留可追踪身份。','Each Sui Proxy is a replaceable session. Historical generations retain traceable identities.')],tasks:[tr('任务案卷','Tasks'),tr('用户意图、代理声明与运行观测分别记录。','Desired, declared and observed states are recorded separately.')],incidents:[tr('故障事件','Incidents'),tr('检测建议、秉烛人最终动作与应用结果分别记录。','Detection advice, Candleholder decisions and application outcomes are recorded separately.')]};
  const items=s.items.filter(i=>JSON.stringify([i.id,i.rule_id,i.task_title,i.title,i.status,i.generation]).toLowerCase().includes(state.search.toLowerCase()) && (!state.filter || (section==='sessions' ? i.health:i.status)===state.filter));
  let headers,rows;
  if(section==='sessions'){
    headers=[tr('代理会话','Sui Proxy'),tr('关联任务','Task'),tr('代际','Generation'),tr('运行状态','Status'),tr('最近心跳','Heartbeat'),tr('运行时长','Runtime'),''];
    rows=items.map(p=>`<tr><td><a class='row-title mono' href='${proxyLink(p)}'>${esc(short(p.id))}</a><span class='sub mono'>${esc(short(p.session_id))} · ${esc(p.adapter)}</span></td><td>${taskLink(p.task_id,p.task_title)}<span class='sub'>${tr('{n} 次故障','{n} incidents',{n:p.incident_count})}</span></td><td class='mono'>G${p.generation}${p.is_current ? `<span class='sub'>${tr('当前代','Current')}</span>`:''}</td><td>${badge(p.health==='HISTORICAL' ? p.status:p.health,p.health==='HEALTHY')}</td><td class='nowrap'>${clock(p.heartbeat_at)}<span class='sub'>${tr('{time}前','{time} ago',{time:duration(p.heartbeat_age_seconds)})}</span></td><td>${duration(p.runtime_seconds)}</td><td><a class='table-link' href='${proxyLink(p)}'>${tr('查看对话','Read conversation')} ${icon('arrow')}</a></td></tr>`).join('');
  }else if(section==='tasks'){headers=[tr('任务','Task'),tr('执行视图','Execution'),tr('代际','Generation'),tr('作业与执行','Jobs / executions'),tr('用户意图','Desired'),''];rows=taskRows(items);}
  else{headers=[tr('故障规则','Rule'),tr('关联任务','Task'),tr('代际','Generation'),tr('检测建议','Advice'),tr('最终动作','Final action'),tr(tr("应用结果","Application result"),'Outcome'),''];rows=items.map(i=>`<tr><td><a class='row-title mono' href='${incidentLink(i)}'>${esc(i.rule_id)} / v${i.payload.rule_version}</a><span class='sub'>${clock(i.created_at)} · ${esc(i.payload.category)}</span></td><td>${taskLink(i.task_id,i.task_title)}</td><td class='mono'>G${i.generation}</td><td>${esc(actionLabel(i.payload.action))}</td><td>${esc(actionLabel(i.action))}</td><td>${badge(i.status)}</td><td><a class='table-link' href='${incidentLink(i)}'>${tr('证据','Evidence')} ${icon('arrow')}</a></td></tr>`).join('');}
  const options=section==='sessions' ? ['HEALTHY','LOST','FROZEN','DELAYED','HISTORICAL']:section==='tasks' ? ['RUNNING','HOLD','WAITING','RETRY_BACKOFF','WAITING_RESOURCE','QUARANTINED','COMPLETED']:['PENDING','APPLIED','OBSERVED','STALE','IGNORED'];
  const controls=`<div class='table-tools'><input id='list-search' class='filter-input' aria-label='${tr('本页搜索','Search this page')}' placeholder='${tr('搜索本页名称或 ID…','Search name or ID on this page…')}' value='${esc(state.search)}'><select id='list-filter' class='filter-select' aria-label='${tr('本页状态筛选','Filter this page by status')}'><option value=''>${tr('本页全部状态','All states on this page')}</option>${options.map(v=>`<option value='${v}' ${state.filter===v ? 'selected':''}>${esc(statusLabel(v))}</option>`).join('')}</select></div>`;
  return `<div class='page-heading'><div><h1>${labels[section][0]}</h1><p>${labels[section][1]}</p></div><div class='actions'>${section==='tasks' ? `<button class='button primary' data-create-task data-requires-live data-eligible='true'>${tr('创建任务','Create task')}</button>`:''}<span class='heading-meta'>${tr('{n} 条账本记录','{n} ledger records',{n:s.total})}</span></div></div>${panel(labels[section][0],table(headers,rows)+pager(),'',controls)}`;
}
function generationList(task){return `<div class='generation-list'>${[...(task.proxies || task.workers)].reverse().map(p=>`<div class='generation ${task.current_proxy===p.id || task.current_worker===p.id ? 'current':''}'><span class='generation-num'>G${p.generation}</span><div><a href='${proxyLink(p)}'>${esc(short(p.id))}</a><p>${fmt(p.created_at)} · ${esc(p.error || tr('已领取任务','Task claimed'))}</p></div>${badge(p.status,p.status==='RUNNING')}</div>`).join('')}</div>`;}
function intervention(task){const eligible=!['COMPLETED','CANCELLED'].includes(task.desired);return `<div class='actions'><button class='button secondary' data-command='hold_task' data-requires-live data-eligible='${eligible && task.desired==='ACTIVE'}'>${icon('pause')}${tr('暂停','Hold')}</button><button class='button secondary' data-command='resume_task' data-requires-live data-eligible='${eligible && (task.desired==='HOLD' || ['WAITING','QUARANTINED','WAITING_RESOURCE'].includes(task.status))}'>${icon('play')}${tr('恢复','Resume')}</button><button class='button secondary' data-command='set_recovery_policy' data-requires-live data-eligible='${eligible}'>${tr('恢复策略','Recovery policy')}</button><button class='button primary' data-command='instruct_task' data-requires-live data-eligible='${eligible}'>${tr('追加指令','Add instructions')} ${icon('arrow')}</button></div>`;}
function metricChart(data){
  const points=data.logs.lines.map(line=>{try{return JSON.parse(line);}catch(_){return null;}}).filter(v=>v && Number.isFinite(v.step) && Number.isFinite(v.loss));
  if(points.length<2) return empty(tr('尚无足够指标点','Not enough metric samples'),tr('图表仅绘制实际运行记录。','Charts use actual Runner observations.'));
  const xs=points.map(v=>v.step),ys=points.map(v=>v.loss),lo=Math.min(...ys),hi=Math.max(...ys),xmin=Math.min(...xs),xmax=Math.max(...xs);
  const line=points.map(v=>`${10+(v.step-xmin)/Math.max(1,xmax-xmin)*780},${135-(v.loss-lo)/Math.max(1e-10,hi-lo)*120}`).join(' ');
  return `<div class='metrics-box'><div class='chart-labels'><span>loss ${hi.toPrecision(4)}</span><span>${tr('{n} 个实际采样点','{n} recorded samples',{n:points.length})}</span></div><svg class='metric-chart' viewBox='0 0 800 150' role='img' aria-label='${tr('损失指标曲线','Runner loss chart')}'><path d='M10 135h780' stroke='#d9d7cb'/><polygon class='chart-area' points='10,135 ${line} 790,135'/><polyline class='chart-line' points='${line}'/></svg><div class='chart-labels'><span>step ${xmin}</span><span>step ${xmax} · loss ${lo.toPrecision(4)}</span></div>${details(tr('当前指标原始数据','Raw metrics'),data.metric.metric,'metrics')}</div>`;
}
function jobsView(task){return task.jobs.length ? task.jobs.map(j=>{const m=j.metric || {},share=m.total_steps ? Math.min(100,m.step/m.total_steps*100):0;return `<article class='job-card'><div class='job-title'><div><h3 class='mono'>${esc(j.logical_key)}</h3><span class='sub mono break'>${esc(j.id)}</span></div>${badge(j.status,j.status==='RUNNING')}</div><div class='job-facts'><span>PID<b>${esc(j.pid)}</b></span><span>step<b>${esc(m.step || 0)} / ${esc(m.total_steps || '—')}</b></span><span>loss<b>${esc(m.loss)}</b></span><span>${tr('观测','Observed')}<b>${clock(j.observed_at)}</b></span></div><div class='progress'><svg viewBox='0 0 100 5' preserveAspectRatio='none'><rect width='${share}' height='5' fill='#486e59'/></svg></div><div class='job-foot'><span>${j.status==='UNKNOWN' ? tr('结果未确认 · 禁止自动重启','Outcome unknown · no automatic restart'):j.stale ? tr('观测已陈旧','Observation stale'):tr('来源：独立执行器','Source: independent Runner')} · exit ${esc(j.exit_code)}</span><button class='text-button' data-job='${esc(j.id)}'>${icon('chart')}${tr('查看指标','View metrics')}</button></div>${state.jobId===j.id ? (state.jobData ? metricChart(state.jobData):empty(tr('读取指标中…','Loading metrics…'))):''}</article>`;}).join(''):empty(tr('尚未登记作业','No registered jobs'));}
function continuityPanels(task){
  const rows=task.instruction_delivery || [],w=task.wait_state;
  const delivery=rows.length ? table([tr('命令编号','Command ID'),tr('入账','Recorded'),tr('投递准备','Delivery prepared'),tr('代理已读声明','Read acknowledgment')],rows.map(r=>`<tr><td class='mono break'>${esc(r.command_id)}</td><td>${badge(r.status)}<br>${fmt(r.created_at)}</td><td>${r.offered_at ? fmt(r.offered_at):tr('尚未准备','Not prepared')}<br>${esc(r.channel || '')}</td><td>${r.acknowledged_at ? tr('已确认读取','Acknowledged')+' · '+fmt(r.acknowledged_at):tr('未确认','Unconfirmed')}</td></tr>`).join('')):empty(tr('尚无追加指令','No additional instructions'),tr('通过上方“追加指令”补充任务要求。','Use Add instructions above to update the task.'));
  const wait=w ? `<dl class='kv'><dt>${tr('状态','State')}</dt><dd>${badge(w.state)} · ${badge(task.desired)}</dd><dt>${tr('等待原因','Reason')}</dt><dd>${esc(w.reason)}</dd><dt>${tr('等待作业','Waiting for job')}</dt><dd class='break'>${esc(w.job_id || tr('定时检查外部依赖','Scheduled external check'))}</dd><dt>${tr('责任方','Owner')}</dt><dd>${esc(w.responsible)}</dd><dt>${tr('下次检查','Next check')}</dt><dd>${fmt(w.next_check_at)}</dd><dt>${tr('唤醒条件','Wake conditions')}</dt><dd>${tr('登记作业终态、到期、新指令或恢复；暂停与取消优先。','Job terminal state, deadline, new instruction or Resume. Hold and Cancel take priority.')}</dd></dl>`:tr('尚未登记合作式等待。代理可以持续工作，也可以主动登记下次检查。','No cooperative wait registered. The proxy may continue working or schedule its next check.');
  const names={instructions:tr('追加要求','Instructions'),attempts:tr('操作回执','Operation receipts'),jobs:tr('作业','Jobs'),proxies:tr('代理历史','Proxies'),artifacts:tr('产物','Artifacts'),events:tr('事件','Events')},p=state.historyPage;
  const buttons=Object.entries(names).map(([key,name])=>`<button class='button secondary' data-history='${key}' data-requires-live data-eligible='${!state.historyLoading}'>${name} (${task.history_refs?.[key]?.total ?? '—'})</button>`).join('');
  const page=p ? `<p class='history-caption'>${names[p.collection]} · ${tr('当前 {n} 条','{n} records',{n:p.items.length})} · ${p.has_more ? tr('后面还有记录','More records available'):tr('已到本次查询末尾','End of this query')}</p>${p.items.map(item=>`<div class='handoff'>${p.collection==='proxies' ? `<a href='${proxyLink(item)}'>${tr('打开第 {n} 代对话','Open generation {n}',{n:item.generation})}</a>`:''}${details(item.id,item,'history-'+item.id)}</div>`).join('')}${p.has_more ? `<button class='button secondary' data-history='${p.collection}' data-after='${p.next_after}' data-requires-live data-eligible='${!state.historyLoading}'>${tr('下一页','Next')}</button>`:''}`:'';
  return {
    delivery:panel(tr('敕令交付记录','Instruction delivery'),`<div data-instruction-delivery>${delivery}</div><p class='source-note'>${tr('最近 20 条、当前代际。入账成功不代表已交付；投递准备未证实模型收到。已读确认是代理声明，不代表理解或执行。请核对操作回执与产物。','Latest 20 for the current generation. Recorded does not mean delivered; prepared does not prove receipt. A read acknowledgment is an agent claim, not proof of understanding or execution. Check receipts and artifacts.')}</p>`),
    waiting:panel(tr('等待与唤醒','Waiting and wake-up'),`<div class='panel-body' data-wait-state>${wait}</div>`),
    history:panel(tr('完整账本索引','Full ledger index'),`<div class='panel-body' data-history-panel><p>${tr('此处为有限预览。全账本未决操作 {ops}，未终态作业 {jobs}。','This is a bounded preview. Full-ledger pending operations: {ops}; nonterminal jobs: {jobs}.',{ops:esc(task.outstanding_operations?.native_or_command),jobs:esc(task.outstanding_operations?.jobs)})}</p><div class='history-buttons'>${buttons}</div>${state.historyError ? `<p role='alert'>${esc(state.historyError)}</p>`:''}${page}</div>`),
  };
}
async function loadHistory(collection,after=0){
  if(state.historyLoading || !canControl() || !state.task) return;
  const taskId=state.task.id,key=location.hash;state.historyLoading=true;state.historyError=null;render();
  try{const page=await rpc('get_task_records',{task_id:taskId,collection,after,limit:10});if(key===location.hash) state.historyPage=page;}
  catch(e){if(key===location.hash) state.historyError=e.message;}
  finally{state.historyLoading=false;if(key===location.hash) render();}
}
const taskTabs={summary:['任务总览','Summary'],execution:['执行核验','Execution'],commands:['敕令回执','Instructions'],archive:['历史案卷','History']};
function markdownBlock(text,key){return `<div class='markdown' data-markdown>${YanMarkdown.render(text)}</div><details class='raw-message' data-preserve='${esc(key)}'><summary>${tr('查看原文','View source')}</summary><div class='raw-content'><button class='text-button' data-copy-source>${tr('复制原文','Copy source')}</button><pre>${esc(text)}</pre></div></details>`;}
function taskDetail(task){
  const d=task.declared || {},proxies=task.proxies || task.workers,continuity=continuityPanels(task),current=proxies.find(p=>p.id===(task.current_proxy || task.current_worker));
  const observed=task.jobs.length ? task.jobs.map(j=>badge(j.status,j.status==='RUNNING')).join(' '):`<span class='muted'>${tr('尚无作业观测','No job observations')}</span>`;
  const brief=panel(tr('任务书与验收','Task brief and acceptance'),`<div class='panel-body brief-body'><div class='task-brief' data-task-brief>${markdownBlock(task.objective,'brief-source')}</div><h3>${tr('验收条件','Acceptance criteria')}</h3><div class='task-brief'>${markdownBlock(task.completion_criteria || tr('未单独定义；按任务书要求核验','Not separately defined; verify against the task brief.'),'criteria-source')}</div><dl class='kv'><dt>${tr('项目目录','Workspace')}</dt><dd class='break'>${esc(task.workspace)}</dd><dt>${tr('执行能力','Execution profile')}</dt><dd>${esc(task.execution_profile || 'managed')}</dd><dt>${tr('完成前核验','Completion verifier')}</dt><dd>${esc(task.verification_template || tr('未配置；语义完成为代理声明','Not configured; semantic completion is an agent claim'))}</dd></dl></div>`);
  const completion=panel(tr('完成标记','Completion manifest'),`<div class='panel-body'><p>${task.completion ? tr('完成标记已接受；任务语义完成由代理声明。','Completion manifest accepted. Semantic completion is declared by the proxy.'):tr('尚未接受完成标记；实验结果与会话健康分别处理。','No completion manifest accepted. Experiment outcomes and session health are handled separately.')}</p><code class='break'>${esc(task.completion_marker_path || tr('代理启动后生成专属路径','Assigned when a proxy starts'))}</code>${task.completion ? details(tr('完成证据与检查结果','Completion evidence and checks'),task.completion,'completion'):''}${(task.completion_receipts || []).map(r=>`<p>${badge(r.status)} · ${fmt(r.created_at)}<br>${esc(r.error || tr('结构、身份、要求版本及未决操作检查通过','Structure, identity, requirement revision and pending-operation checks passed'))}</p>`).join('')}</div>`);
  const recovery=panel(tr('持续恢复','Persistent recovery'),`<div class='panel-body'><dl class='kv'><dt>${tr('模式','Mode')}</dt><dd data-recovery-mode>${esc(task.recovery_mode)}</dd><dt>${tr('恢复阶段','Recovery phase')}</dt><dd>${esc(task.recovery_state?.phase || tr('尚未评估','Not evaluated'))}</dd><dt>${tr('最近运行证据','Last runtime evidence')}</dt><dd>${fmt(task.recovery_state?.last_runtime_evidence_at || task.recovery_state?.last_verified_progress_at)}</dd><dt>${tr('未观测新证据','No new evidence')}</dt><dd>${duration(task.recovery_state?.no_progress_seconds || 0)}</dd><dt>${tr('累计代理运行','Total proxy runtime')}</dt><dd>${duration(task.recovery_state?.proxy_seconds_used || 0)} / ${task.max_proxy_seconds ? duration(task.max_proxy_seconds):tr('不限制','Unlimited')}</dd><dt>${tr('下次允许启动','Next start allowed')}</dt><dd>${task.next_start_at ? fmt(task.next_start_at):tr('就绪','Ready')}</dd></dl><p class='task-brief'>${esc(task.recovery_state?.guidance || tr('这里只记录运行变化；工作内容与完成判断由代理负责。','Runtime observations only. The proxy owns the work and its completion judgment.'))}</p></div>`);
  const attempts=panel(tr('命令执行回执','Execution receipts'),(task.tool_attempts || []).length ? table([tr('工具','Tool'),tr('实际状态','Actual state'),tr('执行编号','Execution ID'),tr('时间','Time')],task.tool_attempts.map(a=>`<tr><td>${esc(a.tool)}</td><td>${badge(a.status)}${a.runtime ? details(tr('运行观察','Runtime observation'),a.runtime,'runtime-'+a.id):''}</td><td class='mono break'>${esc(a.id)}</td><td>${fmt(a.created_at)}</td></tr>`).join('')):empty(tr('尚无受管或原生命令回执','No managed or native command receipts')),tr('有限预览；结果未知的操作应先核验，再决定是否重试。','Bounded preview. Verify unknown outcomes before retrying.'));
  const incidents=panel(tr('最近故障','Recent incidents'),task.incidents.length ? task.incidents.slice(0,5).map(incidentPreview).join(''):empty(tr('暂无故障','No incidents')));
  const handoffs=panel(tr('交接记录','Handoffs'),proxies.filter(p=>p.handoff).map(p=>`<div class='handoff'><span class='mono'>G${p.generation} · ${esc(short(p.id))}</span>${handoffView(p.handoff,'handoff-'+p.id)}</div>`).join('') || empty(tr('尚无交接记录','No handoffs'),tr('异常退出时可从账本恢复。','Unexpected exits can recover from the ledger.')));
  const events=panel(tr('历史事件','Events'),`<div class='dossier-events' tabindex='0' aria-label='${tr('历史事件阅读区','Event reader')}'>${eventList([...state.events].reverse(),true)}</div><div class='source-note'>${tr('已加载 {n} 条，最多保留 500 条。','{n} loaded; up to 500 retained.',{n:state.events.length})}<button class='text-button' data-action='events'>${tr('刷新 / 加载后续','Refresh / load more')}</button></div>`);
  const panes={summary:`<div class='detail-columns'><div>${brief}${completion}</div><div>${continuity.waiting}${recovery}</div></div>`,execution:panel(tr('作业与指标','Jobs and metrics'),jobsView(task))+attempts+incidents,commands:`<p class='section-intro'>${tr('核对追加要求的入账、投递与已读声明。本浏览器提交的操作回执可从右上角打开。','Inspect recorded instructions, delivery and read acknowledgments. Open this browser’s command receipts at the top right.')}</p>${continuity.delivery}`,archive:continuity.history+`<div class='detail-columns'><div>${events}</div><div>${panel(tr('代理换代历史','Generations'),generationList(task))}${handoffs}</div></div>`};
  const tabs=`<div class='task-tabs' role='tablist' aria-label='${tr('任务案卷分区','Task sections')}'>${Object.entries(taskTabs).map(([key,name])=>`<button type='button' id='tab-${key}' role='tab' data-task-tab='${key}' aria-controls='pane-${key}' aria-selected='${state.taskTab===key}' tabindex='${state.taskTab===key ? 0:-1}'>${tr(...name)}</button>`).join('')}</div>`;
  return `<a class='back-link' href='#/tasks'>${icon('back')}${tr('返回任务案卷','Back to tasks')}</a><div class='page-heading detail-heading'><div><h1>${esc(task.title)}</h1><span class='sub mono'>${esc(task.id)} · ${esc(task.adapter)} · G${task.generation}</span>${current ? `<a class='proxy-entry' href='${proxyLink(current)}'>${icon('sessions')}${tr('查看当前代理的会话实录','Read the current proxy’s conversation')} ${icon('arrow')}</a>`:''}</div>${intervention(task)}</div><div class='state-grid'><div class='state-card desired' data-state-view='desired'><h3>${tr('用户意图','Desired state')}</h3><div class='large-value'>${badge(task.desired)}</div><p>${tr('来源：用户命令','Source: Yan Command')}<br>${tr('执行视图','Execution view')} ${esc(task.status)}</p></div><div class='state-card declared' data-state-view='declared'><h3>${tr('代理声明','Declared state')} <span class='badge neutral'>${tr('未核验','Unverified')}</span></h3><div class='large-value'>${esc(d.phase || tr('尚无声明','No declaration'))}</div><p class='summary'>${esc(d.summary || tr('代理尚未提交进度报告。','No progress report submitted.'))}</p><p>${fmt(d.updated_at)}</p></div><div class='state-card observed' data-state-view='observed'><h3>${tr('运行观测','Observed state')}</h3><div>${observed}</div><p>${tr('{jobs} 个作业 · {executions} 条执行','{jobs} jobs · {executions} executions',{jobs:task.history_refs?.jobs?.total ?? task.jobs.length,executions:task.execution_count})}<br>${tr('代理更替不等于重新执行作业。','Proxy replacement does not restart jobs.')}</p></div></div>${tabs}${Object.entries(panes).map(([key,body])=>`<div id='pane-${key}' role='tabpanel' aria-labelledby='tab-${key}' tabindex='0' ${state.taskTab===key ? '':'hidden'}>${body}</div>`).join('')}`;
}
function handoffView(value,key){
  const text=typeof value==='string' ? value:typeof value.summary==='string' ? value.summary:typeof value.notes==='string' ? value.notes:'';
  return (text ? `<div class='markdown'>${YanMarkdown.render(text)}</div>`:'')+details(tr('查看结构化交接','Structured handoff'),value,key);
}
function selectTaskTab(name){
  if(!Object.hasOwn(taskTabs,name)) return;state.taskTab=name;
  for(const key of Object.keys(taskTabs)){const selected=key===name,tab=$('tab-'+key),pane=$('pane-'+key);if(tab){tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected ? 0:-1;}if(pane) pane.hidden=!selected;}
}
function proxyDetail(task,id){
  const p=(task.proxies || task.workers).find(p=>p.id===id);if(!p) return empty(tr('此代理不存在','Proxy not found'));
  const entries=[[tr('代理编号','Proxy ID'),p.id],[tr('会话标识','Session UUID'),p.session_id],[tr('代际','Generation'),'G'+p.generation],[tr('运行时','Adapter'),p.adapter],['PID',p.pid],[tr('心跳','Heartbeat'),fmt(p.heartbeat_at)],[tr('最后活动','Last activity'),fmt(p.activity_at)],[tr('运行时长','Runtime'),duration(p.runtime_seconds)],[tr('创建','Created'),fmt(p.created_at)],[tr('退出','Ended'),fmt(p.ended_at)],[tr('输出流','Stream'),p.stream_observation],[tr('关联任务','Task'),task.title]];
  const incidents=state.proxyIncidents;
  return `<a class='back-link' href='#/sessions'>${icon('back')}${tr('返回代理会话','Back to proxies')}</a><div class='page-heading detail-heading'><div><h1>${tr('第 {n} 代代理','Generation {n}',{n:p.generation})} ${badge(p.health==='HISTORICAL' ? p.status:p.health,p.health==='HEALTHY')}</h1><span class='sub mono'>${esc(p.id)}</span></div><a class='button secondary' href='#/tasks/${encodeURIComponent(task.id)}'>${tr('管理关联任务','Manage task')} ${icon('arrow')}</a></div><nav class='section-links' aria-label='${tr('代理详情分区','Proxy sections')}'><button type='button' data-jump='proxy-transcript'>${tr('会话实录','Conversation')}</button><button type='button' data-jump='proxy-evidence'>${tr('运行核验','Execution evidence')}</button><button type='button' data-jump='proxy-identity'>${tr('身份档案','Identity')}</button></nav>${transcriptView(p)}${executionEvidence(task,p)}<div class='detail-columns' id='proxy-identity' tabindex='-1'><div>${panel(tr('代理身份与活动','Identity and activity'),`<div class='panel-body'><dl class='kv'>${entries.map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl><div class='mini-divider'></div><p class='note'>${tr('最后活动由包装进程与协议采集，不能单独证明科研进展。离线时仅显示最后快照。','Activity is captured by the wrapper and protocol; it does not prove research progress. Offline views are snapshots.')}</p>${details(tr('登记的进程身份','Registered process identities'),{proxy:p.identity,runtime:p.child_identity},'identity')}</div>`)}${panel(tr('本代故障与处理','Incidents in this generation'),incidents.length ? incidents.map(incidentPreview).join(''):empty(tr('此代际暂无已加载故障','No loaded incidents for this generation')))}${p.handoff ? panel(tr('交接记录','Handoff'),`<div class='panel-body'>${handoffView(p.handoff,'proxy-handoff')}</div>`):''}</div><div>${panel(tr('同一任务的代际历史','Task generations'),generationList(task))}${panel(tr('关联作业','Related jobs'),jobsView(task))}</div></div>`;
}
function executionEvidence(task,p){
  const verified=live(),processLabel=(exists,osState)=>!verified ? tr('快照 · 待重新核验','Snapshot · unverified'):['T','t'].includes(osState) ? tr('进程冻结','Process frozen'):exists===true ? tr('进程身份匹配','Process identity matched'):exists===false ? tr('进程已退出','Process exited'):tr('没有进程观测','No process observation');
  const job=task.jobs.find(j=>j.status==='RUNNING') || task.jobs[0],runtime=p.child_identity;
  return `<section class='panel execution-evidence' id='proxy-evidence' tabindex='-1' data-execution-evidence><div class='panel-head'><h2>${tr('实际运行证据','Execution evidence')}</h2><span><b data-evidence-fresh>${verified ? tr('查询时核验','Verified at query'):tr('历史快照','Snapshot')}</b> · ${fmt(task.queried_at)}</span></div><div class='evidence-grid'>
    <article><h3>${tr('代理包装进程','Proxy wrapper')}</h3><strong>${processLabel(p.process_alive,p.process_state)}</strong><p>PID ${esc(p.pid)} · OS ${esc(p.process_state)}</p><span>${tr('来源：系统启动标识、进程编号与启动时刻','Source: boot ID, PID and start ticks')}</span></article>
    <article><h3>${tr('模型运行时','Model runtime')}</h3><strong>${p.adapter==='mock' ? tr('合成演示 · 未调用真实模型','Mock · no real model'):processLabel(p.runtime_process_alive,p.runtime_process_state)}</strong><p>${p.adapter==='mock' ? tr('此会话只执行合成演示','This session runs a synthetic demo'):'CLI PID '+esc(runtime?.pid)+' · OS '+esc(p.runtime_process_state)}</p><span>${tr('输出流','Stream')}: ${esc(p.stream_observation)}</span></article>
    <article><h3>${tr('独立作业','Independent job')}</h3><strong>${job ? esc(statusLabel(job.status)):tr('尚未登记作业','No registered job')}</strong><p>${job ? 'PID '+esc(job.pid)+' · '+processLabel(job.process_alive):tr('请核对工具的实际返回','Inspect actual tool results')}</p><span>${tr('共 {n} 条执行','{n} executions',{n:task.execution_count})}</span></article>
    <article><h3>${tr('最近运行观测','Latest Runner observation')}</h3><strong>${job?.metric?.step!=null ? 'step '+esc(job.metric.step):tr('尚无指标','No metrics')}</strong><p>${job ? 'loss '+esc(job.metric?.loss)+' · '+fmt(job.observed_at):tr('请核对实际运行结果','Inspect execution outcomes')}</p><span>${tr('来源：独立执行器；代理自述单独记录','Source: independent Runner; agent claims recorded separately')}</span></article>
    </div><p class='source-note'>${tr('进程存在、收到文本、工具返回与作业完成是不同事实。助手声称“完成”不会改写运行观测。暂停或离线时，证据仅为快照。','Process existence, text, tool results and job completion are different facts. Assistant claims never overwrite execution observations. Paused or offline evidence is a snapshot.')}</p></section>`;
}
function transcriptGroups(){
  const groups=[],calls=new Map();
  for(const item of state.transcriptItems){
    if(item.kind==='tool_result' && item.call_id && calls.has(item.call_id)){
      const call=calls.get(item.call_id);call.results.push(item);call.endTime=item.time;continue;
    }
    const previous=groups.at(-1);
    if(item.kind==='text' && item.message_id && previous?.kind==='text' && previous.message_id===item.message_id && previous.role===item.role){previous.text+=item.text;previous.endTime=item.time;previous.truncated ||= item.truncated;}
    else{const entry={...item,results:[]};groups.push(entry);if(item.kind==='tool_call' && item.call_id) calls.set(item.call_id,entry);}
  }
  for(const incident of state.proxyIncidents){groups.push({id:'incident-'+incident.sequence,kind:'incident',time:incident.created_at,incident});}
  return groups.sort((a,b)=>a.time-b.time);
}
function rawRecord(text){return `<div class='raw-content'><button type='button' class='text-button' data-copy-source>${tr('复制原文','Copy source')}</button><pre>${esc(text)}</pre></div>`;}
function recordMeta(item){return `<p class='record-meta'>${esc(item.source)}${item.call_id ? ' · '+esc(item.call_id):''}${item.model ? ' · '+esc(item.model):''}</p>`;}
function shortToolInput(text){
  try{const value=JSON.parse(text);if(value && typeof value==='object'){const hint=value.description || value.command || value.file_path || value.path || value.pattern || value.query || value.logical_key;return typeof hint==='string' ? hint.replace(/\s+/g,' ').slice(0,100):'';}}catch(_){}return '';
}
function transcriptMessage(item,proxy){
  if(item.kind==='incident'){
    const i=item.incident,p=i.payload;
    return `<article class='session-message incident-marker' data-incident-marker='${i.sequence}' tabindex='-1'><header><strong>${icon('incidents')} ${tr('故障检出','Incident detected')} · ${esc(i.rule_id)}</strong><time>${fmt(i.created_at)}</time></header><p>${esc(p.symptom)}</p><div class='decision-line'><span>${tr('建议','Advice')}: ${esc(actionLabel(p.action))}</span><span>→</span><span>${tr('最终','Final')}: ${esc(actionLabel(i.action))}</span>${badge(i.status)}</div><p class='note'>${tr('检测器标记的起始时间','Detector-marked onset')}: ${fmt(p.onset_at)} · ${tr('检测时间','Detected')}: ${fmt(p.detected_at)}</p><a href='${incidentLink(i)}'>${tr('查看证据与恢复结果','Inspect evidence and recovery')} ${icon('arrow')}</a></article>`;
  }
  const expanded=state.transcriptFilter==='all' ? 'open':'';
  if(['tool_call','tool_result'].includes(item.kind)){
    const results=item.kind==='tool_result' ? [item]:item.results,name=item.tool_name || tr('工具','Tool');
    const status=results.length ? results.some(r=>r.is_error) ? tr('工具返回错误','Tool returned an error'):tr('已返回','Returned'):tr('尚无返回记录','No return recorded');
    const blocks=(item.kind==='tool_call' ? `<h4>${tr('调用参数','Arguments')}</h4>${rawRecord(item.text)}`:'')+results.map(result=>`<h4>${tr('工具返回','Result')} · ${clock(result.time)}</h4>${rawRecord(result.text)}${result.truncated ? `<p class='message-limit'>${tr('返回内容已截断','Result truncated')}</p>`:''}`).join('');
    return `<article class='session-message role-tool compact-activity' data-message-role='tool' data-tool-call='${esc(item.call_id || '')}'><details class='message-tool' data-preserve='message-${item.id}' ${expanded}><summary><strong>↳ ${esc(name)}</strong><span class='tool-hint'>${esc(shortToolInput(item.kind==='tool_call' ? item.text:''))}</span><span class='tool-status'>${status}</span><time>${clock(item.time)}</time></summary>${blocks}${results.some(r=>r.is_error) ? `<p class='note'>${tr('工具错误不代表会话故障；由代理决定如何处理任务。','A tool error is not session failure. The proxy decides how to handle it.')}</p>`:''}${recordMeta(item)}</details></article>`;
  }
  if(item.role==='system' || item.kind!=='text'){
    const label=item.message_id==='supervisor-protocol' ? tr('运行协议','Runtime protocol'):item.message_id==='injected-context' ? tr('注入上下文','Injected context'):item.kind==='exit' ? tr('运行时结束记录','Runtime end record'):item.kind==='recording_limit' ? tr('达到采集上限','Recording limit reached'):tr('运行时记录','Runtime record');
    return `<article class='session-message role-system compact-activity' data-message-role='system'><details data-preserve='message-${item.id}' ${expanded}><summary><strong>· ${label}</strong><time>${clock(item.time)}</time></summary>${rawRecord(item.text)}${item.truncated ? `<p class='message-limit'>${item.message_id==='injected-context' ? tr('原始上下文已截断；用户目标与验收条件另行完整记录。','Raw context truncated; operator goals and acceptance criteria are recorded separately in full.'):tr('本条内容已截断。','This record was truncated.')}</p>`:''}${recordMeta(item)}</details></article>`;
  }
  let text=item.text,context='';
  if(item.role==='user' && item.message_id==='initial-input'){
    try{const injected=JSON.parse(text),request=injected.operator_request;
      if(request && typeof request.objective==='string'){
        text=request.objective;
        if(request.completion_criteria) text+='\n\n### '+tr('验收条件','Acceptance criteria')+'\n\n'+request.completion_criteria;
        context=`<details class='raw-message' data-preserve='injected-${item.id}'><summary>${tr('注入上下文','Injected context')}</summary>${rawRecord(item.text)}</details>`;
      }
    }catch(_){}
  }
  const overlaps=state.proxyIncidents.filter(i=>i.rule_id==='GEN-001' && Number.isFinite(i.payload.onset_at) && item.time<=i.payload.detected_at && (item.endTime || item.time)>=i.payload.onset_at);
  const role=item.role==='user' ? 'user':'assistant',label=role==='user' ? item.input_field==='completion_criteria' ? tr('用户 · 验收条件','You · acceptance criteria'):tr('用户','You'):tr('岁兽代理人','Sui Proxy');
  return `<article class='session-message role-${role} ${overlaps.length ? 'in-detection-window':''}' data-message-role='${role}'><header><strong><span class='role-symbol' aria-hidden='true'>${role==='user' ? '●':'◆'}</span>${label}</strong><time>${fmt(item.time)}</time>${overlaps.length ? `<a class='window-label' href='${incidentLink(overlaps[0])}'>${tr('退化检测窗口','Degeneration detection window')} · GEN-001</a>`:''}</header><div class='message-text markdown'>${YanMarkdown.render(text)}</div><details class='raw-message' data-preserve='message-${item.id}' ${expanded}><summary>${tr('原文与来源','Source and metadata')}</summary>${rawRecord(text)}${recordMeta(item)}</details>${context}${item.truncated ? `<p class='message-limit'>${tr('本条内容已截断。','This record was truncated.')}</p>`:''}</article>`;
}
function transcriptView(proxy){
  const page=state.transcript,groups=transcriptGroups(),query=state.transcriptSearch.toLocaleLowerCase();
  const filtered=groups.filter(item=>(item.kind==='incident' || state.transcriptFilter==='reader' || state.transcriptFilter==='all' || (state.transcriptFilter==='dialogue' ? item.kind==='text' && ['user','assistant'].includes(item.role):['tool_call','tool_result'].includes(item.kind))) && (!query || item.kind==='incident' || JSON.stringify([item.text,item.tool_name,item.results]).toLocaleLowerCase().includes(query)));
  let content=filtered.map(item=>transcriptMessage(item,proxy)).join('');
  if(!content){const missing=page?.availability==='not_recorded';content=empty(missing ? tr('此会话没有留存对话','No conversation was recorded'):page?.availability==='unreadable' ? tr('会话记录无法读取','Transcript unreadable'):query || state.transcriptFilter!=='reader' ? tr('当前筛选下没有记录','No matching records'):tr('等待会话输出','Waiting for session output'),missing ? tr('只采集升级后启动的新代理。不会根据事件编造历史对话。','Capture applies to proxies started after upgrading. Events never become invented conversation history.'):tr('仅显示实际采集输出；空记录不代表代理未启动。','Only captured output is shown. An empty transcript does not mean the proxy failed to start.'));}
  const latest=state.transcriptItems.at(-1),options=[['reader',tr('阅读模式','Reader')],['dialogue',tr('只看对话','Dialogue only')],['tools',tr('工具活动','Tools')],['all',tr('展开原始记录','Expanded records')]];
  return `<section class='panel session-transcript' id='proxy-transcript' tabindex='-1' data-session-transcript><div class='panel-head'><h2>${tr('会话实录','Conversation')}</h2><span>${proxy.adapter==='mock' ? tr('合成演示 · 非真实模型','Synthetic demo · no real model'):tr('已采集的会话输出','Captured session output')}</span></div><div class='transcript-toolbar'><label>${tr('查看','View')} <select id='transcript-filter'>${options.map(([v,label])=>`<option value='${v}' ${state.transcriptFilter===v ? 'selected':''}>${label}</option>`).join('')}</select></label><label><input id='transcript-follow' type='checkbox' ${state.followTranscript ? 'checked':''}> ${tr('跟随新消息','Follow new messages')}</label><button type='button' id='reader-options-toggle' class='text-button reader-options-toggle' aria-controls='reader-tools' aria-expanded='${state.readerOptions}' data-reader-options>${tr('阅读选项','Reading options')}</button><div id='reader-tools' class='reader-tools ${state.readerOptions ? 'expanded':''}'><input id='transcript-search' type='search' placeholder='${tr('搜索已加载的记录…','Search loaded records…')}' aria-label='${tr('搜索已加载的记录','Search loaded records')}' value='${esc(state.transcriptSearch)}'><button class='text-button' data-reader-jump='first'>${tr('开头','Top')}</button><button class='text-button' data-reader-jump='last'>${tr('最新','Latest')}</button>${state.proxyIncidents.length ? `<button class='button secondary small' data-next-incident>${tr('下一处故障','Next incident')} (${state.proxyIncidents.length})</button>`:''}<button class='button secondary small' data-action='transcript'>${page?.has_more || state.incidentHasMore ? tr('继续加载记录','Load more'):tr('刷新记录','Refresh')}</button></div></div>
    ${state.transcriptError ? `<p class='notice warning'>${tr('会话记录未刷新','Transcript not refreshed')}: ${esc(state.transcriptError)}</p>`:''}${state.incidentError ? `<p class='notice warning'>${tr('故障标记未刷新','Incident markers not refreshed')}: ${esc(state.incidentError)}</p>`:''}${page?.limited ? `<p class='notice warning'>${tr('本代际记录已达 4 MiB 上限，后续内容未保存。','The 4 MiB generation limit was reached. Later output was not saved.')}</p>`:''}
    <div class='reader-legend'><span class='user-key'>● ${tr('用户','You')}</span><span class='assistant-key'>◆ ${tr('代理回复','Sui Proxy')}</span><span class='tool-key'>↳ ${tr('工具活动','Tools')}</span><span>${tr('技术细节默认收起 · 点击展开','Technical details collapsed · click to inspect')}</span></div><div class='transcript-list' data-transcript-list tabindex='0' aria-label='${tr('会话记录阅读区','Conversation reader')}'>${content}</div><div class='source-note'>${tr('最近采集','Last captured')} ${latest ? fmt(latest.time):'—'} · ${tr('已加载 {n} 条','{n} records loaded',{n:state.transcriptItems.length})}${state.transcriptDiscarded ? ' · '+tr('更早 {n} 条已从浏览器释放，重新进入可从头读取','{n} earlier records released from the browser; reopen to read from the start',{n:state.transcriptDiscarded}):''}${page?.has_more ? ' · '+tr('后面还有记录','More records available'):''}${state.incidentHasMore ? ' · '+tr('故障标记尚未加载完整','More incident markers available'):''}${state.incidentDiscarded ? ' · '+tr('仅保留最近加载的 100 个故障标记','Only the latest 100 loaded incident markers are retained'):''}${tr('。','. ')}${tr('故障标记来自检测账本；工具错误与实验结果不自动视为会话故障。','Incident markers come from the ledger; tool errors and experiment outcomes are not automatically session failures.')}</div></section>`;
}
function incidentDetail(i){const p=i.payload;return `<a class='back-link' href='#/incidents'>${icon('back')}${tr('返回故障事件','Back to incidents')}</a><div class='page-heading detail-heading'><div><h1>${esc(i.rule_id)} <span class='muted'>/ v${p.rule_version}</span></h1><span class='sub mono'>${esc(i.id)} · ${fmt(i.created_at)}</span></div>${badge(i.status)}</div>${panel(tr('从检出到处理','Detection to recovery'),`<div class='incident-flow'><div class='flow-step'><small>01 · ${tr('检测建议','Detection advice')}</small><h3>${esc(actionLabel(p.action))}</h3><p>${tr('规则建议，尚不代表已执行','A rule suggestion, not proof of execution')}</p></div><div class='flow-step'><small>02 · ${tr('秉烛人最终动作','Candleholder decision')}</small><h3>${esc(actionLabel(i.action))}</h3><p>${i.applied_at ? fmt(i.applied_at):tr('等待处理','Pending')}</p></div><div class='flow-step'><small>03 · ${tr(tr("应用结果","Application result"),'Application result')}</small><h3>${esc(statusLabel(i.status))}</h3><p>${i.status==='APPLIED' ? tr('控制状态已落盘','Control state committed'):tr('以持久处理回执为准','Consult the persisted receipt')}</p></div></div>`)}<div class='detail-columns'><div>${panel(tr('检测证据','Detection evidence'),`<div class='panel-body'><p class='summary'>${esc(p.symptom)}</p><div class='mini-divider'></div><pre class='evidence'>${esc(JSON.stringify(p.evidence,null,2))}</pre><p class='note'>${tr('证据保存计数与摘要。置信分数是启发式结果，并非校准后的概率。','Evidence contains counts and excerpts. Confidence is a heuristic score, not a calibrated probability.')}</p></div>`)}${panel(tr('应用回执','Application receipt'),`<div class='panel-body'><pre class='evidence'>${esc(JSON.stringify(i.outcome,null,2))}</pre>${details(tr('完整检测建议','Full detection advice'),p,'incident-payload')}</div>`)}</div><div>${panel(tr('关联与保护','Context and safeguards'),`<div class='panel-body'><dl class='kv compact'><dt>${tr('任务','Task')}</dt><dd>${taskLink(i.task_id,state.task?.title)}</dd><dt>${tr('代理会话','Sui Proxy')}</dt><dd><a class='mono' href='#/sessions/${encodeURIComponent(i.task_id)}/${encodeURIComponent(i.proxy_id || i.worker_id)}'>${esc(short(i.proxy_id || i.worker_id))}</a></dd><dt>${tr('代际','Generation')}</dt><dd>G${i.generation}</dd><dt>${tr('类别 / 严重度','Category / severity')}</dt><dd>${esc(p.category)} / ${esc(p.severity)}</dd><dt>${tr('推测来源','Suspected origin')}</dt><dd>${esc(p.suspected_origin)}</dd><dt>${tr('根因已确认','Cause confirmed')}</dt><dd>${p.cause_confirmed ? tr('是','Yes'):tr('否','No')}</dd><dt>${tr('保留任务与作业','Preserve task / jobs')}</dt><dd>${p.preserve_task && p.preserve_running_jobs ? tr('是','Yes'):tr('见规则','See rule')}</dd><dt>${tr('规则恢复预算','Rule recovery budget')}</dt><dd>${tr('{n} 次 / 规则窗口','{n} attempts / rule window',{n:p.max_attempts})}</dd></dl></div>`)}<div class='notice info'>${tr('检测建议与最终动作可能不同，请以处理回执为准。','Detection advice and final actions may differ. Check the application receipt.')}</div></div></div>`;}
function render(){
  const r=route(),titles={overview:tr('值守总览','Overview'),sessions:tr('代理名册','Sui Proxies'),tasks:tr('任务案卷','Tasks'),incidents:tr('故障案录','Incidents')};
  $('breadcrumb').textContent=titles[r[0]];document.title=titles[r[0]]+' · '+tr('司岁台','Yan Console');
  document.querySelectorAll('[data-nav]').forEach(el=>{el.classList.toggle('active',el.dataset.nav===r[0]);if(el.dataset.nav===r[0]) el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  if(state.snapshot){const c=state.snapshot.counts;$('nav-sessions').textContent=sum(c.workers);$('nav-tasks').textContent=sum(c.tasks);$('nav-incidents').textContent=sum(c.incidents);}
  const focused=document.activeElement,focusId=focused?.id,selection=focused?.selectionStart;
  const focusedDetails=focused?.tagName==='SUMMARY' ? focused.parentElement.dataset.preserve:null;
  const disclosures=new Map([...$('main').querySelectorAll('details[data-preserve]')].map(el=>[el.dataset.preserve,el.open]));
  const transcriptScroll=document.querySelector('[data-transcript-list]')?.scrollTop || 0;
  const eventScroll=document.querySelector('.dossier-events, .overview-events')?.scrollTop || 0;
  let html;
  if(!state.snapshot) html=empty(state.error ? tr("无法读取账本","Cannot read the ledger"):tr("等待控制器连接","Waiting for Controller"),state.error || tr("启动同一状态目录的控制器后，点击立即刷新。","Start the Controller for the same home directory, then refresh."));
  else if(r.length>1) html=state.task ? (r[0]==='tasks' ? taskDetail(state.task):r[0]==='sessions' ? proxyDetail(state.task,r[2]):state.incident ? incidentDetail(state.incident):empty(tr("正在读取故障详情…","Loading incident…"))):empty(tr("正在读取详情…","Loading details…"),state.error || '');
  else html=r[0]==='overview' ? overview():listView(r[0]);
  $('main').innerHTML=(state.error && state.snapshot ? `<div class='notice warning'>${esc(state.error)}</div>`:'')+html;
  if(!state.resetDisclosure) $('main').querySelectorAll('[data-preserve]').forEach(el=>{if(disclosures.has(el.dataset.preserve)) el.open=disclosures.get(el.dataset.preserve);});
  state.resetDisclosure=false;
  const reader=document.querySelector('[data-transcript-list]');if(reader) reader.scrollTop=state.followTranscript ? reader.scrollHeight:transcriptScroll;
  const eventReader=document.querySelector('.dossier-events, .overview-events');if(eventReader) eventReader.scrollTop=eventScroll;
  if(focusedDetails) [...document.querySelectorAll('details[data-preserve]')].find(el=>el.dataset.preserve===focusedDetails)?.querySelector('summary')?.focus({preventScroll:true});
  if(focusId && $(focusId) && (['list-search','list-filter','transcript-filter','transcript-follow','transcript-search','reader-options-toggle'].includes(focusId) || focused?.hasAttribute('data-task-tab'))){ $(focusId).focus({preventScroll:true});if(selection!=null && $(focusId).setSelectionRange) $(focusId).setSelectionRange(selection,selection);}
  updateHealth();renderReceipts();
}
function renderReceipts(){
  const open=[...$('receipt-list').querySelectorAll('details[open][data-preserve]')].map(el=>el.dataset.preserve),scroll=$('receipts').scrollTop;
  $('receipt-count').textContent=state.receipts.length;
  $('receipt-list').innerHTML=state.receipts.length ? state.receipts.map(i=>`<article class='receipt-item ${['REJECTED','UNCONFIRMED'].includes(i.status) ? 'error':''}'><div><h3>${esc({set_recovery_policy:tr('恢复策略','Recovery policy'),submit_task:tr('创建任务','Create task'),hold_task:tr('暂停','Hold'),resume_task:tr('恢复','Resume'),instruct_task:tr('追加指令','Add instructions')}[i.kind])}</h3>${badge(i.status)}</div><p>${esc(i.title || short(i.task_id))}</p><code>${esc(i.id || i.key)}</code><span class='sub'>${fmt(i.updated_at || i.created_at)}</span>${i.error ? `<p>${esc(i.error)}</p>`:''}${i.result ? details(tr("应用结果","Application result"),i.result,'receipt-'+i.key):''}${['UNCONFIRMED','NOT_SENT'].includes(i.status) ? `<button class='text-button' data-retry='${esc(i.key)}'>${tr('使用原幂等键核验 / 重试','Check / retry with original key')}</button>`:''}</article>`).join(''):empty(tr("尚无本浏览器提交的指令","No commands submitted in this browser"),tr("历史操作仍保留在任务审计事件中。","Historical operations remain in the task audit trail."));
  $('receipt-list').querySelectorAll('[data-preserve]').forEach(el=>{if(open.includes(el.dataset.preserve)) el.open=true;});$('receipts').scrollTop=scroll;
}
function openCommand(kind,old=null){
  if(kind==='submit_task'){openTask(old);return;}
  if(!old && !state.task) return;
  state.modal=old || {key:crypto.randomUUID(),kind,task_id:state.task.id,title:state.task.title,created_at:Date.now()/1000};
  updateCommandCopy();
  $('policy-fields').hidden=kind!=='set_recovery_policy';$('policy-mode').value=old?.payload?.recovery_mode || state.task?.recovery_mode || 'persistent';$('policy-budget').value=old?.payload?.max_proxy_seconds ?? state.task?.max_proxy_seconds ?? 0;
  $('dialog-target').innerHTML=esc(state.modal.title)+`<code>${esc(state.modal.task_id)}</code>`;
  $('instruction').hidden=kind!=='instruct_task';$('instruction-label').hidden=kind!=='instruct_task';$('instruction').value=old?.instruction || '';
  $('confirm-label').hidden=kind!=='hold_task';$('hold-confirm').checked=false;$('dialog-error').hidden=true;$('dialog-submit').disabled=false;
  $('command-dialog').showModal();if(kind==='instruct_task') $('instruction').focus();
}
function updateCommandCopy(){
  const kind=state.modal?.kind;if(!kind) return;
  const names={set_recovery_policy:tr('调整恢复策略','Adjust recovery policy'),hold_task:tr('暂停代理','Hold proxy'),resume_task:tr('恢复任务','Resume task'),instruct_task:tr('追加指令','Add instructions')};
  $('dialog-title').textContent=names[kind];
  $('dialog-description').textContent=kind==='hold_task' ? tr('秉烛人将撤销当前代理所有权并停止会话。已启动的作业继续运行。','Candleholder will revoke the current proxy and stop its session. Existing jobs keep running.'):kind==='resume_task' ? tr('恢复任务的继续执行意图。秉烛人将按状态与预算分配代理，并核验已有作业。','Restore ACTIVE intent. Candleholder assigns a proxy according to state and budget, checking existing jobs.'):kind==='set_recovery_policy' ? tr('修改持久恢复模式与累计运行额度；不会自动恢复。额度为代理会话墙钟时间，不是模型账单。','Change recovery mode and total proxy runtime budget. This does not automatically Resume; runtime is wall-clock time, not a model bill.'):tr('指令写入持久任务；回执成功表示要求已记录，不代表代理已完成工作。','Instructions enter the durable task. An applied receipt means recorded, not that the proxy has finished the work.');
  $('dialog-submit').textContent=kind==='hold_task' ? tr('确认暂停','Confirm hold'):kind==='resume_task' ? tr('确认恢复','Confirm resume'):tr('提交指令','Submit instruction');
}
async function submitCommand(e){
  e.preventDefault();if(state.submitting || !state.modal) return;
  const item=state.modal, kind=item.kind;
  if(kind==='hold_task' && !$('hold-confirm').checked){$('dialog-error').textContent=tr("请明确确认停止当前代理；已启动的作业将保留。","Confirm stopping the current proxy. Existing jobs are retained.");$('dialog-error').hidden=false;return;}
  if(kind==='instruct_task' && !$('instruction').value.trim()){ $('instruction').focus();return;}
  if(kind==='set_recovery_policy') item.payload=item.payload || {recovery_mode:$('policy-mode').value,max_proxy_seconds:Number($('policy-budget').value)};
  item.instruction=kind==='instruct_task' ? $('instruction').value.trim():undefined;
  if(!state.receipts.includes(item)) state.receipts.unshift(item);state.receipts=state.receipts.slice(0,20);
  item.status='SENDING';state.submitting=true;$('dialog-submit').disabled=true;$('dialog-error').hidden=true;storeReceipts();renderReceipts();
  try {
    const data=await http('/api/rpc',{request:{method:'yan.'+kind,params:{task_id:item.task_id,idempotency_key:item.key,payload:kind==='instruct_task' ? {instruction:item.instruction}:item.payload || {}}},confirmed:kind==='hold_task'});
    const receipt=result(data);Object.assign(item,{id:receipt.id,status:receipt.status,error:receipt.error,result:receipt.result,updated_at:receipt.updated_at});
    $('command-dialog').close();$('receipts').hidden=false;notice(tr("指令已接收，可通过命令回执核验效果。","Command received. Check its receipt for the result."));
  }catch(err){item.status=err.code && err.code!=='DELIVERY_UNCONFIRMED' ? 'NOT_SENT':'UNCONFIRMED';item.error=err.code ? err.code+': '+err.message:err.message;$('dialog-error').textContent=item.error+tr("。保留原幂等键，避免重复提交。"," Retaining the original idempotency key to prevent duplicate submission.");$('dialog-error').hidden=false;}
  finally{state.submitting=false;$('dialog-submit').disabled=false;storeReceipts();renderReceipts();refresh();}
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-reader-options]')){state.readerOptions=!state.readerOptions;render();}
  const copy=e.target.closest('[data-copy-code],[data-copy-source]');
  if(copy){const text=copy.closest('.code-block,.raw-content')?.querySelector('pre')?.textContent || '';navigator.clipboard.writeText(text).then(()=>notice(tr('已复制','Copied'))).catch(()=>notice(tr('复制失败，请展开原文手动复制。','Copy failed. Open the source and copy manually.')));}
  const reader=document.querySelector('[data-transcript-list]'),jumpReader=e.target.closest('[data-reader-jump]');
  if(jumpReader && reader){state.followTranscript=jumpReader.dataset.readerJump==='last';$('transcript-follow').checked=state.followTranscript;reader.scrollTop=state.followTranscript ? reader.scrollHeight:0;}
  if(e.target.closest('[data-next-incident]') && reader){
    state.followTranscript=false;$('transcript-follow').checked=false;
    const marks=[...reader.querySelectorAll('[data-incident-marker]')],top=reader.getBoundingClientRect().top;
    const next=marks.find(n=>n.getBoundingClientRect().top>top+35) || marks[0];
    if(next){reader.scrollTop+=next.getBoundingClientRect().top-top-18;next.focus({preventScroll:true});}
  }
  if(e.target.closest('.skip-link')){e.preventDefault();$('main').focus();$('main').scrollIntoView({block:'start'});}
  const tab=e.target.closest('[data-task-tab]');if(tab) selectTaskTab(tab.dataset.taskTab);
  const jump=e.target.closest('[data-jump]');if(jump){const target=$(jump.dataset.jump);if(target){target.scrollIntoView({block:'start'});target.focus({preventScroll:true});}}
  if(e.target.closest('[data-create-task]') && canControl()) openTask();
  const command=e.target.closest('[data-command]');if(command && !command.disabled) openCommand(command.dataset.command);
  const retry=e.target.closest('[data-retry]');if(retry){const old=state.receipts.find(i=>i.key===retry.dataset.retry);if(old) openCommand(old.kind,old);}
  const page=e.target.closest('[data-page]');if(page && !page.disabled){state.offset=Math.max(0,state.offset+(page.dataset.page==='next' ? state.limit:-state.limit));refresh();}
  const history=e.target.closest('[data-history]');if(history && !history.disabled) loadHistory(history.dataset.history,Number(history.dataset.after || 0));
  const job=e.target.closest('[data-job]');if(job){state.jobId=job.dataset.job;state.jobData=null;refresh();}
  if(e.target.closest('[data-action="events"]')) refresh();
  if(e.target.closest('[data-action="transcript"]')) refresh();
});
document.addEventListener('keydown',e=>{
  const tab=e.target.closest('[data-task-tab]');if(!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
  e.preventDefault();const keys=Object.keys(taskTabs),index=keys.indexOf(tab.dataset.taskTab);
  const next=e.key==='Home' ? 0:e.key==='End' ? keys.length-1:(index+(e.key==='ArrowRight' ? 1:-1)+keys.length)%keys.length;
  selectTaskTab(keys[next]);$('tab-'+keys[next]).focus({preventScroll:true});
});
function filterInput(e){if(e.isComposing) return;if(e.target.id==='list-search'){state.search=e.target.value;render();}if(e.target.id==='transcript-search'){state.transcriptSearch=e.target.value;state.followTranscript=false;render();}}
document.addEventListener('input',filterInput);
document.addEventListener('compositionend',filterInput);
document.addEventListener('change',e=>{if(e.target.id==='list-filter'){state.filter=e.target.value;render();}});
document.addEventListener('change',e=>{if(e.target.id==='transcript-filter'){state.transcriptFilter=e.target.value;state.resetDisclosure=true;render();}if(e.target.id==='transcript-follow'){state.followTranscript=e.target.checked;render();}});
document.addEventListener('scroll',e=>{const el=e.target;if(el.matches?.('[data-transcript-list]') && el.scrollTop+el.clientHeight<el.scrollHeight-40){state.followTranscript=false;if($('transcript-follow')) $('transcript-follow').checked=false;}},true);
$('refresh-now').addEventListener('click',refresh);
$('language').value=YanI18n.language;
$('language').addEventListener('change',e=>{
  YanI18n.set(e.target.value);updateCommandCopy();
  clearTimeout(toastTimer);$('toast').hidden=true;
  $('pause-refresh').innerHTML=icon(state.paused ? 'play':'pause')+`<span>${state.paused ? tr('恢复','Resume'):tr('暂停','Pause')}</span>`;
  render();
});
$('pause-refresh').addEventListener('click',()=>{state.paused=!state.paused;$('pause-refresh').innerHTML=icon(state.paused ? 'play':'pause')+`<span>${state.paused ? tr('恢复','Resume'):tr('暂停','Pause')}</span>`;if(state.paused) clearTimeout(timer);else refresh();updateHealth();});
$('refresh-interval').addEventListener('change',e=>{state.interval=Number(e.target.value);schedule();updateHealth();});
$('receipt-toggle').addEventListener('click',()=>$('receipts').hidden=!$('receipts').hidden);$('receipt-close').addEventListener('click',()=>$('receipts').hidden=true);
const closeDialog=()=>{if(!state.submitting) $('command-dialog').close();};$('dialog-close').addEventListener('click',closeDialog);$('dialog-cancel').addEventListener('click',closeDialog);$('command-dialog').addEventListener('cancel',e=>{if(state.submitting)e.preventDefault();});$('command-form').addEventListener('submit',submitCommand);
window.addEventListener('hashchange',()=>{state.offset=0;state.search='';state.filter='';state.task=null;state.taskTab='summary';state.historyPage=null;state.historyError=null;state.incident=null;state.events=[];state.jobId=null;state.jobData=null;state.error=null;state.transcript=null;state.transcriptItems=[];state.transcriptAfter=0;state.transcriptError=null;state.transcriptDiscarded=0;state.transcriptFilter='reader';state.transcriptSearch='';state.proxyIncidents=[];state.incidentAfter=0;state.incidentHasMore=false;state.incidentError=null;state.incidentDiscarded=0;state.followTranscript=true;$('main').innerHTML=`<div class='loading'><span class='loading-dot'></span>${tr('正在读取账本…','Reading the ledger…')}</div>`;refresh();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);updateHealth();}else if(!state.paused) refresh();});
setInterval(updateHealth,1000);renderReceipts();refresh();

let taskDraft=null;
const taskFields={title:'task-title',objective:'task-objective',completion_criteria:'task-criteria',workspace:'task-workspace',adapter:'task-adapter',execution_profile:'task-profile',verification_template:'task-verifier',recovery_mode:'task-recovery',max_proxy_seconds:'task-budget'};
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
    item.payload.max_proxy_seconds=Number(item.payload.max_proxy_seconds);
    item.title=item.payload.title;
  }
  if(!state.receipts.includes(item)) state.receipts.unshift(item);
  item.status='SENDING';state.submitting=true;$('task-submit').disabled=true;
  for(const id of Object.values(taskFields)) $(id).disabled=true;
  storeReceipts();renderReceipts();
  try{
    const receipt=result(await http('/api/rpc',{request:{method:'yan.submit_task',params:{idempotency_key:item.key,payload:item.payload}}}));
    Object.assign(item,{id:receipt.id,task_id:receipt.task_id,status:receipt.status,error:receipt.error,result:receipt.result,updated_at:receipt.updated_at});
    $('task-dialog').close();$('receipts').hidden=false;notice(tr("任务已提交；通过回执确认应用结果。","Task submitted. Check the receipt for the result."));
  }catch(err){
    item.status=err.code && err.code!=='DELIVERY_UNCONFIRMED' ? 'NOT_SENT':'UNCONFIRMED';item.error=err.message;
    $('task-error').textContent=err.message+tr("；重试保留原任务书和幂等键。"," Retrying retains the original brief and idempotency key.");$('task-error').hidden=false;
  }finally{state.submitting=false;$('task-submit').disabled=false;storeReceipts();renderReceipts();refresh();}
}
$('task-form').addEventListener('submit',submitTask);
for(const id of ['task-close','task-cancel']) $(id).addEventListener('click',()=>{if(!state.submitting)$('task-dialog').close();});
$('task-dialog').addEventListener('cancel',e=>{if(state.submitting)e.preventDefault();});
