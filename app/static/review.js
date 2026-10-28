/* Human decisions and source text are rendered as text, never as markup. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), api=Admin.api;
  const el=(tag,text='',className='')=>{const n=document.createElement(tag);n.textContent=text;n.className=className;return n;};
  const button=(text,action)=>{const b=el('button',text);b.type='button';b.addEventListener('click',()=>action(b));return b;};
  const selected=new Set(); let rows=[],after=0,next=null,history=[],detail=null,draft=null,editors=[],releaseRows=[];
  const stages={prepared:'等待发布',writing:'写入知识中',pending:'知识已保存，正在向量化',done:'正在校准',profile:'正在切换版本',complete:'已生效',failed:'发布失败'};
  const sources={retrieval_low_conf:'检索证据不足',self_check:'生成前自评不足',user_feedback:'用户反馈未解决'};
  function message(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('fly-error',error);}
  async function busy(b,fn){b.disabled=true;try{await fn();}finally{b.disabled=false;}}
  const refKey=r=>[r.kind,r.review_id || r.source,r.fingerprint,r.approved_content_hash || r.source_hash].join(':');
  function selection(){ $('selectionCount').textContent=`已选 ${selected.size} 条核准知识`; }
  function published(id){return releaseRows.some(r=>r.stage==='complete' && r.review_ids.includes(id));}
  function renderRows(){
    $('queueRows').replaceChildren();$('queueCount').textContent=`本页 ${rows.length} 个缺口`;
    if(!rows.length){const cell=el('td','当前筛选下没有问题。','fly-note');cell.colSpan=6;const row=el('tr');row.append(cell);$('queueRows').append(row);}
    for(const item of rows){
      const row=el('tr'),pick=el('input');pick.type='checkbox';pick.disabled=item.review_status!=='通过';pick.checked=selected.has(item.id);
      pick.setAttribute('aria-label',`选择核准知识 ${item.id}`);pick.addEventListener('change',()=>{pick.checked?selected.add(item.id):selected.delete(item.id);selection();});
      const select=el('td');select.append(pick);
      const question=el('td',item.normalized_question,'fly-question'),sample=el('td',item.ai_suggested_answer || '暂无示例答案','fly-sample');
      const status=el('td',item.review_status+(published(item.id)?' · 已生效':''));
      const actions=el('td');actions.append(button('查看与审核',()=>openDetail(item.id)));
      row.append(select,question,el('td',String(item.occurrence_count)),sample,status,actions);$('queueRows').append(row);
    }
    $('previousPage').disabled=!history.length;$('nextPage').disabled=next===null;selection();
  }
  async function loadQueue(){
    message('queueMessage','正在读取问题队列…');
    try{const query=new URLSearchParams({after:String(after),limit:'20'});if($('statusFilter').value)query.set('status',$('statusFilter').value);
      const data=await api('/api/review?'+query);rows=data.items;next=data.next_after;renderRows();message('queueMessage','');
    }catch(error){message('queueMessage',error.message,true);}
  }
  function renderEvents(events){
    $('eventRows').replaceChildren();
    if(!events.length)$('eventRows').append(el('p','没有来源事件。','fly-note'));
    for(const event of events){
      const card=el('article','','fly-source'),snapshot=event.snapshot || {};
      card.append(el('strong',event.raw_question),el('p',`${sources[event.source] || event.source} · 事件 #${event.id}`,'fly-note'));
      if(event.reason)card.append(el('p',event.reason,'fly-note'));
      if(snapshot.identity){const identity=snapshot.identity;card.append(el('p',`会话 ${identity.session_id} · 用户消息 ${identity.user_message_id} · 回答消息 ${identity.assistant_message_id ?? '待归档'}`,'fly-note'));
        if(identity.trace_id)card.append(el('p','Trace ID：'+identity.trace_id,'fly-note'));}
      const hits=snapshot.hits || [];
      if(!hits.length){const labels={not_retrieved:'该轮没有检索（如闲聊或业务数据查询）。',no_hits:'该轮检索没有召回片段。',snapshot_unavailable:'历史检查点不可用，未重新检索补造证据。',legacy_unknown:'历史记录没有完整快照。'};
        card.append(el('p',labels[snapshot.snapshot_status] || '该轮没有可用的证据快照。','fly-note'));}
      for(const hit of hits){const fragment=el('details'),summary=el('summary',`片段 #${hit.chunk_id} · ${hit.section_path || '未标注章节'}`);
        fragment.append(summary,el('p',`重排分 ${hit.rerank_score ?? '—'} · 原分 ${hit.score ?? '—'} · 分数类型 ${hit.score_type || '未知'}`,'fly-note'),el('pre',hit.answer || hit.text || ''));
        card.append(fragment);}
      $('eventRows').append(card);
    }
  }
  async function openDetail(id){
    try{detail=await api('/api/review/'+id);$('detailTitle').textContent=detail.normalized_question;
      $('detailState').textContent=`${detail.review_status} · 出现 ${detail.occurrence_count} 次${published(id)?' · 已有生效发布':''}`;
      $('suggestedAnswer').value=detail.ai_suggested_answer || '';$('approvedAnswer').value=detail.approved_answer || detail.ai_suggested_answer || '';
      $('approvedAnswer').readOnly=detail.review_status!=='待审';$('approveReview').hidden=$('rejectReview').hidden=detail.review_status!=='待审';
      $('selectApproved').hidden=detail.review_status!=='通过';renderEvents(detail.events || []);message('detailMessage','');
      if(!$('detailDialog').open)$('detailDialog').showModal();
    }catch(error){message('queueMessage',error.message,true);}
  }
  $('approveReview').addEventListener('click',event=>busy(event.target,async()=>{
    const answer=$('approvedAnswer').value.trim();if(!answer){message('detailMessage','请填写核准答案。',true);return;}
    try{await api(`/api/review/${detail.id}/approve`,{method:'POST',body:{approved_answer:answer}});selected.add(detail.id);await openDetail(detail.id);await loadQueue();
      message('detailMessage','已人工核准并加入发布批。请关闭详情，检查标注影响后发布。');
    }catch(error){message('detailMessage',error.message,true);}
  }));
  $('rejectReview').addEventListener('click',event=>busy(event.target,async()=>{
    try{await api(`/api/review/${detail.id}/reject`,{method:'POST'});selected.delete(detail.id);await openDetail(detail.id);await loadQueue();message('detailMessage','已驳回。之后同义问题只累计次数，不会自动重开。');}
    catch(error){message('detailMessage',error.message,true);}
  }));
  $('selectApproved').addEventListener('click',()=>{selected.add(detail.id);selection();message('detailMessage','已加入发布批，请关闭详情后检查标注影响。');});
  function field(label,value,rows=3){const wrapper=el('label','','fly-field'),input=el('textarea');input.rows=rows;input.value=value || '';wrapper.append(el('span',label),input);return {wrapper,input};}
  function refLabel(ref){return ref.kind==='review'?`核准知识 #${ref.review_id} · ${draft.reviews.find(r=>r.id===ref.review_id)?.normalized_question || ''}`:`原知识 · ${ref.section_path}（${ref.source}）`;}
  function renderImpact(){
    $('impactKnowledge').replaceChildren();$('impactCases').replaceChildren();editors=[];
    for(const review of draft.reviews){const item=el('details','','fly-source');item.open=true;item.append(el('summary',`本批核准知识 #${review.id} · ${review.normalized_question}`),el('p',review.approved_answer,'fly-note'));$('impactKnowledge').append(item);}
    const proposals=new Map(draft.suggestions.map(s=>[s.case_id,s])),catalog=new Map();
    for(const c of draft.cases)for(const ref of c.relevant_chunk_refs)catalog.set(refKey(ref),ref);
    for(const review of draft.reviews)for(const chunk of review.chunks)catalog.set(refKey(chunk.ref),chunk.ref);
    for(const c of draft.cases){
      const suggestion=proposals.get(c.id) || {affected:false,changes:{},reason:'无模型建议，请人工核对。'},value={...c,...suggestion.changes};
      const card=el('details','','fly-case');card.open=!!suggestion.affected;
      card.append(el('summary',`${suggestion.affected?'建议修订 · ':''}${c.raw_query}`));
      card.append(el('p',`${c.id} · ${c.split==='calibration'?'校准题':'保留题'} · 原标注：${c.answerable?'可回答':'不可回答'}`,'fly-note'));
      card.append(el('p','旧标准答案：'+c.ground_truth_answer,'fly-note'),el('p','建议理由：'+suggestion.reason,'fly-note'));
      const adopt=el('input');adopt.type='checkbox';adopt.checked=!!suggestion.affected;const adoptLabel=el('label','','fly-check');adoptLabel.append(adopt,el('span','修订此题（可人工补充模型漏报）'));card.append(adoptLabel);
      const form=el('div');form.hidden=!adopt.checked;adopt.addEventListener('change',()=>{form.hidden=!adopt.checked;updateImpactSummary();});
      const answerable=el('input');answerable.type='checkbox';answerable.checked=!!value.answerable;const answerLabel=el('label','','fly-check');answerLabel.append(answerable,el('span','有明确核准证据支持，可回答所问事实'));form.append(answerLabel);
      const truth=field('修订后的标准答案',value.ground_truth_answer),facts=field('需要覆盖的事实（每行一条）',(value.required_facts || []).join('\n')),
        forbidden=field('禁止承诺（每行一条）',(value.forbidden_claims || []).join('\n')),reason=field('本次人工修订理由',suggestion.reason,2);
      form.append(truth.wrapper,facts.wrapper,forbidden.wrapper,reason.wrapper);
      const refs=el('details');refs.append(el('summary','选择支持该题的知识来源'));refs.open=adopt.checked;const refList=el('div','','fly-sources'),checks=[];
      const ordered=new Map();for(const ref of [...c.relevant_chunk_refs,...(value.relevant_chunk_refs || []),...catalog.values()])ordered.set(refKey(ref),ref);
      const chosen=new Set((value.relevant_chunk_refs || []).map(refKey));
      for(const ref of ordered.values()){const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=chosen.has(refKey(ref));const label=el('label','','fly-check');label.append(checkbox,el('span',refLabel(ref)));refList.append(label);checks.push({ref,checkbox});}
      refs.append(refList);form.append(refs);card.append(form);$('impactCases').append(card);
      answerable.addEventListener('change',()=>{if(!answerable.checked){facts.input.value='';checks.forEach(item=>item.checkbox.checked=false);}});
      editors.push({c,adopt,answerable,truth:truth.input,facts:facts.input,forbidden:forbidden.input,reason:reason.input,checks});
    }
    $('confirmUnchanged').checked=false;$('confirmPublish').disabled=false;updateImpactSummary();
  }
  function updateImpactSummary(){const count=editors.filter(e=>e.adopt.checked).length;$('impactSummary').textContent=`固定 ${draft.cases.length} 题 · 当前选择检查修订 ${count} 题，其余题目需要明确确认保持不变。`;}
  $('previewBatch').addEventListener('click',event=>busy(event.target,async()=>{
    if(!selected.size){message('queueMessage','请先审核通过并选择至少一条知识。',true);return;}
    message('queueMessage','正在检查固定96题的标注影响，会真实调用模型，请勿重复提交…');
    try{draft=await api('/api/review/impact',{method:'POST',body:{review_ids:[...selected].sort((a,b)=>a-b)}});
      if(!Array.isArray(draft.cases) || draft.cases.length!==96)throw new Error('原题标注读取不完整，请刷新后重试。');
      renderImpact();message('impactMessage','');message('queueMessage','标注影响已生成，等待人工确认。');$('impactDialog').showModal();
    }catch(error){message('queueMessage',error.message,true);}
  }));
  const lines=input=>input.value.split('\n').map(s=>s.trim()).filter(Boolean);
  $('confirmPublish').addEventListener('click',event=>busy(event.target,async()=>{
    if(!$('confirmedBy').value.trim() || !$('confirmUnchanged').checked){message('impactMessage','请填写审核标识，并确认其余题目保持不变。',true);return;}
    const edits=[],unchanged=[];
    for(const e of editors){
      if(!e.adopt.checked){unchanged.push(e.c.id);continue;}
      const value={answerable:e.answerable.checked,ground_truth_answer:e.truth.value.trim(),required_facts:lines(e.facts),forbidden_claims:lines(e.forbidden),
        relevant_chunk_refs:e.checks.filter(item=>item.checkbox.checked).map(item=>item.ref)};
      if(!value.ground_truth_answer || !e.reason.value.trim() || value.answerable!==!!value.required_facts.length || value.answerable!==!!value.relevant_chunk_refs.length){message('impactMessage',`请核对 ${e.c.id}：标准答案和理由必填，可答题需要事实及来源，不可答题两者留空。`,true);return;}
      const changes={};for(const [key,v] of Object.entries(value)){const compare=x=>key==='relevant_chunk_refs'?JSON.stringify(x.map(refKey).sort()):JSON.stringify(x);if(compare(v)!==compare(e.c[key]))changes[key]=v;}
      if(Object.keys(changes).length)edits.push({case_id:e.c.id,changes,reason:e.reason.value.trim()});else unchanged.push(e.c.id);
    }
    message('impactMessage','人工确认已提交，正在发布与校准。知识问答将暂时维护，请勿重复操作…');
    try{const release=await api('/api/review/impact/confirm',{method:'POST',body:{digest:draft.digest,edits,unchanged_case_ids:unchanged,confirmed_by:$('confirmedBy').value.trim()}});
      message('releaseMessage',release.stage==='complete'?'本批知识已生效，可回到聊天页验证。':'发布未完成，请在下方查看状态并续跑。',release.stage!=='complete');
      $('impactDialog').close();if(release.stage==='complete')selected.clear();await loadReleases();await loadQueue();
    }catch(error){message('impactMessage',error.message+' 已确认的批次请在发布与恢复中检查状态。',true);await loadReleases();}
  }));
  async function loadReleases(){
    try{const data=await api('/api/review/releases');releaseRows=data.items;$('releaseRows').replaceChildren();
      if(!releaseRows.length)$('releaseRows').append(el('p','尚无发布记录。','fly-note'));
      for(const release of releaseRows){const item=el('article','','fly-release');item.append(el('strong',stages[release.stage] || release.stage),el('p',release.release_id,'fly-note'),el('p',`核准知识 ${release.review_ids.map(id=>'#'+id).join('、')} · 标注版本 ${release.dataset_version}`,'fly-note'));
        if(release.stage==='failed')item.append(el('p',`${release.write_started?'知识维护中':'写入前失败，旧知识可用'} · ${release.error_code || '未知故障'}`,'fly-error'));
        if(release.stage!=='complete')item.append(button('续跑本次发布',b=>busy(b,async()=>{message('releaseMessage','正在续跑并校准，请稍候…');try{const result=await api(`/api/review/releases/${release.release_id}/retry`,{method:'POST'});message('releaseMessage',result.stage==='complete'?'续跑成功，知识已生效。':'仍未完成，请检查故障后再试。',result.stage!=='complete');await loadReleases();await loadQueue();}catch(error){message('releaseMessage',error.message,true);}})));
        $('releaseRows').append(item);}
      renderRows();
    }catch(error){message('releaseMessage',error.message,true);}
  }
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
  $('reloadQueue').addEventListener('click',loadQueue);$('reloadReleases').addEventListener('click',loadReleases);
  $('statusFilter').addEventListener('change',()=>{after=0;history=[];loadQueue();});
  $('nextPage').addEventListener('click',()=>{if(next!==null){history.push(after);after=next;loadQueue();}});
  $('previousPage').addEventListener('click',()=>{if(history.length){after=history.pop();loadQueue();}});
  loadQueue();loadReleases();
})();
