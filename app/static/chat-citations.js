/* 消息级引用快照；不满意反馈只提交服务端签发的轮次身份。 */
(() => {
  const style = document.createElement('style');
  style.textContent = `.citation-button{border:1px solid #2dd4bf66;border-radius:6px;background:#2dd4bf15;color:#5eead4;padding:0 5px;font:inherit;cursor:pointer}.response-feedback{display:flex;gap:8px;align-items:center;margin-top:12px;font-size:12px;color:#a08c6a}.response-feedback button{border:1px solid #a08c6a66;background:transparent;border-radius:7px;padding:4px 9px;cursor:pointer}.response-feedback button.selected{background:#fbbf2425;border-color:#fbbf24}.response-feedback button:disabled{cursor:default}.source-dialog{margin:auto;width:min(620px,92vw);max-height:85vh;overflow:auto;background:#1e180e;color:#f3ead7;border:1px solid #fbbf2466;border-radius:16px;padding:24px}.source-dialog::backdrop{background:#000a}.source-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin:20px 0}.source-dialog h2{font-size:18px}.source-dialog p{margin-top:12px;color:#a08c6a;overflow-wrap:anywhere}.source-dialog a{color:#5eead4}.source-dialog button{float:right;background:transparent;color:#f3ead7;border:1px solid #a08c6a;border-radius:7px;padding:6px 12px;cursor:pointer}.citation-button:focus-visible,.response-feedback button:focus-visible{outline:2px solid #fbbf24;outline-offset:3px}@media(max-width:520px){.bubble-wrap{max-width:88%}header{flex-wrap:wrap;padding:10px}.status-chip{display:none}}`;
  document.head.append(style);
  const dialog = document.createElement('dialog');
  dialog.className = 'source-dialog';
  dialog.setAttribute('aria-label', '引用来源');
  const close = document.createElement('button'); close.textContent = '关闭';
  close.addEventListener('click', () => dialog.close());
  const title = document.createElement('h2'), path = document.createElement('p');
  const original = document.createElement('pre'), link = document.createElement('a');
  link.textContent = '定位原文 ↗'; link.target = '_blank'; link.rel = 'noopener';
  dialog.append(close, title, path, original, link); document.body.append(dialog);
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r=dialog.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dialog.close(); } });
  function openSource(item) {
    title.textContent = `引用 [${item.number}] · 来源 #${item.chunk_id}`;
    path.textContent = item.section_path || '未标注章节'; original.textContent = item.text;
    link.href = '/kb?chunk_id=' + item.chunk_id;
    if (!dialog.open) dialog.showModal();
  }
  window.ChatResponseView = {
    create({bubble, body, sessionId}) {
      const state = {key: crypto.randomUUID(), citations:new Map(), text:'', completed:false, failed:false,
        feedback:null, turn:null, pending:false, feedbackError:''};
      let storageKey = `mewhelp-feedback:${sessionId}:${state.key}`;
      const feedback = document.createElement('div'); feedback.className = 'response-feedback';
      const feedbackText = document.createElement('span'); feedbackText.setAttribute('aria-live','polite');
      const buttons = ['up','down'].map((choice,i) => {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = i ? '👎' : '👍'; button.setAttribute('aria-label',i?'不满意':'满意');
        button.addEventListener('click', async () => {
          if (state.feedback || state.pending || !state.completed || state.failed) return;
          state.feedbackError='';
          if(choice==='down') {
            const turn=state.turn;
            if(!turn || !turn.assistant_message_id) {
              state.feedbackError='这条旧回答没有完整消息标识，请在当前版本重新提问后反馈。';
              paintFeedback();return;
            }
            state.pending=true;paintFeedback();
            try {
              const response=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({session_id:turn.session_id,user_message_id:turn.user_message_id,
                  assistant_message_id:turn.assistant_message_id,rating:'down'})});
              if(!response.ok)throw new Error('反馈未保存，请稍后重试。');
              const saved=await response.json();
              if(!Number.isInteger(saved.event_id))throw new Error('反馈结果暂不可确认，请重试。');
              state.feedback='down';
            } catch(error) {state.feedbackError=error.message || '反馈未保存，请重试。';}
            finally {state.pending=false;}
          } else state.feedback='up';
          if(state.feedback) {
            try {sessionStorage.setItem(storageKey,state.feedback);} catch (_) { /* 后端仍保证同轮幂等 */ }
          }
          paintFeedback();
        });
        feedback.append(button); return button;
      });
      feedback.append(feedbackText);
      function paintFeedback() {
        try { state.feedback ||= sessionStorage.getItem(storageKey); } catch (_) {}
        buttons.forEach((button,i) => {button.disabled=!!state.feedback || state.pending;button.classList.toggle('selected',state.feedback === (i?'down':'up'));button.setAttribute('aria-pressed',String(state.feedback === (i?'down':'up')));});
        feedbackText.textContent = state.pending ? '正在保存…' : state.feedbackError || (state.feedback === 'down' ? '已收集，待审核' : state.feedback ? '已反馈' : '');
        if (state.completed && !state.failed && state.text.trim()) bubble.append(feedback);
        else feedback.remove();
      }
      function render(text) {
        state.text = text; body.replaceChildren();
        const tokens = /(\*\*[^*]+\*\*|\[\d+\])/g; let offset=0, match;
        while ((match=tokens.exec(text))) {
          body.append(document.createTextNode(text.slice(offset,match.index)));
          const token=match[0], number=Number(token.slice(1,-1));
          if(token.startsWith('**')) {const strong=document.createElement('strong');strong.textContent=token.slice(2,-2);body.append(strong);}
          else if(state.citations.has(number) && !state.failed) {const button=document.createElement('button');button.type='button';button.className='citation-button';button.textContent=token;button.setAttribute('aria-label',`查看引用 ${number}`);button.addEventListener('click',()=>openSource(state.citations.get(number)));body.append(button);}
          else body.append(document.createTextNode(token));
          offset=match.index+token.length;
        }
        body.append(document.createTextNode(text.slice(offset))); paintFeedback();
      }
      return {render,
        setTurn(turn) {
          if(turn && turn.session_id===sessionId && Number.isInteger(turn.user_message_id) && turn.user_message_id>0) {
            state.turn=Object.freeze({session_id:turn.session_id,user_message_id:turn.user_message_id,
              assistant_message_id:Number.isInteger(turn.assistant_message_id) && turn.assistant_message_id>0 ? turn.assistant_message_id : null});
            if(state.turn.assistant_message_id)storageKey=`mewhelp-feedback:${sessionId}:message-${state.turn.assistant_message_id}`;
          }
        },
        setCitations(items) {state.citations.clear(); for(const item of items || []) {if(Number.isInteger(item.number)&&item.number>=1&&item.number<=10&&Number.isInteger(item.chunk_id)&&item.chunk_id>0&&typeof item.text==='string') state.citations.set(item.number,Object.freeze({...item}));} render(state.text);},
        complete() {state.completed=true; paintFeedback();},
        fail() {state.failed=true;state.completed=false;state.citations.clear();feedback.remove();if(dialog.open)dialog.close();},
      };
    }
  };
})();
