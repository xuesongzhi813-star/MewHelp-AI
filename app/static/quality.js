(() => {
  'use strict';
  const $=id=>document.getElementById(id),api=Admin.api;
  const el=(tag,text='',className='')=>{const n=document.createElement(tag);n.textContent=text;n.className=className;return n;};
  const fmt=(n,digits=3)=>typeof n==='number' && Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'N/A';
  const percent=n=>typeof n==='number' && Number.isFinite(n)?(n*100).toFixed(1)+'%':'N/A';
  const date=value=>{const d=new Date(value);return value && Number.isFinite(d.getTime())?d.toLocaleString('zh-CN'):'—';};
  const statuses={success:'有效完成',failed:'失败',invalid:'版本变化 · 无效',interrupted:'已中断',running:'运行中'};
  let points=[],runs=[];
  function message(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('fly-error',error);}
  function chart(series,key,label){
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 440 150');svg.classList.add('fly-chart');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+'同版本趋势');
    for(const y of [15,70,125]){const line=document.createElementNS(ns,'line');for(const [k,v] of Object.entries({x1:15,y1:y,x2:425,y2:y}))line.setAttribute(k,v);line.classList.add('grid');svg.append(line);}
    let path='';let connected=false;
    series.forEach((point,index)=>{const value=point.status==='success'?point.values[key]:null;
      if(typeof value!=='number' || !Number.isFinite(value)){connected=false;return;}
      const x=series.length===1?220:15+index*410/(series.length-1),y=125-Math.min(1,Math.max(0,value))*110;
      path+=(connected?'L':'M')+x+','+y;connected=true;
      const circle=document.createElementNS(ns,'circle');circle.setAttribute('cx',x);circle.setAttribute('cy',y);circle.setAttribute('r',4);circle.setAttribute('tabindex','0');
      const title=document.createElementNS(ns,'title');title.textContent=`轮次 #${point.id} · ${date(point.finished_at)} · ${label} ${fmt(value)}`;circle.append(title);svg.append(circle);
    });
    const line=document.createElementNS(ns,'path');line.setAttribute('d',path);line.classList.add('line');svg.prepend(line);return svg;
  }
  function renderMetrics(){
    const selected=$('seriesFilter').value,series=points.filter(p=>p.comparison_key===selected);
    $('metricCards').replaceChildren();
    for(const [key,title,denom] of [['recall_at_10','Recall@10','retrieval_denominator'],['mrr_at_10','MRR@10','retrieval_denominator'],['faithfulness','Faithfulness','faithfulness_denominator']]){
      const card=el('article','','admin-card'),valid=series.filter(p=>p.status==='success' && typeof p.values[key]==='number'),last=valid.at(-1);
      card.append(el('h2',title),el('p',last?fmt(last.values[key]):'N/A','fly-number'));
      const delta=last?.delta[key],change=typeof delta==='number'?(delta>0?'+':'')+delta.toFixed(4):'暂无上次可比结果';
      card.append(el('p',change,delta<0?'fly-delta-down':delta>0?'fly-delta-up':'fly-note'));
      card.append(chart(series,key,title),el('p',last?`有效分母 ${last.denominators[denom] ?? '—'} · ${date(last.finished_at)}`:'还没有有效数据。','fly-note'));
      $('metricCards').append(card);
    }
  }
  function renderRuns(){
    $('evalRows').replaceChildren();
    if(!runs.length){const td=el('td','尚无评估记录。','fly-note');td.colSpan=6;const tr=el('tr');tr.append(td);$('evalRows').append(tr);}
    for(const run of runs){const m=run.metrics,s=m.scores || {},valid=m.status==='success',tr=el('tr'),first=el('td',`#${run.id} · ${run.triggered_by}`);first.append(el('p',date(m.finished_at || m.started_at),'fly-note'));
      const details=el('details'),summary=el('summary',`回答覆盖 ${percent(s.coverage)} · 详情`);details.append(summary);
      const text=[`计划 ${run.dataset_size} 题，完成 ${m.completed_cases ?? '—'}，服务失败 ${m.failed_cases ?? s.service_failures ?? '—'}`,
        `检索分母 ${s.retrieval_denominator ?? '—'}，忠实度分母 ${s.faithfulness_denominator ?? '—'}，judge失败 ${s.judge_failures ?? '—'}`,
        `正确拒答 ${percent(s.correct_refusal_rate)}，误拒答 ${percent(s.false_refusal_rate)}`,
        `标注 ${m.dataset_version || '—'}`,`评分协议 ${m.metric_protocol_version || '—'}`,`发布 ${m.release_id || '—'}`,
        `生成模型 ${m.generation_model || '—'}，judge ${m.judge_model || '—'}`,m.error_code?'错误码 '+m.error_code:''].filter(Boolean).join('\n');
      details.append(el('p',text,'fly-note'));const diagnostic=el('td');diagnostic.append(details);
      const state=valid && s.judge_failures>0?'完成 · 评分不完整':statuses[m.status] || m.status;
      tr.append(first,el('td',state,valid && s.judge_failures>0?'fly-error':''),el('td',valid?fmt(s.recall_at_10):'—'),el('td',valid?fmt(s.mrr_at_10):'—'),el('td',valid?fmt(s.faithfulness):'—'),diagnostic);$('evalRows').append(tr);
    }
  }
  async function loadEvaluations(quiet=false){
    if(!quiet)message('evalMessage','正在读取评估记录…');
    try{const [list,trend]=await Promise.all([api('/api/evaluations/runs'),api('/api/evaluations/trends')]);runs=list.items;points=trend.points;
      const previous=$('seriesFilter').value,groups=new Map();for(const point of [...points].reverse())if(!groups.has(point.comparison_key))groups.set(point.comparison_key,point);
      $('seriesFilter').replaceChildren();for(const [key,p] of groups){const option=el('option',`${p.identities.dataset_version || '未完成版本绑定'} · 序列 ${key.slice(0,8)}`);option.value=key;$('seriesFilter').append(option);}
      if(!groups.size){const option=el('option','暂无可比较序列');option.value='';$('seriesFilter').append(option);}
      if(groups.has(previous))$('seriesFilter').value=previous;renderMetrics();renderRuns();if(!quiet)message('evalMessage','');
    }catch(error){message('evalMessage',error.message,true);}
  }
  $('runEvaluation').addEventListener('click',async event=>{
    const button=event.target;button.disabled=true;message('evalMessage','正在运行72题真实模型评估。此页可查看进度，请勿重复触发。');
    const timer=setInterval(()=>loadEvaluations(true),5000);
    try{const result=await api('/api/evaluations/run',{method:'POST',body:{trigger:'手动'}});await loadEvaluations(true);
      message('evalMessage',`轮次 #${result.id}：${statuses[result.status] || result.status}。`,result.status!=='success');
    }catch(error){message('evalMessage',error.message,true);}finally{clearInterval(timer);button.disabled=false;}
  });
  async function loadCosts(){
    const button=$('refreshCosts');button.disabled=true;message('costMessage','正在读取Cloud统计，可能需要分页与限流等待…');
    try{const query=new URLSearchParams({purpose:$('costPurpose').value});
      for(const [id,key] of [['costFrom','from'],['costTo','to']]){const value=$(id).value;if(value){const parsed=new Date(value);if(!Number.isFinite(parsed.getTime()))throw new Error('请选择有效日期。');query.set(key,parsed.toISOString());}}
      const report=await api('/api/observability/costs?'+query);$('costRows').replaceChildren();
      if(!report.rows.length){const td=el('td',report.complete?'当前窗口没有可用的Generation记录。':'统计暂不可用，不能据此认为消耗为零。','fly-note');td.colSpan=7;const tr=el('tr');tr.append(td);$('costRows').append(tr);}
      for(const item of report.rows){const tr=el('tr');tr.append(el('td',item.intent),el('td',`${item.request_count} / ${item.generation_count}`),el('td',item.input_tokens===null?'未知':fmt(item.input_tokens,0)),el('td',item.output_tokens===null?'未知':fmt(item.output_tokens,0)),el('td',item.total_tokens===null?'未知':fmt(item.total_tokens,0)),el('td',item.cost_usd===null?'未知':fmt(item.cost_usd,6)),el('td',`usage未知 ${item.unknown_usage_count} 次 · 价格未知 ${item.unknown_cost_count} 次`));$('costRows').append(tr);}
      $('costAsOf').textContent=`数据时间 ${date(report.as_of)} · 窗口 ${date(report.start)} 至 ${date(report.end)} · ${report.cached?'缓存':'Cloud读取'}${report.stale?'（旧缓存）':''}`;
      message('costMessage',report.complete?'统计分页完整。':`统计不完整${report.error_code?' · '+report.error_code:''}${report.retry_at?' · 可重试时间 '+date(report.retry_at):''}。${report.stale?'已保留旧结果及原时间戳。':'请稍后再试。'}`,!report.complete);
    }catch(error){message('costMessage',error.message+' 原有读数未被改成零。',true);}finally{button.disabled=false;}
  }
  async function loadStatus(){try{const state=await api('/api/observability/status');$('cloudState').textContent=`Langfuse：${state.status==='ready'?'连接就绪':state.status==='disabled'?'未启用':'观测降级'} · 日本区 · 核心问答不依赖观测连接。`;}catch(error){$('cloudState').textContent='观测状态暂不可读。';}}
  $('seriesFilter').addEventListener('change',renderMetrics);$('refreshEvaluations').addEventListener('click',()=>loadEvaluations());$('refreshCosts').addEventListener('click',loadCosts);
  loadEvaluations();loadStatus();loadCosts();
})();
