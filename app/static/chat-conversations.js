/* Browser-local anonymous grouping. The server owns canonical conversation IDs. */
(() => {
  const safeGet = (store, key) => { try { return store.getItem(key); } catch { return null; } };
  const safeSet = (store, key, value) => { try { store.setItem(key, value); } catch { /* chat remains usable */ } };
  const randomHex = () => [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, '0')).join('');
  const fresh = () => 'tmp-' + randomHex();
  let userId = safeGet(localStorage, 'mewhelp-user-id');
  if (!/^[a-f0-9]{32}$/.test(userId || '')) {
    userId = randomHex();
    safeSet(localStorage, 'mewhelp-user-id', userId);
  }
  let sessionId = safeGet(sessionStorage, 'mewhelp-chat-session') || fresh();
  let conversationId = null, callbacks = {}, loadingVersion = 0;
  function identify() {
    const match = /^u:([a-f0-9]{32}):c:([1-9][0-9]*)$/.exec(sessionId);
    conversationId = match && match[1] === userId ? Number(match[2]) : null;
    safeSet(sessionStorage, 'mewhelp-chat-session', sessionId);
  }
  identify();
  async function select(item) {
    const version = ++loadingVersion;
    try {
      const response = await fetch(`/api/conversations/${item.id}/messages?user_id=${encodeURIComponent(userId)}`);
      if (!response.ok) return;
      const data = await response.json();
      if (version !== loadingVersion) return;
      sessionId = data.session_id; identify();
      await callbacks.onSelect?.(data);
      await refresh();
    } catch { /* Failed history reload keeps the current chat. */ }
  }
  async function refresh() {
    try {
      const response = await fetch('/api/conversations?user_id=' + encodeURIComponent(userId));
      if (!response.ok) return;
      const data = await response.json();
      const list = document.getElementById('conversationList');
      if (!list) return;
      list.replaceChildren();
      for (const item of data.items || []) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'conversation-item'; button.dataset.conversationId = item.id;
        button.setAttribute('aria-current', String(item.session_id === sessionId));
        const preview = document.createElement('span'); preview.textContent = item.preview || '新对话';
        const label = document.createElement('small'); label.textContent = item.has_summary ? '已摘要 · 可继续聊' : '完整历史';
        button.append(preview, label); button.addEventListener('click', () => select(item)); list.append(button);
      }
    } catch { /* Sidebar is optional and must not interrupt chat. */ }
  }
  window.MewHelpConversations = {
    init(options) {
      callbacks = options;
      document.getElementById('newChatBtn').addEventListener('click', () => {
        loadingVersion++; sessionId = fresh(); identify(); callbacks.onNew?.(); refresh();
      });
      refresh();
      if (conversationId) select({id: conversationId});
    },
    applyIdentity(event) {
      sessionId = event.session_id; identify(); refresh();
    },
    refresh,
    currentRequestFields() {
      if (!conversationId && !sessionId.startsWith('tmp-')) return {session_id: sessionId};
      return {session_id: sessionId, user_id: userId, ...(conversationId ? {conversation_id: conversationId} : {})};
    }
  };
})();
