/* 消息级引用快照与本页满意度，不发送反馈请求。 */
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
      const state = {key: crypto.randomUUID(), citations:new Map(), text:'', completed:false, failed:false, feedback:null};
      const storageKey = `mewhelp-feedback:${sessionId}:${state.key}`;
      const feedback = document.createElement('div'); feedback.className = 'response-feedback';
      const feedbackText = document.createElement('span'); feedbackText.setAttribute('aria-live','polite');
      const buttons = ['up','down'].map((choice,i) => {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = i ? '👎' : '👍'; button.setAttribute('aria-label',i?'不满意':'满意');
        button.addEventListener('click', () => {
          if (state.feedback || !state.completed || state.failed) return;
          state.feedback = choice;
          try { sessionStorage.setItem(storageKey,choice); } catch (_) { /* 内存状态仍锁定 */ }
          paintFeedback();
        });
        feedback.append(button); return button;
      });
      feedback.append(feedbackText);
      function paintFeedback() {
        try { state.feedback ||= sessionStorage.getItem(storageKey); } catch (_) {}
        buttons.forEach((button,i) => {button.disabled=!!state.feedback;button.classList.toggle('selected',state.feedback === (i?'down':'up'));button.setAttribute('aria-pressed',String(state.feedback === (i?'down':'up')));});
        feedbackText.textContent = state.feedback ? '已反馈' : '';
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
        setCitations(items) {state.citations.clear(); for(const item of items || []) {if(Number.isInteger(item.number)&&item.number>=1&&item.number<=10&&Number.isInteger(item.chunk_id)&&item.chunk_id>0&&typeof item.text==='string') state.citations.set(item.number,Object.freeze({...item}));} render(state.text);},
        complete() {state.completed=true; paintFeedback();},
        fail() {state.failed=true;state.completed=false;state.citations.clear();feedback.remove();if(dialog.open)dialog.close();},
      };
    }
  };
})();
