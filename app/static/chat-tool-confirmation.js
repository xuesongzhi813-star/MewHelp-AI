/* Ticket previews contain user data. Render every field as text. */
(() => {
  const views = new Set();
  const style = document.createElement('style');
  style.textContent = '.tool-confirmation{margin-top:14px;padding:15px;border:1px solid #a78bfa77;border-radius:12px;overflow-wrap:anywhere}.tool-confirmation p{white-space:pre-wrap;line-height:1.6}.tool-confirmation .controls{display:flex;gap:10px;flex-wrap:wrap}.tool-confirmation button{padding:9px 17px;border-radius:8px;border:1px solid #a78bfa77;background:#292138;color:#eee;cursor:pointer}.tool-confirmation button:disabled{opacity:.5;cursor:default}.tool-confirmation .status{font-size:12px;color:#b7a8ce}';
  document.head.append(style);
  const el = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };

  async function submit(request, emit, signal) {
    const response = await fetch('/api/chat/actions/tool-confirmation', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(request), signal
    });
    if (!response.ok) {
      const error = new Error('提交未完成，请刷新查看状态，请勿重复提交。');
      error.expired = response.status === 409 || response.status === 404;
      throw error;
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', terminal = false, outcome = false;
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream:true});
        buffer = buffer.replace(/\r\n/g, '\n');
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
          if (!data) continue;
          const event = JSON.parse(data);
          if (event.type === 'tool_confirmation_result') outcome = true;
          if (event.type === 'done') terminal = true;
          emit(event);
        }
      }
    } finally { reader.releaseLock(); }
    if (!terminal || !outcome) throw new Error('连接提前结束，提交结果待核实，请勿重复提交。');
  }

  window.createToolConfirmationView = ({container, sessionId, submit:transport=submit, onEvent=()=>{}, isCurrent=()=>true}) => {
    const root = el('section', ''); root.className = 'tool-confirmation'; root.hidden = true; container.append(root);
    const title = el('strong', '工单预览'), type = el('p', ''), description = el('p', ''), status = el('p', '');
    status.className = 'status'; status.setAttribute('role', 'status');
    const row = el('div', ''); row.className = 'controls';
    const confirm = el('button', '确认提交'), cancel = el('button', '取消');
    confirm.type = cancel.type = 'button'; row.append(confirm, cancel); root.append(title, type, description, row, status);
    let preview = null, ready = false, disposed = false, controller = null;
    const labels = {available:'请核对信息，确认后才会创建工单。',submitting:'正在提交，请勿重复操作…',
      completed:'工单已提交。',cancelled:'已取消，未创建工单。',expired:'预览已失效，请以最新消息为准。',
      rejected:'本次提交未获授权，请重新核对。',uncertain:'提交结果待核实，请联系人工客服，请勿重复提交。'};
    function enabled() {
      confirm.disabled = cancel.disabled = disposed || !ready || preview?.status !== 'available';
    }
    function setStatus(value, result='') {
      if (!preview) return;
      preview.status = value;
      status.textContent = result || labels[value] || labels.uncertain;
      enabled();
    }
    async function choose(decision) {
      if (confirm.disabled || disposed || !isCurrent()) return;
      setStatus('submitting'); controller = new AbortController();
      try {
        await transport({session_id:sessionId, preview_id:preview.preview_id, decision}, event => {
          if (disposed || !isCurrent()) return;
          if (event.type === 'tool_confirmation_result' && event.preview_id === preview.preview_id) setStatus(event.status, event.result);
          if (event.type === 'error') setStatus('uncertain', event.message + ' 请勿重复提交。');
          onEvent(event);
        }, controller.signal);
      } catch (error) {
        if (!disposed && isCurrent()) setStatus(error.expired ? 'expired' : 'uncertain');
      }
    }
    confirm.addEventListener('click', () => choose('confirm'));
    cancel.addEventListener('click', () => choose('cancel'));
    const view = {root,
      setPreview(value) {
        if (disposed || !isCurrent()) return;
        preview = {...value}; root.hidden = false;
        type.textContent = '类型：' + value.ticket_type; description.textContent = value.description;
        setStatus(value.status || 'available', value.result);
      },
      complete() { ready = true; enabled(); },
      fail() { if (preview?.status === 'available' || preview?.status === 'submitting') setStatus('uncertain'); },
      expire() { if (preview?.status === 'available') setStatus('expired'); },
      dispose() { disposed = true; controller?.abort(); enabled(); views.delete(view); }
    };
    views.add(view); return view;
  };
  window.ToolConfirmationUI = {
    expireAll() { for (const view of views) { if (!view.root.isConnected) view.dispose(); else view.expire(); } },
    disposeAll() { for (const view of [...views]) view.dispose(); }
  };
})();
