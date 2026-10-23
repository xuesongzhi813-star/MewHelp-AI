/* Chat-native demo orders and user-confirmed refund application. */
(() => {
  const views = new Set();
  const style = document.createElement('style');
  style.textContent = `.order-picker,.refund-panel{margin-top:14px;padding:14px;border:1px solid #fbbf2455;border-radius:12px;background:#fbbf2408;color:inherit}.order-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px;margin-top:10px}.order-card,.refund-panel button{padding:11px;border-radius:9px;border:1px solid #fbbf2455;background:#251e13;color:#f3ead7;cursor:pointer;text-align:left}.order-card span{display:block;margin:5px 0;font-size:12px;color:#cbb88f}.order-card:disabled,.refund-panel button:disabled{opacity:.5;cursor:default}.refund-panel form{display:grid;gap:11px;margin-top:12px}.refund-panel label{display:grid;gap:6px}.refund-panel select,.refund-panel textarea{max-width:100%;box-sizing:border-box;padding:10px;border:1px solid #fbbf2455;border-radius:7px;background:#211b13;color:#f3ead7;font:inherit}.refund-panel textarea{min-height:70px;resize:vertical}.refund-note,.workflow-stage{font-size:12px;color:#cbb88f;line-height:1.6}.refund-row{display:flex;gap:9px;flex-wrap:wrap}`;
  document.head.append(style);
  const el = (tag, text, cls) => { const node=document.createElement(tag); if(text)node.textContent=text; if(cls)node.className=cls; return node; };
  window.ChatRefundUI = {
    expireSelections() { for(const view of views){ if(!view.root.isConnected){views.delete(view);continue;} view.expire(); } },
    create({container, sessionId, onSelect, isBusy}) {
      const root=el('div'); container.append(root);
      const understood=el('div','','workflow-stage'); root.append(understood);
      const stage=el('div','','workflow-stage'); root.append(stage);
      let complete=false, failed=false, picker=null, expired=false;
      const actions=new Map();
      function availableButtons(){ return picker ? [...picker.querySelectorAll('button')] : []; }
      function setEnabled(){ for(const button of availableButtons())button.disabled=!complete||failed||expired; }
      const view={root,
        expire(){expired=true;setEnabled();if(picker){const n=picker.querySelector('.refund-note');if(n)n.textContent='此选择已结束或失效，请以最新消息为准。';}},
        setStage(payload){
          const labels={question_understood:'已理解本轮问题',read_order:'正在读取订单',expand_queries:'正在准备政策查询',retrieve_policy:'正在检索政策条款'};
          if(payload.question)understood.textContent='本轮问题：'+payload.question;
          stage.textContent=labels[payload.stage]||'正在处理';
        },
        setSelection(payload){
          if(picker)return;
          picker=el('section','','order-picker');
          picker.append(el('strong','请选择要处理的订单'));
          const note=el('p','演示订单 · 点选后将继续查询订单与政策，不会执行退款。','refund-note');
          const grid=el('div','','order-grid'); picker.append(note,grid); root.append(picker);
          for(const order of payload.items||[]){
            const button=el('button',order.product,'order-card');button.type='button';
            button.append(el('span','订单 '+order.order_id+' · '+order.status),el('span','实付 ¥'+Number(order.amount).toFixed(2)));
            button.addEventListener('click',async()=>{
              if(!complete||failed||expired||isBusy())return;
              expired=true;setEnabled();note.textContent='已选择订单 '+order.order_id+'，正在继续原请求…';
              const ok=await onSelect({session_id:sessionId,selection_id:payload.selection_id,order_id:order.order_id});
              note.textContent=ok?'已选择订单 '+order.order_id+'，处理结果见下方。':'本次续跑未完成，请重新提问或刷新页面恢复可用操作。';
            });
            grid.append(button);
          }
          setEnabled();
        },
        setActions(items){
          for(const action of items||[]){
            if(action.kind!=='refund_application'||actions.has(action.action_id))continue;
            actions.set(action.action_id,action);
            const panel=el('section','','refund-panel');root.append(panel);
            const button=el('button','提交演示退款申请');button.type='button';button.disabled=true;
            const note=el('p','仅提交申请，是否符合退款条件仍需审核；不会执行真实退款。','refund-note');panel.append(button,note);
            const status=action.status||'available';
            if(status==='completed'){button.textContent='申请已提交';note.textContent='演示申请已提交，待审核；未执行真实退款。'+(action.result||'');}
            else if(status!=='available'){note.textContent='申请结果待核实，请联系客服，请勿重复提交。';}
            action.button=button;action.available=status==='available';
            button.addEventListener('click',()=>{
              if(!complete||failed||!action.available||panel.querySelector('form'))return;
              const form=el('form'); const reasonLabel=el('label','退款原因');
              const select=el('select');select.required=true;select.setAttribute('aria-label','退款原因');
              const empty=el('option','请选择退款原因');empty.value='';select.append(empty);
              for(const reason of action.reasons||[]){const option=el('option',reason);option.value=reason;select.append(option);}
              reasonLabel.append(select);
              const noteLabel=el('label','补充说明（选填）');const textarea=el('textarea');textarea.maxLength=500;noteLabel.append(textarea);
              const row=el('div','','refund-row');const submit=el('button','确认提交');submit.type='submit';const cancel=el('button','取消');cancel.type='button';row.append(submit,cancel);
              form.append(el('div','订单 '+action.order_id+' · '+(action.product||'')),reasonLabel,noteLabel,row);panel.append(form);
              cancel.addEventListener('click',()=>{form.remove();note.textContent='已取消填写，未提交申请。';});
              form.addEventListener('submit',async event=>{
                event.preventDefault();if(!form.reportValidity()||!action.available||isBusy())return;
                action.available=false;button.disabled=true;submit.disabled=true;cancel.disabled=true;
                note.textContent='正在提交演示申请…';
                try{
                  const response=await fetch('/api/chat/actions/refund',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,action_id:action.action_id,reason:select.value,note:textarea.value,confirmed:true})});
                  const result=await response.json();
                  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'提交未完成，请核实申请状态。');
                  note.textContent=result.result;button.textContent='申请已提交';form.remove();
                }catch(error){note.textContent=error.message+' 请联系客服核实，请勿重复提交。';}
              });
            });
          }
          for(const action of actions.values())action.button.disabled=!complete||failed||!action.available;
        },
        complete(){complete=true;setEnabled();if(picker)stage.textContent='等待选择订单后继续';else if(actions.size)stage.textContent='订单与政策分析已完成';else if(stage.textContent)stage.textContent='本轮处理已结束';for(const a of actions.values())a.button.disabled=failed||!a.available;},
        fail(){failed=true;setEnabled();for(const a of actions.values())a.button.disabled=true;}
      };
      views.add(view);return view;
    }
  };
})();
