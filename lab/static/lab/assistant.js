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
  const historyNoticeThreshold = 10;
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

  const updateHistoryNotice = () => {
    $('history-notice').hidden = messages.length < historyNoticeThreshold;
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const safeHref = value => {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\{}<>\x00-\x20]|%7[bd]|%3[ce]/i.test(value)) return null;
    try { const url = new URL(value, location.origin); return url.origin === location.origin ? url.pathname + url.search + url.hash : null; }
    catch { return null; }
  };
  const navigation = JSON.parse(document.getElementById('assistant-navigation').textContent)
    .map(item => ({...item, pattern:new RegExp(item.pattern)}));
  const routeLabel = value => {
    const path = value.split(/[?#]/)[0];
    return navigation.find(item => item.pattern.test(path))?.label || null;
  };
  const appendRoute = (target, value) => {
    const label = routeLabel(value);
    if (!label) return false;
    const href = safeHref(value);
    const node = element(href ? 'a' : 'span', '', label);
    if (href) node.href = href;
    target.append(node);
    return true;
  };
  // The model's text never enters innerHTML. Only same-site links become anchors.
  const inline = (target, text) => {
    const pattern = /\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`|(?<![\p{L}\p{N}_:/])\/[a-z][a-zA-Z0-9_/{\}?=&%#-]*/gu;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(text.slice(offset, match.index)));
      if (match[1] !== undefined) {
        const href = safeHref(match[2]);
        const node = element(href ? 'a' : 'span', '', routeLabel(match[1]) || match[1]);
        if (href) node.href = href;
        target.append(node);
      } else if (match[3] !== undefined) {
        const node = element('strong'); inline(node, match[3]); target.append(node);
      } else if (match[4] !== undefined) {
        if (!appendRoute(target, match[4])) target.append(element('code', '', match[4]));
      } else if (!appendRoute(target, match[0])) target.append(document.createTextNode(match[0]));
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
      const seen = new Set([...article.querySelectorAll('a')].map(node => node.getAttribute('href')));
      for (const source of message.sources.slice(0, 12)) {
        if (!source || typeof source.title !== 'string') continue;
        const href = safeHref(source.url);
        if (href && seen.has(href)) continue;
        if (href) seen.add(href);
        const node = element(href ? 'a' : 'span', href ? '' : 'assistant-source-text', source.title.slice(0, 180));
        if (href) node.href = href;
        sources.append(node);
      }
      if (sources.children.length > 1) article.append(sources);
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
    if (message.role === 'assistant' && message.interrupted === true) {
      article.classList.add('is-interrupted');
      article.setAttribute('aria-label', 'Прерванный ответ помощника');
      article.append(element('div', 'assistant-interrupted', 'Ответ прерван'));
    }
    messagesNode.append(article);
    return article;
  };
  const render = () => {
    updateHistoryNotice();
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
    if (event.key === 'Escape') {
      event.preventDefault();
      if (controller) void cancelCurrent();
      else if (drag) stopResize();
      else setOpen(false, true);
    }
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
    $('send').disabled = !ready || Boolean(controller) || configured === false;
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
  const chatsUrl = '/assistant/conversations/';
  const activeKey = `${storageKey}:conversation`;
  let chatId = null, activeJob = null, ready = false, submitting = false;
  let pollTimer = null, lastTranscript = '', nextBefore = null, pendingSubmission = null, listInitialized = false;
  let lastQuestion = '', retainedJobs = [], olderCursor = null, historyLoaded = false;
  let hasSavedSelection = false;
  try {
    const saved = sessionStorage.getItem(activeKey);
    // An empty value explicitly selects a new chat; a missing key means first visit.
    hasSavedSelection = saved === '' || (Number.isSafeInteger(Number(saved)) && Number(saved) > 0);
    chatId = hasSavedSelection ? Number(saved) || null : null;
  } catch {}
  const api = async (url, body) => {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch(url, {
        method:body === undefined ? 'GET' : 'POST', credentials:'same-origin', signal:abort.signal,
        headers:{Accept:'application/json', 'Content-Type':'application/json',
          'X-CSRFToken':$('form').querySelector('[name=csrfmiddlewaretoken]').value},
        ...(body === undefined ? {} : {body:JSON.stringify(body)}),
      });
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Обновите страницу и войдите снова.');
      const data = await response.json();
      if (!response.ok) {
        const failure = new Error(data.error || 'Не удалось загрузить диалог.');
        failure.status = response.status;
        throw failure;
      }
      return data;
    } finally { clearTimeout(timeout); }
  };
  const saveSelection = () => {
    try { sessionStorage.setItem(activeKey, String(chatId || '')); } catch {}
  };
  const chatSummaries = new Map();
  const renderChatOptions = () => {
    const select = $('conversations');
    // Native menus can jump or close if their options change while open.
    // Focus also covers keyboard interaction; apply the latest data on blur.
    if (document.activeElement === select) return;
    const options = new Map([...select.options].map(option => [option.value, option]));
    const desired = [{value:'', text:'Новый диалог'},
      ...[...chatSummaries.values()].sort((a, b) => b.id - a.id).map(chat => ({
        value:String(chat.id), text:`${chat.active ? '◌ ' : ''}${chat.title}`,
      }))];
    desired.forEach(({value, text}, index) => {
      const option = options.get(value) || element('option');
      if (!option.hasAttribute('value') || option.value !== value) option.value = value;
      if (option.textContent !== text) option.textContent = text;
      if (select.options[index] !== option) select.insertBefore(option, select.options[index] || null);
    });
    const selected = chatId ? String(chatId) : '';
    if (select.value !== selected) select.value = selected;
    $('more-chats').hidden = !nextBefore;
  };
  $('conversations').addEventListener('blur', () => requestAnimationFrame(renderChatOptions));
  const refreshChats = async (append = false) => {
    const data = await api(chatsUrl + (append && nextBefore ? `?before=${nextBefore}` : ''));
    for (const chat of data.conversations) chatSummaries.set(String(chat.id), chat);
    if (append || !listInitialized) nextBefore = data.next_before;
    listInitialized = true;
    renderChatOptions();
    return data.conversations;
  };
  const schedulePoll = () => {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => void poll(), activeJob ? 1000 : 6000);
  };
  const syncConversation = data => {
    const transcript = [];
    if (!historyLoaded) { olderCursor = data.next_before; historyLoaded = true; }
    const jobs = new Map(retainedJobs.map(job => [job.id, job]));
    for (const job of data.jobs) jobs.set(job.id, job);
    retainedJobs = [...jobs.values()];
    activeJob = null;
    for (const job of retainedJobs) {
      transcript.push({role:'user', content:job.question});
      if (['queued', 'running'].includes(job.status)) activeJob = job;
      if (job.reply || job.progress.length || ['failed', 'cancelled'].includes(job.status)) {
        transcript.push({role:'assistant', content:job.reply, progress:job.progress,
          sources:job.sources, interrupted:['failed', 'cancelled'].includes(job.status)});
      }
    }
    lastQuestion = retainedJobs.at(-1)?.question || '';
    const signature = JSON.stringify(transcript);
    if (signature !== lastTranscript) {
      const keepFollowing = following, oldTop = scroll.scrollTop;
      messages = transcript;
      render();
      following = keepFollowing;
      if (!following) scroll.scrollTop = oldTop;
      lastTranscript = signature;
    }
    const wasBusy = Boolean(controller);
    controller = activeJob ? (controller || new AbortController()) : null;
    if (wasBusy !== Boolean(controller)) busy(Boolean(controller));
    if (activeJob) activity(activeJob.activity);
    const last = retainedJobs.at(-1);
    $('error').hidden = true;
    if (last && ['failed', 'cancelled'].includes(last.status)) {
      error(last.status === 'cancelled' ? 'Запрос остановлен. Его можно повторить.' :
        'Не удалось завершить ответ. Его можно повторить.');
    }
    $('older-messages').hidden = !olderCursor;
    ready = true;
    $('send').disabled = Boolean(controller) || configured === false || submitting;
  };
  const poll = async () => {
    const ownTurn = turn, id = chatId;
    try {
      if (id) {
        const data = await api(`${chatsUrl}${id}/`);
        if (ownTurn !== turn || id !== chatId) return;
        syncConversation(data);
      }
      await refreshChats();
      if (!id) { ready = true; $('send').disabled = configured === false || submitting; }
    } catch (failure) {
      if (ownTurn !== turn) return;
      error(failure.message + ' Переподключаюсь…', false);
    } finally {
      if (ownTurn === turn) schedulePoll();
    }
  };
  const chooseChat = async id => {
    turn++;
    clearTimeout(pollTimer);
    chatId = id; saveSelection();
    ready = false; activeJob = null; controller = null;
    pendingSubmission = null; lastQuestion = ''; lastTranscript = '';
    retainedJobs = []; olderCursor = null; historyLoaded = false;
    messages = []; render(); busy(false); $('send').disabled = true;
    $('error').hidden = true;
    input.value = ''; promptIndex = null;
    if (id) await poll();
    else { ready = true; $('send').disabled = configured === false; schedulePoll(); }
  };
  const cancelCurrent = async () => {
    if (!activeJob || !chatId) return;
    try {
      await api(`${chatsUrl}${chatId}/jobs/${activeJob.id}/cancel/`, {});
      await poll();
    } catch (failure) { error(failure.message, false); }
  };
  const send = async (retry = false) => {
    if (!ready || controller || submitting || configured === false) return;
    const text = retry ? (pendingSubmission?.question || input.value.trim() || lastQuestion) : input.value.trim();
    if (!text) return;
    submitting = true;
    $('send').disabled = true;
    $('new').disabled = true;
    $('conversations').disabled = true;
    $('error').hidden = true;
    try {
      if (!chatId) {
        const chat = await api(chatsUrl, {});
        chatId = chat.id; saveSelection();
      }
      if (!retry || !pendingSubmission) {
        pendingSubmission = {request_id:crypto.randomUUID(), question:text,
          page:location.pathname, form_state:formState()};
      }
      await api(`${chatsUrl}${chatId}/send/`, pendingSubmission);
      rememberPrompt(text);
      input.value = '';
      pendingSubmission = null;
      await poll();
    } catch (failure) {
      // A transport failure may follow a committed enqueue. Retry with the same UUID.
      if (failure.status) pendingSubmission = null;
      error(failure.message || 'Нет соединения с сервером. Попробуйте ещё раз.');
    } finally {
      submitting = false;
      $('new').disabled = false;
      $('conversations').disabled = false;
      $('send').disabled = Boolean(controller) || configured === false;
    }
  };
  $('older-messages').addEventListener('click', async () => {
    if (!chatId || !olderCursor) return;
    const ownTurn = turn;
    try {
      const data = await api(`${chatsUrl}${chatId}/?before=${olderCursor}`);
      if (ownTurn !== turn) return;
      retainedJobs = [...data.jobs, ...retainedJobs];
      olderCursor = data.next_before;
      syncConversation({jobs:[]});
    } catch (failure) { error(failure.message, false); }
  });
  $('conversations').addEventListener('change', () => void chooseChat(Number($('conversations').value)));
  $('more-chats').addEventListener('click', () => {
    void refreshChats(true).catch(failure => error(failure.message, false));
  });
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
  $('cancel').addEventListener('click', () => void cancelCurrent());
  $('new').addEventListener('click', () => {
    void chooseChat(null);
    $('conversations').value = '';
    input.focus();
  });
  panel.querySelectorAll('[data-assistant-prompt]').forEach(node => node.addEventListener('click', () => { input.value = node.dataset.assistantPrompt; input.focus(); void send(); }));
  document.querySelector('.logout-form')?.addEventListener('submit', () => { try { sessionStorage.removeItem(storageKey); } catch {} });
  render();
  setWidth();

  setOpen(savedOpen === null ? desktop.matches : savedOpen === 'true', false, false);
  void loadStatus();
  void (async () => {
    const ownTurn = turn;
    try {
      const chats = await refreshChats();
      if (ownTurn !== turn) return;
      await chooseChat(hasSavedSelection ? chatId : chats[0]?.id || null);
    } catch (failure) { error(failure.message, false); schedulePoll(); }
  })();
})();
