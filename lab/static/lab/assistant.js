(() => {
  'use strict';
  const panel = document.getElementById('lab-assistant');
  if (!panel) return;
  const $ = id => document.getElementById(`assistant-${id}`);
  const launcher = $('launcher'), backdrop = $('backdrop'), input = $('input');
  const messagesNode = $('messages'), scroll = $('scroll'), statusNode = $('status');
  const resize = $('resize');
  const desktop = window.matchMedia('(min-width: 1400px)');
  const storageKey = `kudo-assistant-v1:${panel.dataset.employee}`;
  const preferenceKey = `${storageKey}:open`;
  const widthKey = `${storageKey}:width`;
  const promptsKey = `${storageKey}:prompts`;
  const maxMessages = 16;
  let messages = [], controller = null, configured = null, open = false;
  let prompts = [], promptIndex = null;
  let turn = 0, savedOpen = null;
  let preferredWidth = 374, drag = null;
  let following = true, activityTimer = null, activityStarted = 0, activitySteps = [];
  scroll.addEventListener('scroll', () => { following = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80; }, {passive:true});
  const followResponse = () => { if (following) scroll.scrollTop = scroll.scrollHeight; };
  const progressParts = value => Array.isArray(value) ? value.filter(part => typeof part === 'string' && part.trim()).slice(0, 8).map(part => part.slice(0, 12000)) : [];
  try {
    savedOpen = sessionStorage.getItem(preferenceKey);
    const savedWidth = Number(sessionStorage.getItem(widthKey));
    if (Number.isFinite(savedWidth) && savedWidth > 0) preferredWidth = savedWidth;
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) {
      for (const item of saved.slice(-maxMessages)) {
        const expected = messages.length % 2 === 0 ? 'user' : 'assistant';
        if (item && item.role === expected && typeof item.content === 'string' && item.content.trim() && item.content.length <= 12000) {
          messages.push({...item, progress:progressParts(item.progress)});
        }
      }
    }
  } catch { /* A disabled or full browser store must not block the chat. */ }

  const loadPrompts = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(promptsKey) || 'null');
      if (Array.isArray(saved)) prompts = saved.filter(text => typeof text === 'string' && text.trim() && text.length <= 6000).slice(-25);
    } catch { /* Recall also works in memory when local storage is unavailable. */ }
  };
  // Make questions from an existing conversation available on first use, too.
  prompts = messages.filter(message => message.role === 'user' && message.content.length <= 6000).map(message => message.content).slice(-25);
  loadPrompts();
  const rememberPrompt = text => {
    loadPrompts();
    prompts.push(text);
    prompts = prompts.slice(-25);
    promptIndex = null;
    try { localStorage.setItem(promptsKey, JSON.stringify(prompts)); } catch {}
  };

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
  const tableRow = line => {
    const source = line.trim(), cells = [];
    let cell = '', codeDelimiter = '', hasPipe = false;
    for (let index = 0; index < source.length; index++) {
      const character = source[index];
      if (character === '\\' && index + 1 < source.length) {
        const next = source[++index];
        cell += next === '|' ? '|' : '\\' + next;
      } else if (character === '`') {
        let ticks = '`';
        while (source[index + 1] === '`') { ticks += '`'; index++; }
        if (codeDelimiter === ticks) codeDelimiter = '';
        else if (!codeDelimiter && source.slice(index + 1).match(/`+/g)?.includes(ticks)) codeDelimiter = ticks;
        cell += ticks;
      } else if (character === '|' && !codeDelimiter) {
        cells.push(cell.trim()); cell = ''; hasPipe = true;
      } else cell += character;
    }
    cells.push(cell.trim());
    if (source.startsWith('|')) cells.shift();
    if (hasPipe && source.endsWith('|') && cells.at(-1) === '') cells.pop();
    return {cells, hasPipe};
  };
  const markdown = (target, text) => {
    let paragraph = null, list = null, code = null;
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      if (line.trim().startsWith('```')) {
        paragraph = null; list = null;
        if (code) code = null;
        else { const pre = element('pre'); code = element('code'); pre.append(code); target.append(pre); }
        continue;
      }
      if (code) { code.append(document.createTextNode((code.textContent ? '\n' : '') + line)); continue; }
      if (!line.trim()) { paragraph = null; list = null; continue; }
      const header = tableRow(line), separator = tableRow(lines[index + 1] || '');
      if (header.hasPipe && header.cells.length && header.cells.length === separator.cells.length && separator.cells.every(cell => /^:?-{3,}:?$/.test(cell))) {
        paragraph = null; list = null;
        const wrapper = element('div', 'assistant-table-scroll');
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', 'Таблица в ответе помощника');
        const table = element('table'), head = element('thead'), body = element('tbody');
        const alignment = separator.cells.map(cell => cell.endsWith(':') ? (cell.startsWith(':') ? 'center' : 'right') : 'left');
        const appendRow = (section, cells, heading = false) => {
          const row = element('tr');
          header.cells.forEach((_, column) => {
            const cell = element(heading ? 'th' : 'td');
            if (heading) cell.scope = 'col';
            cell.style.textAlign = alignment[column];
            inline(cell, cells[column] || '');
            row.append(cell);
          });
          section.append(row);
        };
        appendRow(head, header.cells, true);
        index++;
        while (index + 1 < lines.length && lines[index + 1].trim() && !/^\s*(?:```|#{1,6}\s|(?:[-*]|\d+[.)])\s)/.test(lines[index + 1])) {
          appendRow(body, tableRow(lines[++index]).cells);
        }
        table.append(head, body); wrapper.append(table); target.append(wrapper);
        continue;
      }
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
  const showSources = (article, message) => {
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
  };
  const showMessage = message => {
    const article = element('article', `assistant-message is-${message.role}`);
    article.append(element('div', 'assistant-message-label', message.role === 'assistant' ? '✦  Помощник' : 'Вы'));
    if (message.role === 'assistant') {
      for (const part of progressParts(message.progress)) {
        const stage = element('div', 'assistant-message-body assistant-message-stage');
        markdown(stage, part);
        article.append(stage);
      }
    }
    const body = element('div', 'assistant-message-body');
    if (message.role === 'assistant') markdown(body, message.content);
    else body.textContent = message.content;
    article.append(body);
    showSources(article, message);
    messagesNode.append(article);
    return article;
  };
  const render = () => {
    messagesNode.replaceChildren();
    messages.forEach(showMessage);
    $('welcome').hidden = messages.length > 0;
    following = true;
    scroll.scrollTop = scroll.scrollHeight;
  };
  const widthBounds = () => {
    const viewport = document.documentElement.clientWidth;
    const sidebar = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sidebar')) || 244;
    const min = Math.min(340, viewport);
    // Leave enough room to use the laboratory when the panel is docked.
    const max = Math.max(min, Math.min(960, desktop.matches ? viewport - sidebar - 600 : viewport - 32));
    return {min, max, viewport, sidebar};
  };
  const setWidth = (value = preferredWidth, remember = false) => {
    const {min, max, viewport, sidebar} = widthBounds();
    const width = Math.round(Math.max(min, Math.min(max, value)));
    document.documentElement.style.setProperty('--assistant-width', `${width}px`);
    resize.hidden = viewport < 640;
    resize.setAttribute('aria-valuemin', String(min));
    resize.setAttribute('aria-valuemax', String(max));
    resize.setAttribute('aria-valuenow', String(width));
    resize.setAttribute('aria-valuetext', `${width} пикселей`);
    document.body.classList.toggle('assistant-narrow-workspace', desktop.matches && viewport - sidebar - width < 760);
    if (remember) {
      preferredWidth = width;
      try { sessionStorage.setItem(widthKey, String(width)); } catch { /* Resizing also works without storage. */ }
    }
  };
  const stopResize = () => {
    if (!drag) return;
    const pointer = drag.pointer;
    drag = null;
    document.body.classList.remove('assistant-resizing');
    if (resize.hasPointerCapture(pointer)) resize.releasePointerCapture(pointer);
  };
  resize.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.isPrimary || resize.hidden || drag) return;
    event.preventDefault();
    drag = {pointer:event.pointerId, x:event.clientX, width:panel.getBoundingClientRect().width};
    resize.setPointerCapture(event.pointerId);
    resize.focus();
    document.body.classList.add('assistant-resizing');
  });
  resize.addEventListener('pointermove', event => {
    if (drag?.pointer === event.pointerId) setWidth(drag.width + drag.x - event.clientX, true);
  });
  resize.addEventListener('pointerup', stopResize);
  resize.addEventListener('pointercancel', stopResize);
  resize.addEventListener('lostpointercapture', stopResize);
  resize.addEventListener('keydown', event => {
    const {min, max} = widthBounds();
    const width = panel.getBoundingClientRect().width, step = event.shiftKey ? 60 : 20;
    const next = {ArrowLeft:width + step, ArrowRight:width - step, Home:min, End:max}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setWidth(next, true);
  });
  window.addEventListener('resize', () => { stopResize(); setWidth(); });
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
    if (!value) stopResize();
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
    if (event.key === 'Escape') { event.preventDefault(); if (drag) stopResize(); else setOpen(false, true); }
    if (event.key !== 'Tab' || desktop.matches) return;
    const nodes = [...panel.querySelectorAll('button:not(:disabled), textarea, a[href], [tabindex="0"]')].filter(node => node.getClientRects().length);
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
  const activity = text => {
    if (typeof text !== 'string' || !text.trim()) return;
    text = text.trim().slice(0, 240);
    if (activitySteps.at(-1) === text) return;
    $('activity-text').textContent = text;
    activitySteps.push(text);
    activitySteps = activitySteps.slice(-32);
    $('steps-list').replaceChildren(...activitySteps.map(step => element('li', '', step)));
    $('steps-summary').textContent = `Действия · ${activitySteps.length}`;
    $('steps').hidden = activitySteps.length < 2;
    followResponse();
  };
  const busy = value => {
    clearInterval(activityTimer);
    activityTimer = null;
    if (value) {
      activityStarted = Date.now();
      activitySteps = [];
      $('steps').open = false;
      $('elapsed').textContent = '';
      activity('Думаю…');
      activityTimer = setInterval(() => {
        const seconds = Math.floor((Date.now() - activityStarted) / 1000);
        $('elapsed').textContent = seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`;
      }, 1000);
    }
    $('pending').hidden = !value;
    $('cancel').hidden = !value;
    $('send').hidden = value;
    $('send').disabled = value || configured === false;
    $('retry').disabled = value;
    input.readOnly = value;
    messagesNode.setAttribute('aria-busy', String(value));
    panel.querySelectorAll('[data-assistant-prompt]').forEach(node => { node.disabled = value; });
    followResponse();
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
  const formState = () => {
    const state = {title:'', forms:[], truncated:false};
    const limit = (value, maximum) => {
      if (value.length > maximum) state.truncated = true;
      const result = value.slice(0, maximum);
      return /[\uD800-\uDBFF]$/.test(result) ? result.slice(0, -1) : result;
    };
    const readable = value => value.replace(/\s+/g, ' ').trim();
    const types = new Set(['text','search','number','textarea','select-one','select-multiple','checkbox','radio','date','datetime-local','time','email','tel','url','range','color','week','month']);
    const privateName = /(?:^|[_-])(?:csrfmiddlewaretoken|csrf|password|passwd|secret|token|api[_-]?key)(?:$|[_-])/i;
    state.title = limit(readable(document.querySelector('.main-shell main h1')?.textContent || document.title), 200);
    let count = 0;
    collect: for (const form of document.querySelectorAll('.main-shell main form')) {
      let snapshot = null;
      for (const node of form.querySelectorAll('input, select, textarea')) {
        if (!node.name || privateName.test(node.name) || !types.has(node.type) || node.matches(':disabled') || !node.getClientRects().length) continue;
        if (count === 200 || (!snapshot && state.forms.length === 3)) { state.truncated = true; break collect; }
        if (!snapshot) {
          snapshot = {method:form.method === 'post' ? 'post' : 'get', fields:[]};
          state.forms.push(snapshot);
        }
        const label = [...(node.labels || [])].map(source => {
          const copy = source.cloneNode(true);
          copy.querySelectorAll('.required').forEach(marker => marker.remove());
          return readable(copy.textContent);
        }).filter(Boolean).join(' · ') || node.getAttribute('aria-label') || node.name;
        const name = limit(node.name, 120);
        if (privateName.test(name)) continue;
        const field = {name, label:limit(readable(label), 160), type:node.type, value:'', changed:false};
        const section = node.closest('.panel')?.querySelector('h2')?.textContent;
        if (section) field.section = limit(readable(section), 160);
        if (node.type === 'checkbox' || node.type === 'radio') {
          field.value = node.checked;
          field.display = limit(node.value, 160);
          field.changed = node.checked !== node.defaultChecked;
        } else if (node.tagName === 'SELECT') {
          const selected = [...node.selectedOptions];
          if (selected.length > 20) state.truncated = true;
          if (node.multiple) {
            field.value = selected.slice(0, 20).map(option => limit(option.value, 2000));
            field.display = selected.slice(0, 20).map(option => limit(readable(option.textContent), 160));
            field.changed = [...node.options].some(option => option.selected !== option.defaultSelected);
          } else {
            field.value = limit(node.value, 2000);
            field.display = limit(readable(selected[0]?.textContent || ''), 160);
            const original = [...node.options].filter(option => option.defaultSelected).at(-1)
              || [...node.options].find(option => !option.disabled && !option.parentElement.disabled);
            field.changed = node.value !== (original?.value || '');
          }
        } else {
          field.value = limit(node.value, 2000);
          field.changed = node.value !== node.defaultValue;
        }
        snapshot.fields.push(field);
        count++;
        if (JSON.stringify(state).length > 24000) {
          snapshot.fields.pop();
          if (!snapshot.fields.length) state.forms.pop();
          state.truncated = true;
          break collect;
        }
      }
    }
    // Form drafts are captured only for this request, never in chat history.
    return count ? state : undefined;
  };
  const readStream = async (response, onEvent, signal) => {
    if (!response.body) throw new Error('Не удалось открыть поток ответа. Повторите запрос.');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', received = 0;
    try {
      while (true) {
        signal.throwIfAborted();
        const {value, done} = await reader.read();
        signal.throwIfAborted();
        buffer += decoder.decode(value, {stream:!done});
        received += value?.byteLength || 0;
        if (buffer.length > 256000 || received > 2000000) throw new Error('Поток ответа слишком большой. Попробуйте сузить вопрос.');
        let boundary;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const block = buffer.slice(0, boundary.index);
          buffer = buffer.slice(boundary.index + boundary[0].length);
          let event = 'message';
          const data = [];
          for (const line of block.split(/\r?\n/)) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
          }
          if (!data.length || !['status', 'delta', 'done', 'error'].includes(event)) continue;
          let payload;
          try { payload = JSON.parse(data.join('\n')); }
          catch { throw new Error('Не удалось прочитать поток ответа. Повторите запрос.'); }
          if (!payload || typeof payload !== 'object') throw new Error('Сервер вернул некорректный ответ. Повторите запрос.');
          if (event === 'error') throw new Error(typeof payload.error === 'string' ? payload.error : 'Не удалось подготовить ответ. Повторите запрос.');
          onEvent(event, payload);
          if (event === 'done') return payload;
        }
        if (done) throw new Error('Соединение прервалось до завершения ответа. Повторите запрос.');
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
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
      rememberPrompt(text);
      input.value = '';
    }
    if (messages.at(-1)?.role !== 'user') return;
    while (messages.length > maxMessages - 1 || messages.reduce((total, item) => total + item.content.length, 0) > 48000) messages.splice(0, 2);
    persist(); render();
    const ownController = new AbortController(), ownTurn = ++turn;
    controller = ownController; busy(true);
    let draft = null, draftBody = null, draftText = '', frame = null;
    const draftStages = [];
    const paintDraft = () => {
      frame = null;
      if (ownTurn !== turn) return;
      if (!draftText) { draftBody?.replaceChildren(); return; }
      if (!draft) {
        draft = showMessage({role:'assistant', content:''});
        draft.classList.add('is-streaming');
        draft.setAttribute('aria-label', 'Помощник печатает ответ');
        draftBody = draft.querySelector('.assistant-message-body');
      }
      draftBody.replaceChildren();
      markdown(draftBody, draftText);
      followResponse();
    };
    const timeout = setTimeout(() => ownController.abort('timeout'), 190000);
    try {
      const response = await fetch(panel.dataset.chatUrl, {
        method:'POST', credentials:'same-origin', signal:ownController.signal,
        headers:{'Content-Type':'application/json', Accept:'text/event-stream', 'X-CSRFToken':$('form').querySelector('[name=csrfmiddlewaretoken]').value},
        body:JSON.stringify({messages:messages.map(({role,content}) => ({role,content})), page:location.pathname, form_state:formState()}),
      });
      const contentType = response.headers.get('content-type') || '';
      let data;
      if (response.ok && contentType.includes('text/event-stream')) {
        data = await readStream(response, (event, payload) => {
          if (ownTurn !== turn) return;
          if (event === 'status') {
            if (payload.reset === true && draftText.trim()) {
              if (frame !== null) cancelAnimationFrame(frame);
              paintDraft();
              // A tool call starts a new segment; keep the public text already shown.
              draftStages.push(draftText);
              draftBody.classList.add('assistant-message-stage');
              draftText = '';
              draftBody = element('div', 'assistant-message-body');
              draft.append(draftBody);
            }
            activity(payload.text);
          } else if (event === 'delta' && typeof payload.text === 'string') {
            activity('Пишу ответ…');
            const next = (draftText + payload.text).slice(0, 12000);
            if (next !== draftText) {
              draftText = next;
              if (frame === null) frame = requestAnimationFrame(paintDraft);
            }
          }
        }, ownController.signal);
      } else {
        if (!contentType.includes('application/json')) throw new Error('Сеанс завершён или сервер недоступен. Обновите страницу и войдите снова.');
        data = await response.json();
        if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'Не удалось получить ответ. Попробуйте ещё раз.');
      }
      if (typeof data.reply !== 'string' || !data.reply.trim()) throw new Error('Модель вернула пустой ответ. Попробуйте ещё раз.');
      if (ownTurn !== turn) return;
      if (frame !== null) { cancelAnimationFrame(frame); frame = null; }
      const keepFollowing = following;
      messages.push({role:'assistant', content:data.reply.slice(0, 12000), progress:progressParts(draftStages), sources:Array.isArray(data.sources) ? data.sources : []});
      persist();
      if (draft) {
        // Complete the current body in place, leaving earlier stages untouched.
        draft.classList.remove('is-streaming');
        draft.removeAttribute('aria-label');
        draftBody.replaceChildren();
        markdown(draftBody, messages.at(-1).content);
        showSources(draft, messages.at(-1));
      } else showMessage(messages.at(-1));
      following = keepFollowing;
      followResponse();
    } catch (failure) {
      if (ownTurn !== turn) return;
      if (frame !== null) { cancelAnimationFrame(frame); frame = null; }
      paintDraft();
      if (draft) {
        draft.classList.remove('is-streaming');
        draft.classList.add('is-interrupted');
        draft.setAttribute('aria-label', 'Прерванный ответ помощника');
        draft.append(element('div', 'assistant-interrupted', 'Ответ прерван'));
      }
      error(ownController.signal.aborted ? (ownController.signal.reason === 'timeout' ? 'Ответ занял слишком много времени. Попробуйте сузить вопрос и повторить запрос.' : 'Запрос остановлен. Его можно повторить.') : failure.message || 'Нет соединения с сервером. Попробуйте ещё раз.');
    } finally {
      clearTimeout(timeout);
      if (frame !== null) cancelAnimationFrame(frame);
      if (ownTurn === turn) { controller = null; busy(false); }
    }
  };
  $('form').addEventListener('submit', event => { event.preventDefault(); void send(); });
  input.addEventListener('input', () => { promptIndex = null; });
  input.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); return; }
    if (!['ArrowUp', 'ArrowDown'].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || input.readOnly || controller) return;
    if (input.selectionStart !== input.selectionEnd) return;
    if (promptIndex === null) {
      if (input.value !== '' || event.key !== 'ArrowUp') return;
      loadPrompts();
      if (!prompts.length) return;
      promptIndex = prompts.length;
    }
    event.preventDefault();
    promptIndex = Math.max(0, Math.min(prompts.length, promptIndex + (event.key === 'ArrowUp' ? -1 : 1)));
    input.value = prompts[promptIndex] || '';
    if (promptIndex === prompts.length) promptIndex = null;
    input.setSelectionRange(input.value.length, input.value.length);
  });
  $('retry').addEventListener('click', () => { void send(true); });
  $('cancel').addEventListener('click', () => controller?.abort('cancelled'));
  $('new').addEventListener('click', () => {
    turn++; controller?.abort('new'); controller = null;
    messages = []; input.value = ''; promptIndex = null; persist(); render(); busy(false); $('error').hidden = true; input.focus();
  });
  panel.querySelectorAll('[data-assistant-prompt]').forEach(node => node.addEventListener('click', () => { input.value = node.dataset.assistantPrompt; input.focus(); void send(); }));
  document.querySelector('.logout-form')?.addEventListener('submit', () => { try { sessionStorage.removeItem(storageKey); } catch {} });
  render();
  setWidth();
  if (messages.at(-1)?.role === 'user') error('Предыдущий запрос не завершён. Его можно повторить.');
  setOpen(savedOpen === null ? desktop.matches : savedOpen === 'true', false, false);
  void loadStatus();
})();
