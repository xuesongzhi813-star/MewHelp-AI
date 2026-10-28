/* Server suggestions are inert until a completed reply; each action is independent. */
(() => {
  const style = document.createElement('style');
  style.textContent = '.chat-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.chat-actions button,.ticket-confirm button{border:1px solid #fbbf2466;border-radius:8px;padding:7px 12px;background:#fbbf2412;color:#f3ead7;cursor:pointer}.chat-actions button:disabled{opacity:.55;cursor:default}.ticket-confirm{margin:auto;max-width:min(460px,90vw);background:#1e180e;color:#f3ead7;border:1px solid #fbbf2466;border-radius:14px;padding:24px}.ticket-confirm::backdrop{background:#0009}.ticket-confirm pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:16px 0;line-height:1.7}.ticket-confirm button{margin-right:10px}.chat-action-status{width:100%;font-size:12px;color:#cbb88f}';
  document.head.append(style);

  window.createActionView = function ({container, sessionId, appendMessage, submitTicket}) {
    const root = document.createElement('div'); root.className = 'chat-actions';
    const status = document.createElement('span'); status.className = 'chat-action-status'; status.setAttribute('aria-live', 'polite');
    const states = new Map();
    let complete = false, failed = false;
    const submit = submitTicket || (async payload => {
      const response = await fetch('/api/chat/actions/ticket', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || result.status !== 'completed') {
        const error=new Error(typeof result.detail==='string'?result.detail:'工单结果待核实，请勿重复提交。');
        error.rejected=[422,403].includes(response.status);throw error;
      }
      return result;
    });

    function paint() {
      root.replaceChildren();
      for (const item of states.values()) {
        if(item.state==='rejected')status.textContent=item.result||'校验未通过，未创建工单。';
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = item.state === 'completed' ? (item.kind === 'handoff' ? '已转接' : '工单已创建') : item.label;
        button.disabled = !complete || failed || item.state !== 'available';
        button.dataset.actionId = item.action_id;
        button.addEventListener('click', () => {
          if (!complete || failed || item.state !== 'available') return;
          if (item.kind === 'handoff') {
            item.state = 'completed'; paint();
            appendMessage('已转接人工客服');
            appendMessage('您好，我是客服小猫，请问有什么可以帮您的');
          } else showConfirmation(item);
        });
        root.append(button);
      }
      root.append(status);
      if (states.size) container.append(root);
    }

    function showConfirmation(item) {
      const dialog = document.createElement('dialog'); dialog.className = 'ticket-confirm';
      dialog.setAttribute('aria-label', '确认创建工单');
      const title = document.createElement('h2'); title.textContent = '确认创建工单';
      const details = document.createElement('pre'); details.textContent = `类型：${item.ticket_type || '咨询'}\n内容：${item.description || ''}`;
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = '取消';
      const confirm = document.createElement('button'); confirm.type = 'button'; confirm.textContent = '确认创建';
      function close() { dialog.close(); dialog.remove(); }
      cancel.addEventListener('click', close);
      dialog.addEventListener('cancel', () => dialog.remove());
      confirm.addEventListener('click', async () => {
        if (item.state !== 'available' || failed) return;
        close(); item.state = 'submitting'; status.textContent = '正在创建工单…'; paint();
        try {
          const result = await submit({session_id: typeof sessionId === 'function' ? sessionId() : sessionId, action_id: item.action_id, confirmed: true});
          item.state = 'completed'; status.textContent = result.result;
        } catch (error) {
          item.state = error.rejected ? 'rejected' : 'uncertain';
          item.result = error.rejected ? error.message : '工单结果待核实，请联系客服核实，请勿重复提交。';
          status.textContent = item.result;
        }
        paint();
      });
      dialog.append(title, details, cancel, confirm); document.body.append(dialog); dialog.showModal();
    }

    return {
      setActions(items) {
        for (const item of items || []) {
          if (typeof item.action_id !== 'string' || !['handoff', 'create_ticket'].includes(item.kind)) continue;
          if (!states.has(item.action_id)) states.set(item.action_id, {...item, state: item.status || 'available', label: item.kind === 'handoff' ? '转人工' : '建工单'});
        }
        paint();
      },
      complete() { complete = true; paint(); },
      fail() { failed = true; complete = false; paint(); }
    };
  };
})();
