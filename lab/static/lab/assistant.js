"use strict";
(() => {
    'use strict';
    const panel = document.getElementById('lab-assistant');
    if (!panel)
        return;
    // The server-rendered assistant include owns this fixed DOM contract.
    const $ = (id) => document.getElementById(`assistant-${id}`);
    const launcher = $('launcher'), backdrop = $('backdrop'), input = $('input');
    const messagesNode = $('messages'), scroll = $('scroll'), statusNode = $('status');
    const resize = $('resize');
    const inputHelp = $('input-help'), inputHint = $('input-hint');
    const desktop = window.matchMedia('(min-width: 1400px)');
    const defaultOpen = () => desktop.matches
        && !document.querySelector('form[data-recipe-editor]');
    const storageKey = `kudo-assistant-v1:${panel.dataset.employee}`;
    const preferenceKey = `${storageKey}:open`;
    const widthKey = `${storageKey}:width`;
    const promptsKey = `${storageKey}:prompts`;
    const historyNoticeThreshold = 10;
    let messages = [], controller = null;
    let configured = null, open = false;
    let prompts = [], promptIndex = null;
    let turn = 0, savedOpen = null;
    let preferredWidth = 374;
    let drag = null;
    let following = true, activityTimer, activityStarted = 0;
    let activitySteps = [];
    let hintPinned = false, hintTimer;
    const setInputHint = (visible) => {
        clearTimeout(hintTimer);
        inputHint.hidden = !visible;
        inputHelp.setAttribute('aria-expanded', String(visible));
        if (!visible)
            hintPinned = false;
    };
    const fitInput = () => {
        if (panel.hidden)
            return;
        input.style.height = 'auto';
        const maximum = parseFloat(getComputedStyle(input).maxHeight);
        input.style.height = `${Math.min(input.scrollHeight, maximum)}px`;
        input.style.overflowY = input.scrollHeight > maximum ? 'auto' : 'hidden';
    };
    inputHelp.parentElement?.addEventListener('pointerenter', event => {
        if (event.pointerType !== 'touch')
            setInputHint(true);
    });
    inputHelp.parentElement?.addEventListener('pointerleave', () => {
        if (!hintPinned && document.activeElement !== inputHelp) {
            hintTimer = setTimeout(() => setInputHint(false), 150);
        }
    });
    inputHelp.addEventListener('focus', () => setInputHint(true));
    inputHelp.addEventListener('blur', () => setInputHint(false));
    inputHelp.addEventListener('click', () => {
        hintPinned = !hintPinned;
        setInputHint(hintPinned);
    });
    document.addEventListener('click', event => {
        if (event.target instanceof Node
            && !inputHelp.parentElement?.contains(event.target))
            setInputHint(false);
    });
    scroll.addEventListener('scroll', () => {
        following = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    }, { passive: true });
    const followResponse = () => {
        if (following)
            scroll.scrollTop = scroll.scrollHeight;
    };
    const progressParts = (value) => Array.isArray(value)
        ? value.filter((part) => typeof part === 'string'
            && Boolean(part.trim())).slice(0, 8).map(part => part.slice(0, 12000)) : [];
    try {
        savedOpen = sessionStorage.getItem(preferenceKey);
        const savedWidth = Number(sessionStorage.getItem(widthKey));
        if (Number.isFinite(savedWidth) && savedWidth > 0)
            preferredWidth = savedWidth;
    }
    catch { /* A disabled or full browser store must not block the chat. */ }
    const loadPrompts = () => {
        try {
            const saved = JSON.parse(localStorage.getItem(promptsKey) || 'null');
            if (Array.isArray(saved))
                prompts = saved.filter((text) => typeof text === 'string' && Boolean(text.trim())
                    && text.length <= 6000).slice(-25);
        }
        catch { /* Recall also works in memory when local storage is unavailable. */ }
    };
    // Make questions from an existing conversation available on first use, too.
    prompts = messages.filter(message => message.role === 'user'
        && message.content.length <= 6000).map(message => message.content).slice(-25);
    loadPrompts();
    const rememberPrompt = (text) => {
        loadPrompts();
        prompts.push(text);
        prompts = prompts.slice(-25);
        promptIndex = null;
        try {
            localStorage.setItem(promptsKey, JSON.stringify(prompts));
        }
        catch { }
    };
    const updateHistoryNotice = () => {
        $('history-notice').hidden = messages.length < historyNoticeThreshold;
    };
    const element = (tag, className = '', text) => {
        const node = document.createElement(tag);
        if (className)
            node.className = className;
        if (text !== undefined)
            node.textContent = text;
        return node;
    };
    const safeHref = (value) => {
        if (typeof value !== 'string' || !value.startsWith('/')
            || value.startsWith('//')
            || /[\\{}<>\x00-\x20]|%7[bd]|%3[ce]/i.test(value))
            return null;
        try {
            const url = new URL(value, location.origin);
            return url.origin === location.origin
                ? url.pathname + url.search + url.hash : null;
        }
        catch {
            return null;
        }
    };
    const navigationData = JSON.parse(document.getElementById('assistant-navigation').textContent || '[]');
    const navigation = navigationData
        .map(item => ({ ...item, pattern: new RegExp(item.pattern) }));
    const routeLabel = (value) => {
        const path = value.split(/[?#]/)[0];
        return navigation.find(item => item.pattern.test(path))?.label || null;
    };
    const appendRoute = (target, value) => {
        const label = routeLabel(value);
        if (!label)
            return false;
        const href = safeHref(value);
        const node = element(href ? 'a' : 'span', '', label);
        if (href)
            node.setAttribute('href', href);
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
                if (href)
                    node.setAttribute('href', href);
                target.append(node);
            }
            else if (match[3] !== undefined) {
                const node = element('strong');
                inline(node, match[3]);
                target.append(node);
            }
            else if (match[4] !== undefined) {
                if (!appendRoute(target, match[4])) {
                    target.append(element('code', '', match[4]));
                }
            }
            else if (!appendRoute(target, match[0])) {
                target.append(document.createTextNode(match[0]));
            }
            offset = match.index + match[0].length;
        }
        target.append(document.createTextNode(text.slice(offset)));
    };
    const tableRow = (line) => {
        const source = line.trim(), cells = [];
        let cell = '', codeDelimiter = '', hasPipe = false;
        for (let index = 0; index < source.length; index++) {
            const character = source[index];
            if (character === '\\' && index + 1 < source.length) {
                const next = source[++index];
                cell += next === '|' ? '|' : '\\' + next;
            }
            else if (character === '`') {
                let ticks = '`';
                while (source[index + 1] === '`') {
                    ticks += '`';
                    index++;
                }
                if (codeDelimiter === ticks)
                    codeDelimiter = '';
                else if (!codeDelimiter
                    && source.slice(index + 1).match(/`+/g)?.includes(ticks)) {
                    codeDelimiter = ticks;
                }
                cell += ticks;
            }
            else if (character === '|' && !codeDelimiter) {
                cells.push(cell.trim());
                cell = '';
                hasPipe = true;
            }
            else
                cell += character;
        }
        cells.push(cell.trim());
        if (source.startsWith('|'))
            cells.shift();
        if (hasPipe && source.endsWith('|') && cells.at(-1) === '')
            cells.pop();
        return { cells, hasPipe };
    };
    const appendTableRow = (section, cells, columns, alignment, heading = false) => {
        const row = element('tr');
        columns.forEach((_, column) => {
            const cell = element(heading ? 'th' : 'td');
            if (heading)
                cell.scope = 'col';
            cell.style.textAlign = alignment[column];
            inline(cell, cells[column] || '');
            row.append(cell);
        });
        section.append(row);
    };
    const appendMarkdownTable = (target, lines, index) => {
        const header = tableRow(lines[index]);
        const separator = tableRow(lines[index + 1] || '');
        if (!header.hasPipe || !header.cells.length
            || header.cells.length !== separator.cells.length
            || !separator.cells.every(cell => /^:?-{3,}:?$/.test(cell)))
            return null;
        const wrapper = element('div', 'assistant-table-scroll');
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', 'Таблица в ответе ИИ-ассистента');
        const table = element('table'), head = element('thead'), body = element('tbody');
        const alignment = separator.cells.map(cell => cell.endsWith(':')
            ? (cell.startsWith(':') ? 'center' : 'right') : 'left');
        appendTableRow(head, header.cells, header.cells, alignment, true);
        index++;
        while (index + 1 < lines.length && lines[index + 1].trim()
            && !/^\s*(?:```|#{1,6}\s|(?:[-*]|\d+[.)])\s)/.test(lines[index + 1])) {
            appendTableRow(body, tableRow(lines[++index]).cells, header.cells, alignment);
        }
        table.append(head, body);
        wrapper.append(table);
        target.append(wrapper);
        return index;
    };
    const appendMarkdownText = (target, line, state) => {
        const heading = line.match(/^#{1,6}\s+(.+)/);
        if (heading) {
            const node = element('h4');
            inline(node, heading[1]);
            target.append(node);
            state.paragraph = null;
            state.list = null;
            return;
        }
        const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)/);
        if (item) {
            const kind = item[1] ? 'ul' : 'ol';
            if (!state.list || state.list.tagName !== kind.toUpperCase()) {
                state.list = element(kind);
                target.append(state.list);
            }
            const node = element('li');
            inline(node, item[2]);
            state.list.append(node);
            state.paragraph = null;
            return;
        }
        state.list = null;
        if (!state.paragraph) {
            state.paragraph = element('p');
            target.append(state.paragraph);
        }
        else
            state.paragraph.append(document.createTextNode('\n'));
        inline(state.paragraph, line);
    };
    const appendMarkdownLine = (target, lines, index, state) => {
        const line = lines[index];
        if (line.trim().startsWith('```')) {
            state.paragraph = null;
            state.list = null;
            if (state.code)
                state.code = null;
            else {
                const pre = element('pre');
                state.code = element('code');
                pre.append(state.code);
                target.append(pre);
            }
            return index;
        }
        if (state.code) {
            state.code.append(document.createTextNode((state.code.textContent ? '\n' : '') + line));
            return index;
        }
        if (!line.trim()) {
            state.paragraph = null;
            state.list = null;
            return index;
        }
        const tableEnd = appendMarkdownTable(target, lines, index);
        if (tableEnd !== null) {
            state.paragraph = null;
            state.list = null;
        }
        else
            appendMarkdownText(target, line, state);
        return tableEnd ?? index;
    };
    const markdown = (target, text) => {
        const state = { paragraph: null, list: null, code: null };
        const lines = text.replace(/\r\n?/g, '\n').split('\n');
        for (let index = 0; index < lines.length; index++) {
            index = appendMarkdownLine(target, lines, index, state);
        }
    };
    const showSources = (article, message) => {
        if (message.role === 'assistant' && Array.isArray(message.sources)
            && message.sources.length) {
            const sources = element('div', 'assistant-sources');
            sources.append(element('span', 'assistant-sources-label', 'Источники'));
            const seen = new Set([...article.querySelectorAll('a')]
                .map(node => node.getAttribute('href')));
            for (const source of message.sources.slice(0, 12)) {
                if (!source || typeof source.title !== 'string')
                    continue;
                const href = safeHref(source.url);
                if (href && seen.has(href))
                    continue;
                if (href)
                    seen.add(href);
                const node = element(href ? 'a' : 'span', href ? '' : 'assistant-source-text', source.title.slice(0, 180));
                if (href)
                    node.setAttribute('href', href);
                sources.append(node);
            }
            if (sources.children.length > 1)
                article.append(sources);
        }
    };
    const showMessage = (message) => {
        const article = element('article', `assistant-message is-${message.role}`);
        article.append(element('div', 'assistant-message-label', message.role === 'assistant' ? 'ИИ-ассистент' : 'Вы'));
        if (message.role === 'assistant') {
            for (const part of progressParts(message.progress)) {
                const stage = element('div', 'assistant-message-body assistant-message-stage');
                markdown(stage, part);
                article.append(stage);
            }
        }
        const body = element('div', 'assistant-message-body');
        if (message.role === 'assistant')
            markdown(body, message.content);
        else
            body.textContent = message.content;
        article.append(body);
        showSources(article, message);
        if (message.role === 'assistant' && message.interrupted === true) {
            article.classList.add('is-interrupted');
            article.setAttribute('aria-label', 'Прерванный ответ ИИ-ассистента');
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
        const sidebar = parseFloat(getComputedStyle(document.documentElement)
            .getPropertyValue('--sidebar')) || 244;
        const min = Math.min(340, viewport);
        // Leave enough room to use the laboratory when the panel is docked.
        const max = Math.max(min, Math.min(960, desktop.matches ? viewport - sidebar - 600 : viewport - 32));
        return { min, max, viewport, sidebar };
    };
    const setWidth = (value = preferredWidth, remember = false) => {
        const { min, max, viewport, sidebar } = widthBounds();
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
            if (open)
                savedOpen = 'true';
            try {
                sessionStorage.setItem(widthKey, String(width));
                if (open)
                    sessionStorage.setItem(preferenceKey, 'true');
            }
            catch { /* Resizing also works without storage. */ }
        }
        fitInput();
    };
    const stopResize = () => {
        if (!drag)
            return;
        const pointer = drag.pointer;
        drag = null;
        document.body.classList.remove('assistant-resizing');
        if (resize.hasPointerCapture(pointer))
            resize.releasePointerCapture(pointer);
    };
    resize.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !event.isPrimary || resize.hidden || drag)
            return;
        event.preventDefault();
        drag = { pointer: event.pointerId, x: event.clientX,
            width: panel.getBoundingClientRect().width };
        resize.setPointerCapture(event.pointerId);
        resize.focus();
        document.body.classList.add('assistant-resizing');
    });
    resize.addEventListener('pointermove', event => {
        if (drag?.pointer === event.pointerId) {
            setWidth(drag.width + drag.x - event.clientX, true);
        }
    });
    resize.addEventListener('pointerup', stopResize);
    resize.addEventListener('pointercancel', stopResize);
    resize.addEventListener('lostpointercapture', stopResize);
    resize.addEventListener('keydown', event => {
        const { min, max } = widthBounds();
        const width = panel.getBoundingClientRect().width, step = event.shiftKey ? 60 : 20;
        const nextWidths = {
            ArrowLeft: width + step, ArrowRight: width - step, Home: min, End: max,
        };
        const next = nextWidths[event.key];
        if (next === undefined)
            return;
        event.preventDefault();
        setWidth(next, true);
    });
    window.addEventListener('resize', () => { stopResize(); setWidth(); });
    const syncModal = () => {
        const modal = open && !desktop.matches;
        document.body.classList.toggle('assistant-modal-open', modal);
        backdrop.hidden = !modal;
        panel.setAttribute('role', modal ? 'dialog' : 'complementary');
        if (modal)
            panel.setAttribute('aria-modal', 'true');
        else
            panel.removeAttribute('aria-modal');
        document.querySelectorAll('.main-shell, .sidebar')
            .forEach(node => { node.inert = modal; });
    };
    const setOpen = (value, focus = false, remember = true) => {
        if (!value) {
            stopResize();
            setInputHint(false);
            setChatPicker(false);
        }
        open = value;
        panel.hidden = !value;
        launcher.setAttribute('aria-expanded', String(value));
        document.body.classList.toggle('assistant-open', value);
        syncModal();
        if (value)
            fitInput();
        if (remember) {
            savedOpen = String(value);
            try {
                sessionStorage.setItem(preferenceKey, savedOpen);
            }
            catch { }
        }
        if (focus)
            (value ? $('close') : launcher).focus();
    };
    launcher.addEventListener('click', () => setOpen(true, true));
    $('close').addEventListener('click', () => setOpen(false, true));
    backdrop.addEventListener('click', () => setOpen(false, true));
    desktop.addEventListener('change', () => {
        if (savedOpen === null)
            setOpen(defaultOpen(), false, false);
        else
            syncModal();
    });
    document.addEventListener('keydown', event => {
        if (!open)
            return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (!$('chat-menu').hidden)
                setChatPicker(false, true);
            else if (!inputHint.hidden)
                setInputHint(false);
            else if (controller)
                void cancelCurrent();
            else if (drag)
                stopResize();
            else
                setOpen(false, true);
        }
        if (event.key !== 'Tab' || desktop.matches)
            return;
        const nodes = [...panel.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea, a[href], [tabindex="0"]')].filter(node => node.getClientRects().length);
        const first = nodes[0], last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
        }
        else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
        }
    });
    const error = (text, retry = true) => {
        $('error-text').textContent = text;
        $('retry').hidden = !retry;
        $('error').hidden = false;
        scroll.scrollTop = scroll.scrollHeight;
    };
    const activity = (text) => {
        if (typeof text !== 'string' || !text.trim())
            return;
        text = text.trim().slice(0, 240);
        if (activitySteps.at(-1) === text)
            return;
        $('activity-text').textContent = text;
        activitySteps.push(text);
        activitySteps = activitySteps.slice(-32);
        $('steps-list').replaceChildren(...activitySteps
            .map(step => element('li', '', step)));
        $('steps-summary').textContent = `Действия · ${activitySteps.length}`;
        $('steps').hidden = activitySteps.length < 2;
        followResponse();
    };
    const updateElapsed = () => {
        const seconds = Math.floor((Date.now() - activityStarted) / 1000);
        $('elapsed').textContent = seconds < 60 ? `${seconds} с`
            : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`;
    };
    const busy = (value) => {
        clearInterval(activityTimer);
        activityTimer = undefined;
        if (value) {
            activityStarted = Date.now();
            activitySteps = [];
            $('steps').open = false;
            $('elapsed').textContent = '';
            activity('Думаю…');
            activityTimer = setInterval(updateElapsed, 1000);
        }
        $('pending').hidden = !value;
        $('cancel').hidden = !value;
        $('send').hidden = value;
        $('send').disabled = value || configured === false;
        $('retry').disabled = value;
        messagesNode.setAttribute('aria-busy', String(value));
        panel.querySelectorAll('[data-assistant-prompt]')
            .forEach(node => { node.disabled = value; });
        followResponse();
    };
    const showConfiguration = (data) => {
        configured = Boolean(data.configured);
        statusNode.hidden = configured;
        statusNode.replaceChildren();
        if (configured)
            return;
        statusNode.append(document.createTextNode('ИИ-ассистент ещё не подключён. Обратитесь к администратору. '));
        const button = element('button', '', 'Проверить настройки');
        button.type = 'button';
        button.addEventListener('click', loadStatus);
        statusNode.append(button);
    };
    const loadStatus = async () => {
        try {
            const response = await fetch(panel.dataset.statusUrl, {
                headers: { Accept: 'application/json' }, credentials: 'same-origin',
            });
            if (!response.ok
                || !response.headers.get('content-type')?.includes('application/json')) {
                throw new Error('status');
            }
            const data = await response.json();
            showConfiguration(data);
        }
        catch {
            statusNode.hidden = false;
            statusNode.textContent = 'Не удалось проверить подключение. '
                + 'Можно попробовать отправить вопрос или обновить страницу.';
        }
        $('send').disabled = !ready || Boolean(controller) || configured === false;
    };
    const readable = (value) => (value || '').replace(/\s+/g, ' ').trim();
    const limitSnapshot = (value, maximum, state) => {
        if (value.length > maximum)
            state.truncated = true;
        const result = value.slice(0, maximum);
        return /[\uD800-\uDBFF]$/.test(result) ? result.slice(0, -1) : result;
    };
    const privateName = /(?:^|[_-])(?:csrfmiddlewaretoken|csrf|password|passwd|secret|token|api[_-]?key)(?:$|[_-])/i;
    const fieldTypes = new Set([
        'text', 'search', 'number', 'textarea', 'select-one', 'select-multiple',
        'checkbox', 'radio', 'date', 'datetime-local', 'time', 'email', 'tel', 'url',
        'range', 'color', 'week', 'month',
    ]);
    const fieldLabel = (node) => [...(node.labels || [])].map(source => {
        const copy = source.cloneNode(true);
        copy.querySelectorAll('.required').forEach(marker => marker.remove());
        return readable(copy.textContent);
    }).filter(Boolean).join(' · ') || node.getAttribute('aria-label') || node.name;
    const hasDisabledParent = (option) => {
        const parent = option.parentElement;
        return (parent instanceof HTMLSelectElement
            || parent instanceof HTMLOptGroupElement) && parent.disabled;
    };
    const captureSelection = (node, field, state) => {
        const selected = [...node.selectedOptions];
        const limit = (value, maximum) => limitSnapshot(value, maximum, state);
        if (selected.length > 20)
            state.truncated = true;
        if (node.multiple) {
            field.value = selected.slice(0, 20).map(option => limit(option.value, 2000));
            field.display = selected.slice(0, 20)
                .map(option => limit(readable(option.textContent), 160));
            field.changed = [...node.options]
                .some(option => option.selected !== option.defaultSelected);
        }
        else {
            field.value = limit(node.value, 2000);
            field.display = limit(readable(selected[0]?.textContent || ''), 160);
            const original = [...node.options]
                .filter(option => option.defaultSelected).at(-1)
                || [...node.options].find(option => !option.disabled
                    && !hasDisabledParent(option));
            field.changed = node.value !== (original?.value || '');
        }
    };
    const captureField = (node, state) => {
        const limit = (value, maximum) => limitSnapshot(value, maximum, state);
        const name = limit(node.name, 120);
        if (privateName.test(name))
            return null;
        const field = {
            name, label: limit(readable(fieldLabel(node)), 160), type: node.type,
            value: '', changed: false,
        };
        const section = node.closest('.panel')?.querySelector('h2')?.textContent;
        if (section)
            field.section = limit(readable(section), 160);
        if (node instanceof HTMLInputElement
            && (node.type === 'checkbox' || node.type === 'radio')) {
            field.value = node.checked;
            field.display = limit(node.value, 160);
            field.changed = node.checked !== node.defaultChecked;
        }
        else if (node instanceof HTMLSelectElement) {
            captureSelection(node, field, state);
        }
        else {
            field.value = limit(node.value, 2000);
            field.changed = node.value !== node.defaultValue;
        }
        return field;
    };
    const appendFormSnapshot = (form, context) => {
        const { state } = context;
        let snapshot = null;
        for (const node of form.querySelectorAll('input, select, textarea')) {
            if (!node.name || privateName.test(node.name) || !fieldTypes.has(node.type)
                || node.matches(':disabled') || !node.getClientRects().length)
                continue;
            if (context.count === 200 || (!snapshot && state.forms.length === 3)) {
                state.truncated = true;
                return false;
            }
            if (!snapshot) {
                snapshot = { method: form.method === 'post' ? 'post' : 'get', fields: [] };
                state.forms.push(snapshot);
            }
            const field = captureField(node, state);
            if (!field)
                continue;
            snapshot.fields.push(field);
            context.count++;
            if (JSON.stringify(state).length > 24000) {
                snapshot.fields.pop();
                if (!snapshot.fields.length)
                    state.forms.pop();
                state.truncated = true;
                return false;
            }
        }
        return true;
    };
    const formState = () => {
        const state = { title: '', forms: [], truncated: false };
        state.title = limitSnapshot(readable(document.querySelector('.main-shell main h1')?.textContent || document.title), 200, state);
        const context = { state, count: 0 };
        for (const form of document.querySelectorAll('.main-shell main form')) {
            if (!appendFormSnapshot(form, context))
                break;
        }
        // Form drafts are captured only for this request, never in chat history.
        return context.count ? state : undefined;
    };
    const chatsUrl = '/assistant/conversations/';
    const activeKey = `${storageKey}:conversation`;
    let chatId = null, activeJob = null;
    let ready = false, submitting = false;
    let pollTimer, lastTranscript = '';
    let nextBefore = null, pendingSubmission = null;
    let listInitialized = false;
    let loadingChats = false;
    let lastQuestion = '', retainedJobs = [], olderCursor = null;
    let historyLoaded = false;
    let hasSavedSelection = false;
    try {
        const saved = sessionStorage.getItem(activeKey);
        // An empty value explicitly selects a new chat; a missing key means first visit.
        hasSavedSelection = saved === ''
            || (Number.isSafeInteger(Number(saved)) && Number(saved) > 0);
        chatId = hasSavedSelection ? Number(saved) || null : null;
    }
    catch { }
    const api = async (url, body) => {
        const abort = new AbortController();
        const timeout = setTimeout(() => abort.abort(), 15000);
        try {
            const response = await fetch(url, {
                method: body === undefined ? 'GET' : 'POST',
                credentials: 'same-origin', signal: abort.signal,
                headers: { Accept: 'application/json', 'Content-Type': 'application/json',
                    'X-CSRFToken': $('form').querySelector('[name=csrfmiddlewaretoken]').value },
                ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            });
            if (!response.headers.get('content-type')?.includes('application/json')) {
                throw new Error('Обновите страницу и войдите снова.');
            }
            // The endpoint determines T; failed responses use a shared error shape.
            const data = await response.json();
            if (!response.ok) {
                const failure = new Error(data.error
                    || 'Не удалось загрузить диалог.');
                failure.status = response.status;
                throw failure;
            }
            return data;
        }
        finally {
            clearTimeout(timeout);
        }
    };
    const asFailure = (value) => value instanceof Error ? value : new Error(String(value));
    const saveSelection = () => {
        try {
            sessionStorage.setItem(activeKey, String(chatId || ''));
        }
        catch { }
    };
    const chatSummaries = new Map();
    const chatRows = new Map();
    const chatGroups = new Map();
    const chatMenu = $('chat-menu'), chatSearch = $('chat-search');
    const chatList = $('chat-list'), chatTrigger = $('conversations');
    const setText = (node, text) => {
        if (node.textContent !== text)
            node.textContent = text;
    };
    const chatDate = (chat) => {
        const date = new Date(chat.updated_at);
        return Number.isNaN(date.getTime()) ? null : date;
    };
    const chatGroup = (chat) => {
        const date = chatDate(chat);
        if (!date)
            return 'Ранее';
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const yesterday = new Date(today);
        yesterday.setDate(today.getDate() - 1);
        const week = new Date(today);
        week.setDate(today.getDate() - 6);
        if (date >= today)
            return 'Сегодня';
        if (date >= yesterday)
            return 'Вчера';
        return date >= week ? 'Последние 7 дней' : 'Ранее';
    };
    const chatMeta = (chat) => {
        if (chat.active)
            return 'Готовит ответ…';
        const date = chatDate(chat);
        if (!date)
            return 'Сохранённый диалог';
        return date.toLocaleString('ru-RU', {
            day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
            ...(date.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}),
        });
    };
    const chatIcon = (path, className = '') => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', `assistant-button-icon ${className}`.trim());
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const shape = document.createElementNS(svg.namespaceURI, 'path');
        shape.setAttribute('d', path);
        svg.append(shape);
        return svg;
    };
    const createChatRow = (id) => {
        const row = element('button', 'assistant-chat-option');
        row.type = 'button';
        row.dataset.chatId = id;
        const copy = element('span', 'assistant-chat-copy');
        copy.append(element('span', 'assistant-chat-title'), element('span', 'assistant-chat-meta'));
        row.append(chatIcon('M21 15a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3Z'), copy, chatIcon('m5 12 4 4L19 6', 'assistant-chat-check'));
        row.addEventListener('click', () => {
            if (submitting)
                return;
            setChatPicker(false);
            if (chatId !== Number(id))
                void chooseChat(Number(id));
            input.focus();
        });
        chatRows.set(id, row);
        return row;
    };
    const updateChatRow = (chat) => {
        const id = String(chat.id), row = chatRows.get(id) || createChatRow(id);
        setText(row.querySelector('.assistant-chat-title'), chat.title);
        setText(row.querySelector('.assistant-chat-meta'), chatMeta(chat));
        if (row.title !== chat.title)
            row.title = chat.title;
        row.classList.toggle('is-running', chat.active);
        const selected = chat.id === chatId;
        if (selected && !row.hasAttribute('aria-current')) {
            row.setAttribute('aria-current', 'true');
        }
        else if (!selected)
            row.removeAttribute('aria-current');
        const check = row.querySelector('.assistant-chat-check');
        if (selected)
            check.removeAttribute('hidden');
        else if (!check.hasAttribute('hidden'))
            check.setAttribute('hidden', '');
        return row;
    };
    const placeChatRows = (label, rows) => {
        let group = chatGroups.get(label);
        if (!group) {
            group = element('section', 'assistant-chat-group');
            group.setAttribute('aria-label', label);
            group.append(element('h4', '', label));
            chatGroups.set(label, group);
        }
        rows.forEach((row, index) => {
            if (group.children[index + 1] !== row) {
                group.insertBefore(row, group.children[index + 1] || null);
            }
        });
        [...group.querySelectorAll('button')].forEach(row => {
            if (!rows.includes(row))
                row.remove();
        });
        return group;
    };
    const renderChatList = (chats) => {
        const groups = new Map();
        for (const chat of chats) {
            const label = chatGroup(chat), rows = groups.get(label) || [];
            rows.push(updateChatRow(chat));
            groups.set(label, rows);
        }
        const sections = [...groups].map(([label, rows]) => placeChatRows(label, rows));
        sections.forEach((section, index) => {
            if (chatList.children[index] !== section) {
                chatList.insertBefore(section, chatList.children[index] || null);
            }
        });
        [...chatList.children].forEach(section => {
            if (!sections.includes(section))
                section.remove();
        });
    };
    const normalizeChatSearch = (value) => value.trim().toLocaleLowerCase('ru-RU')
        .replace(/[\s\u001c-\u001f\u0085._-]+/g, '.');
    const renderChatPicker = (force = false) => {
        // Polling must not change a menu under the pointer or keyboard focus.
        if (!chatMenu.hidden && !force)
            return;
        const selected = chatSummaries.get(String(chatId));
        const title = selected?.title || (chatId ? 'Сохранённый диалог' : 'Новый диалог');
        setText($('current-chat'), title);
        if (chatTrigger.title !== title)
            chatTrigger.title = title;
        const id = chatId ? String(chatId) : '';
        if (chatTrigger.dataset.chatId !== id)
            chatTrigger.dataset.chatId = id;
        const query = normalizeChatSearch(chatSearch.value);
        const chats = [...chatSummaries.values()].sort((a, b) => (chatDate(b)?.getTime() || 0) - (chatDate(a)?.getTime() || 0) || b.id - a.id);
        renderChatList(chats.filter(chat => normalizeChatSearch(chat.title).includes(query)));
        $('chat-empty').hidden = Boolean(chatList.children.length);
        setText($('chat-empty-title'), query ? 'Ничего не найдено' : 'Пока нет диалогов');
        setText($('chat-empty-text'), query ? 'Попробуйте другое название.'
            : 'Начните разговор — он появится здесь.');
        setText($('chat-count'), String(chatSummaries.size));
        $('chat-search-note').hidden = !query || !nextBefore;
        $('more-chats').hidden = !nextBefore;
    };
    const setChatPicker = (visible, focus = false) => {
        if (visible && submitting)
            return;
        if (visible) {
            chatSearch.value = '';
            renderChatPicker(true);
            chatList.scrollTop = 0;
            setInputHint(false);
        }
        chatMenu.hidden = !visible;
        chatTrigger.setAttribute('aria-expanded', String(visible));
        if (!visible)
            renderChatPicker();
        if (visible)
            chatSearch.focus();
        else if (focus)
            chatTrigger.focus();
    };
    const moveChatFocus = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            setChatPicker(false, true);
            return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)
            || event.altKey || event.ctrlKey || event.metaKey || event.isComposing)
            return;
        const inSearch = event.target === chatSearch;
        if (inSearch && ['Home', 'End'].includes(event.key))
            return;
        const rows = [...chatList.querySelectorAll('button')];
        if (!rows.length) {
            if (inSearch) {
                event.preventDefault();
                $('chat-new').focus();
            }
            return;
        }
        const index = rows.indexOf(document.activeElement);
        let target = index + (event.key === 'ArrowUp' ? -1 : 1);
        if (event.key === 'Home')
            target = 0;
        if (event.key === 'End' || (inSearch && event.key === 'ArrowUp')) {
            target = rows.length - 1;
        }
        event.preventDefault();
        if (!inSearch && index === 0 && event.key === 'ArrowUp')
            chatSearch.focus();
        else
            rows[(target + rows.length) % rows.length].focus();
    };
    chatTrigger.addEventListener('click', () => setChatPicker(Boolean(chatMenu.hidden)));
    $('chat-close').addEventListener('click', () => setChatPicker(false, true));
    chatSearch.addEventListener('input', () => renderChatPicker(true));
    chatMenu.addEventListener('keydown', moveChatFocus);
    chatMenu.addEventListener('focusout', event => {
        if (event.relatedTarget === chatTrigger && !chatMenu.hidden) {
            setChatPicker(false);
        }
    });
    document.addEventListener('pointerdown', event => {
        if (event.target instanceof Node
            && !chatTrigger.parentElement?.contains(event.target) && !chatMenu.hidden) {
            setChatPicker(false);
        }
    });
    chatTrigger.parentElement?.addEventListener('focusout', event => {
        if (event.relatedTarget instanceof Node
            && !chatTrigger.parentElement?.contains(event.relatedTarget)) {
            setChatPicker(false);
        }
    });
    const refreshChats = async (append = false) => {
        const data = await api(chatsUrl
            + (append && nextBefore ? `?before=${nextBefore}` : ''));
        for (const chat of data.conversations)
            chatSummaries.set(String(chat.id), chat);
        if (append || !listInitialized)
            nextBefore = data.next_before;
        listInitialized = true;
        renderChatPicker(append === true);
        return data.conversations;
    };
    const schedulePoll = () => {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => void poll(), activeJob ? 1000 : 6000);
    };
    const buildTranscript = () => {
        const transcript = [];
        activeJob = null;
        for (const job of retainedJobs) {
            transcript.push({ role: 'user', content: job.question });
            if (['queued', 'running'].includes(job.status))
                activeJob = job;
            if (job.reply || job.progress.length
                || ['failed', 'cancelled'].includes(job.status)) {
                transcript.push({ role: 'assistant', content: job.reply, progress: job.progress,
                    sources: job.sources,
                    interrupted: ['failed', 'cancelled'].includes(job.status) });
            }
        }
        return transcript;
    };
    const syncTranscript = (transcript) => {
        const signature = JSON.stringify(transcript);
        if (signature === lastTranscript)
            return;
        const keepFollowing = following, oldTop = scroll.scrollTop;
        messages = transcript;
        render();
        following = keepFollowing;
        if (!following)
            scroll.scrollTop = oldTop;
        lastTranscript = signature;
    };
    const syncConversation = (data) => {
        if (!historyLoaded) {
            olderCursor = data.next_before ?? null;
            historyLoaded = true;
        }
        const jobs = new Map(retainedJobs.map(job => [job.id, job]));
        for (const job of data.jobs)
            jobs.set(job.id, job);
        retainedJobs = [...jobs.values()];
        const transcript = buildTranscript();
        lastQuestion = retainedJobs.at(-1)?.question || '';
        syncTranscript(transcript);
        const wasBusy = Boolean(controller);
        controller = activeJob ? (controller || new AbortController()) : null;
        if (wasBusy !== Boolean(controller))
            busy(Boolean(controller));
        const current = activeJob;
        if (current)
            activity(current.activity);
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
                if (ownTurn !== turn || id !== chatId)
                    return;
                chatSummaries.set(String(id), data);
                syncConversation(data);
            }
            await refreshChats();
            if (!id) {
                ready = true;
                $('send').disabled = configured === false || submitting;
            }
        }
        catch (caught) {
            const failure = asFailure(caught);
            if (ownTurn !== turn)
                return;
            error(failure.message + ' Переподключаюсь…', false);
        }
        finally {
            if (ownTurn === turn)
                schedulePoll();
        }
    };
    const chooseChat = async (id) => {
        turn++;
        clearTimeout(pollTimer);
        chatId = id;
        saveSelection();
        renderChatPicker();
        ready = false;
        activeJob = null;
        controller = null;
        pendingSubmission = null;
        lastQuestion = '';
        lastTranscript = '';
        retainedJobs = [];
        olderCursor = null;
        historyLoaded = false;
        messages = [];
        render();
        busy(false);
        $('send').disabled = true;
        $('error').hidden = true;
        input.value = '';
        promptIndex = null;
        fitInput();
        setInputHint(false);
        if (id)
            await poll();
        else {
            ready = true;
            $('send').disabled = configured === false;
            schedulePoll();
        }
    };
    const cancelCurrent = async () => {
        if (!activeJob || !chatId)
            return;
        try {
            await api(`${chatsUrl}${chatId}/jobs/${activeJob.id}/cancel/`, {});
            await poll();
        }
        catch (caught) {
            const failure = asFailure(caught);
            error(failure.message, false);
        }
    };
    const setSubmitting = (value) => {
        submitting = value;
        if (value)
            setChatPicker(false);
        $('new').disabled = value;
        $('conversations').disabled = value;
        $('send').disabled = value || Boolean(controller) || configured === false;
        if (value)
            $('error').hidden = true;
    };
    const requestId = () => {
        if (typeof crypto.randomUUID === 'function')
            return crypto.randomUUID();
        // randomUUID requires a secure context; getRandomValues also works over HTTP.
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
    const submitQuestion = async (text, retry) => {
        if (!chatId) {
            const chat = await api(chatsUrl, {});
            chatId = chat.id;
            saveSelection();
        }
        if (!retry || !pendingSubmission) {
            pendingSubmission = { request_id: requestId(), question: text,
                page: location.pathname, form_state: formState() };
        }
        await api(`${chatsUrl}${chatId}/send/`, pendingSubmission);
        rememberPrompt(text);
        input.value = '';
        fitInput();
        pendingSubmission = null;
        await poll();
    };
    const send = async (retry = false) => {
        if (!ready || controller || submitting || configured === false)
            return;
        const text = retry
            ? (pendingSubmission?.question || input.value.trim() || lastQuestion)
            : input.value.trim();
        if (!text)
            return;
        setSubmitting(true);
        try {
            await submitQuestion(text, retry);
        }
        catch (caught) {
            const failure = asFailure(caught);
            // A transport failure may follow a committed enqueue. Keep the same UUID.
            if (failure.status)
                pendingSubmission = null;
            error(failure.message || 'Нет соединения с сервером. Попробуйте ещё раз.');
        }
        finally {
            setSubmitting(false);
        }
    };
    $('older-messages').addEventListener('click', async () => {
        if (!chatId || !olderCursor)
            return;
        const ownTurn = turn;
        try {
            const data = await api(`${chatsUrl}${chatId}/?before=${olderCursor}`);
            if (ownTurn !== turn)
                return;
            retainedJobs = [...data.jobs, ...retainedJobs];
            olderCursor = data.next_before ?? null;
            syncConversation({ jobs: [] });
        }
        catch (caught) {
            error(asFailure(caught).message, false);
        }
    });
    $('more-chats').addEventListener('click', async () => {
        if (loadingChats)
            return;
        loadingChats = true;
        const more = $('more-chats');
        const hadFocus = document.activeElement === more;
        more.setAttribute('aria-disabled', 'true');
        try {
            const chats = await refreshChats(true);
            const lostFocus = hadFocus && document.activeElement === document.body;
            if (!chatMenu.hidden && more.hidden
                && (document.activeElement === more || lostFocus)) {
                const first = chats.map(chat => chatRows.get(String(chat.id)))
                    .find(row => row?.isConnected);
                (first || chatSearch).focus();
            }
        }
        catch (failure) {
            error(asFailure(failure).message, false);
        }
        finally {
            loadingChats = false;
            more.removeAttribute('aria-disabled');
        }
    });
    $('form').addEventListener('submit', event => {
        event.preventDefault();
        void send();
    });
    input.addEventListener('input', () => { promptIndex = null; fitInput(); });
    input.addEventListener('keydown', event => {
        if (event.isComposing)
            return;
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void send();
            return;
        }
        if (!['ArrowUp', 'ArrowDown'].includes(event.key) || event.altKey
            || event.ctrlKey || event.metaKey || event.shiftKey
            || input.readOnly || controller)
            return;
        if (input.selectionStart !== input.selectionEnd)
            return;
        if (promptIndex === null) {
            if (input.value !== '' || event.key !== 'ArrowUp')
                return;
            loadPrompts();
            if (!prompts.length)
                return;
            promptIndex = prompts.length;
        }
        event.preventDefault();
        promptIndex = Math.max(0, Math.min(prompts.length, promptIndex + (event.key === 'ArrowUp' ? -1 : 1)));
        input.value = prompts[promptIndex] || '';
        fitInput();
        if (promptIndex === prompts.length)
            promptIndex = null;
        input.setSelectionRange(input.value.length, input.value.length);
    });
    $('retry').addEventListener('click', () => { void send(true); });
    $('cancel').addEventListener('click', () => void cancelCurrent());
    const startNewChat = () => {
        if (submitting)
            return;
        setChatPicker(false);
        void chooseChat(null);
        input.focus();
    };
    $('new').addEventListener('click', startNewChat);
    $('chat-new').addEventListener('click', startNewChat);
    panel.querySelectorAll('[data-assistant-prompt]')
        .forEach(node => node.addEventListener('click', () => {
        input.value = node.dataset.assistantPrompt || '';
        fitInput();
        input.focus();
        void send();
    }));
    document.querySelector('.logout-form')?.addEventListener('submit', () => {
        try {
            sessionStorage.removeItem(storageKey);
        }
        catch { }
    });
    render();
    setWidth();
    setOpen(savedOpen === null ? defaultOpen() : savedOpen === 'true', false, false);
    void loadStatus();
    void (async () => {
        const ownTurn = turn;
        try {
            const chats = await refreshChats();
            if (ownTurn !== turn)
                return;
            await chooseChat(hasSavedSelection ? chatId : chats[0]?.id || null);
        }
        catch (caught) {
            error(asFailure(caught).message, false);
            schedulePoll();
        }
    })();
})();
