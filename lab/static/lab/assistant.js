(() => {
  'use strict';
  const panel = document.getElementById('lab-assistant');
  if (!panel) return;
  const $ = id => document.getElementById(`assistant-${id}`);
  const launcher = $('launcher'), backdrop = $('backdrop'), input = $('input');
  const messagesNode = $('messages'), scroll = $('scroll'), statusNode = $('status');
  const desktop = window.matchMedia('(min-width: 1400px)');
  const storageKey = `kudo-assistant-v1:${panel.dataset.employee}`;
  const preferenceKey = `${storageKey}:open`;
  const maxMessages = 16;
  let messages = [], controller = null, configured = null, open = false;
  let turn = 0, savedOpen = null;
  try {
    savedOpen = sessionStorage.getItem(preferenceKey);
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) {
      for (const item of saved.slice(-maxMessages)) {
        const expected = messages.length % 2 === 0 ? 'user' : 'assistant';
        if (item && item.role === expected && typeof item.content === 'string' && item.content.trim() && item.content.length <= 12000) messages.push(item);
      }
    }
  } catch { /* A disabled or full browser store must not block the chat. */ }

  const persist = () => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(messages.slice(-maxMessages))); }
    catch { /* The current page can still keep a conversation in memory. */ }
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const safeHref = value => {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\x00-\x20]/.test(value)) return null;
    try { const url = new URL(value, location.origin); return url.origin === location.origin ? url.pathname + url.search + url.hash : null; }
    catch { return null; }
  };
  // The model's text never enters innerHTML. Only same-site links become anchors.
  const inline = (target, text) => {
    const pattern = /\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(text.slice(offset, match.index)));
      if (match[1] !== undefined) {
        const href = safeHref(match[2]);
        const node = element(href ? 'a' : 'span', '', match[1]);
        if (href) node.href = href;
        target.append(node);
      } else target.append(element(match[3] !== undefined ? 'strong' : 'code', '', match[3] ?? match[4]));
      offset = match.index + match[0].length;
    }
    target.append(document.createTextNode(text.slice(offset)));
  };
  const markdown = (target, text) => {
    let paragraph = null, list = null, code = null;
    for (const line of text.split('\n')) {
      if (line.trim().startsWith('```')) {
        paragraph = null; list = null;
        if (code) code = null;
        else { const pre = element('pre'); code = element('code'); pre.append(code); target.append(pre); }
        continue;
      }
      if (code) { code.append(document.createTextNode((code.textContent ? '\n' : '') + line)); continue; }
      if (!line.trim()) { paragraph = null; list = null; continue; }
      const heading = line.match(/^#{1,6}\s+(.+)/);
      if (heading) { const node = element('h4'); inline(node, heading[1]); target.append(node); paragraph = null; list = null; continue; }
      const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)/);
      if (item) {
        const kind = item[1] ? 'UL' : 'OL';
        if (!list || list.tagName !== kind) { list = element(kind.toLowerCase()); target.append(list); }
        const node = element('li'); inline(node, item[2]); list.append(node); paragraph = null; continue;
      }
      list = null;
      if (!paragraph) { paragraph = element('p'); target.append(paragraph); }
      else paragraph.append(document.createTextNode('\n'));
      inline(paragraph, line);
    }
  };
  const showMessage = message => {
    const article = element('article', `assistant-message is-${message.role}`);
    article.append(element('div', 'assistant-message-label', message.role === 'assistant' ? '✦  Помощник' : 'Вы'));
    const body = element('div', 'assistant-message-body');
    if (message.role === 'assistant') markdown(body, message.content);
    else body.textContent = message.content;
    article.append(body);
    if (message.role === 'assistant' && Array.isArray(message.sources) && message.sources.length) {
      const sources = element('div', 'assistant-sources');
      sources.append(element('span', 'assistant-sources-label', 'Источники'));
      for (const source of message.sources.slice(0, 12)) {
        if (!source || typeof source.title !== 'string') continue;
        const href = safeHref(source.url);
        const node = element(href ? 'a' : 'span', href ? '' : 'assistant-source-text', source.title.slice(0, 180));
        if (href) node.href = href;
        sources.append(node);
      }
      article.append(sources);
    }
    messagesNode.append(article);
  };
  const render = () => {
    messagesNode.replaceChildren();
    messages.forEach(showMessage);
    $('welcome').hidden = messages.length > 0;
    scroll.scrollTop = scroll.scrollHeight;
  };
  const syncModal = () => {
    const modal = open && !desktop.matches;
    document.body.classList.toggle('assistant-modal-open', modal);
    backdrop.hidden = !modal;
    panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) panel.setAttribute('aria-modal', 'true');
    else panel.removeAttribute('aria-modal');
    document.querySelectorAll('.main-shell, .sidebar').forEach(node => { node.inert = modal; });
  };
  const setOpen = (value, focus = false, remember = true) => {
    open = value;
    panel.hidden = !value;
    launcher.setAttribute('aria-expanded', String(value));
    document.body.classList.toggle('assistant-open', value);
    syncModal();
    if (remember) { savedOpen = String(value); try { sessionStorage.setItem(preferenceKey, savedOpen); } catch {} }
    if (focus) (value ? $('close') : launcher).focus();
  };
  launcher.addEventListener('click', () => setOpen(true, true));
  $('close').addEventListener('click', () => setOpen(false, true));
  backdrop.addEventListener('click', () => setOpen(false, true));
  desktop.addEventListener('change', () => { if (savedOpen === null) setOpen(desktop.matches, false, false); else syncModal(); });
  document.addEventListener('keydown', event => {
    if (!open) return;
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false, true); }
    if (event.key !== 'Tab' || desktop.matches) return;
    const nodes = [...panel.querySelectorAll('button:not(:disabled), textarea, a[href]')].filter(node => node.getClientRects().length);
    const first = nodes[0], last = nodes.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });

  const error = (text, retry = true) => {
    $('error-text').textContent = text;
    $('retry').hidden = !retry;
    $('error').hidden = false;
    scroll.scrollTop = scroll.scrollHeight;
  };
  const busy = value => {
    $('pending').hidden = !value;
    $('cancel').hidden = !value;
    $('send').hidden = value;
    $('send').disabled = value || configured === false;
    $('retry').disabled = value;
    input.readOnly = value;
    messagesNode.setAttribute('aria-busy', String(value));
    panel.querySelectorAll('[data-assistant-prompt]').forEach(node => { node.disabled = value; });
    scroll.scrollTop = scroll.scrollHeight;
  };
  const loadStatus = async () => {
    try {
      const response = await fetch(panel.dataset.statusUrl, {headers:{Accept:'application/json'}, credentials:'same-origin'});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('status');
      const data = await response.json();
      configured = Boolean(data.configured);
      $('model').textContent = configured ? `Cloud.ru · ${data.model || 'ИИ-модель'}` : 'Cloud.ru · требуется подключение';
      statusNode.hidden = configured;
      statusNode.replaceChildren();
      if (!configured) {
        statusNode.append(document.createTextNode('Подключение к Cloud.ru ещё не настроено. Администратору нужно добавить API-ключ и выбрать модель на сервере. '));
        const button = element('button', '', 'Проверить настройки'); button.type = 'button';
        button.addEventListener('click', loadStatus); statusNode.append(button);
      }
    } catch {
      $('model').textContent = 'Cloud.ru · соединение недоступно';
      statusNode.hidden = false;
      statusNode.textContent = 'Не удалось проверить подключение. Можно попробовать отправить вопрос или обновить страницу.';
    }
    $('send').disabled = Boolean(controller) || configured === false;
  };
  const send = async (retry = false) => {
    if (controller || configured === false) return;
    const text = input.value.trim();
    if (!retry && !text) return;
    $('error').hidden = true;
    if (!retry) {
      // Replace an unanswered draft after a failed/cancelled turn; only completed
      // user/assistant pairs are ever followed by another user message.
      if (messages.at(-1)?.role === 'user') messages.pop();
      messages.push({role:'user', content:text});
      input.value = '';
    }
    if (messages.at(-1)?.role !== 'user') return;
    while (messages.length > maxMessages - 1 || messages.reduce((total, item) => total + item.content.length, 0) > 48000) messages.splice(0, 2);
    persist(); render();
    const ownController = new AbortController(), ownTurn = ++turn;
    controller = ownController; busy(true);
    const timeout = setTimeout(() => ownController.abort('timeout'), 190000);
    try {
      const response = await fetch(panel.dataset.chatUrl, {
        method:'POST', credentials:'same-origin', signal:ownController.signal,
        headers:{'Content-Type':'application/json', Accept:'application/json', 'X-CSRFToken':$('form').querySelector('[name=csrfmiddlewaretoken]').value},
        body:JSON.stringify({messages:messages.map(({role,content}) => ({role,content})), page:location.pathname}),
      });
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Сеанс завершён или сервер недоступен. Обновите страницу и войдите снова.');
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'Не удалось получить ответ. Попробуйте ещё раз.');
      if (typeof data.reply !== 'string' || !data.reply.trim()) throw new Error('Модель вернула пустой ответ. Попробуйте ещё раз.');
      if (ownTurn !== turn) return;
      messages.push({role:'assistant', content:data.reply.slice(0, 12000), sources:Array.isArray(data.sources) ? data.sources : []});
      persist(); render();
    } catch (failure) {
      if (ownTurn !== turn) return;
      error(ownController.signal.aborted ? (ownController.signal.reason === 'timeout' ? 'Ответ занял слишком много времени. Попробуйте сузить вопрос и повторить запрос.' : 'Запрос остановлен. Его можно повторить.') : failure.message || 'Нет соединения с сервером. Попробуйте ещё раз.');
    } finally {
      clearTimeout(timeout);
      if (ownTurn === turn) { controller = null; busy(false); }
    }
  };
  $('form').addEventListener('submit', event => { event.preventDefault(); void send(); });
  input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(); } });
  $('retry').addEventListener('click', () => { void send(true); });
  $('cancel').addEventListener('click', () => controller?.abort('cancelled'));
  $('new').addEventListener('click', () => {
    turn++; controller?.abort('new'); controller = null;
    messages = []; input.value = ''; persist(); render(); busy(false); $('error').hidden = true; input.focus();
  });
  panel.querySelectorAll('[data-assistant-prompt]').forEach(node => node.addEventListener('click', () => { input.value = node.dataset.assistantPrompt; input.focus(); void send(); }));
  document.querySelector('.logout-form')?.addEventListener('submit', () => { try { sessionStorage.removeItem(storageKey); } catch {} });
  render();
  if (messages.at(-1)?.role === 'user') error('Предыдущий запрос не завершён. Его можно повторить.');
  setOpen(savedOpen === null ? desktop.matches : savedOpen === 'true', false, false);
  void loadStatus();
})();
