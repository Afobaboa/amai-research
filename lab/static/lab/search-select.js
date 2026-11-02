"use strict";
{
    let activeSelect = null;
    let sequence = 0;
    function normalizeSearch(value) {
        return value.trim().toLocaleLowerCase('ru-RU')
            .replace(/[\s\u001c-\u001f\u0085._-]+/g, '.');
    }
    function element(tag, className) {
        const node = document.createElement(tag);
        node.className = className;
        return node;
    }
    function icon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('search-select-icon', `search-select-icon-${name}`);
        svg.setAttribute('viewBox', '0 0 20 20');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const paths = {
            chevron: 'm6 8 4 4 4-4',
            search: 'M14 14l3 3M15 9a6 6 0 1 1-12 0 6 6 0 0 1 12 0',
            check: 'm5 10 3.5 3.5L15 7',
        };
        path.setAttribute('d', paths[name]);
        svg.append(path);
        return svg;
    }
    class SearchSelect {
        select;
        wrapper = element('div', 'search-select');
        trigger = element('button', 'search-select-trigger');
        value = element('span', 'search-select-value');
        popup = element('div', 'search-select-popover');
        search = element('div', 'search-select-search');
        input = element('input', 'search-select-input');
        list = element('div', 'search-select-options');
        status = element('div', 'search-select-empty');
        clear = element('button', 'search-select-clear');
        footer = element('div', 'search-select-footer');
        validation = element('small', 'search-select-validation');
        id = `search-select-${++sequence}`;
        items = [];
        activeIndex = -1;
        labelName = '';
        constructor(select) {
            this.select = select;
            const hadFocus = document.activeElement === select;
            this.setupTrigger();
            this.setupPopup();
            this.bindEvents();
            this.sync();
            new MutationObserver(() => this.sync()).observe(select, {
                childList: true, subtree: true, attributes: true, characterData: true,
            });
            if (hadFocus)
                this.trigger.focus();
        }
        setupTrigger() {
            this.select.before(this.wrapper);
            this.wrapper.append(this.select, this.trigger);
            this.select.classList.add('search-select-native');
            this.select.tabIndex = -1;
            this.select.setAttribute('aria-hidden', 'true');
            this.trigger.type = 'button';
            this.trigger.id = this.id;
            this.trigger.setAttribute('aria-haspopup', 'listbox');
            this.trigger.setAttribute('aria-expanded', 'false');
            this.trigger.setAttribute('aria-controls', `${this.id}-list`);
            this.value.id = `${this.id}-value`;
            this.trigger.append(this.value, icon('chevron'));
            this.setupLabel();
        }
        setupLabel() {
            const label = this.select.labels?.[0];
            const name = label?.textContent?.replace('*', '').trim()
                || this.select.getAttribute('aria-label') || 'Выбор значения';
            this.labelName = name;
            if (label) {
                label.id ||= `${this.id}-label`;
                label.htmlFor = this.trigger.id;
                this.trigger.setAttribute('aria-labelledby', `${label.id} ${this.value.id}`);
            }
            else {
                this.trigger.setAttribute('aria-label', name);
            }
            this.input.setAttribute('aria-label', `Поиск: ${name}`);
            this.list.setAttribute('aria-label', name);
            this.validation.id = `${this.id}-error`;
            this.validation.setAttribute('role', 'alert');
            this.validation.hidden = true;
            this.wrapper.append(this.validation);
        }
        setupPopup() {
            this.popup.hidden = true;
            this.setupSearch();
            this.list.id = `${this.id}-list`;
            this.list.setAttribute('role', 'listbox');
            this.status.setAttribute('role', 'status');
            this.clear.type = 'button';
            this.clear.textContent = 'Сбросить выбор';
            const hint = element('span', 'search-select-hint');
            hint.textContent = '↑↓ выбор  ·  ↵ подтвердить';
            hint.setAttribute('aria-hidden', 'true');
            this.footer.append(this.clear, hint);
            this.popup.append(this.search, this.list, this.status, this.footer);
            this.wrapper.append(this.popup);
        }
        setupSearch() {
            this.input.type = 'search';
            this.input.autocomplete = 'off';
            this.input.placeholder = 'Найти в списке…';
            this.input.setAttribute('role', 'combobox');
            this.input.setAttribute('aria-autocomplete', 'list');
            this.input.setAttribute('aria-expanded', 'false');
            this.input.setAttribute('aria-controls', `${this.id}-list`);
            this.search.append(icon('search'), this.input);
        }
        bindEvents() {
            this.trigger.addEventListener('click', () => {
                if (activeSelect === this)
                    this.close();
                else
                    this.open();
            });
            this.trigger.addEventListener('keydown', event => this.triggerKey(event));
            this.input.addEventListener('input', () => this.render());
            this.input.addEventListener('keydown', event => this.inputKey(event));
            this.clear.addEventListener('click', () => this.choose(''));
            this.clear.addEventListener('keydown', event => this.clearKey(event));
            this.select.addEventListener('change', () => {
                this.validation.hidden = true;
                this.sync();
            });
            this.select.addEventListener('focus', () => this.trigger.focus());
            this.select.addEventListener('invalid', event => this.showInvalid(event));
            this.select.form?.addEventListener('reset', () => {
                setTimeout(() => this.sync(), 0);
            });
        }
        showInvalid(event) {
            event.preventDefault();
            this.validation.textContent = this.select.validationMessage;
            this.validation.hidden = false;
            this.sync();
            const firstInvalid = this.select.form?.querySelector('input:invalid, select:invalid, textarea:invalid');
            if (firstInvalid === this.select)
                this.trigger.focus();
        }
        sync() {
            const selected = this.select.selectedOptions[0];
            const label = selected ? this.optionLabel(selected) : 'Выберите значение';
            this.value.textContent = label;
            if (!this.trigger.hasAttribute('aria-labelledby')) {
                this.trigger.setAttribute('aria-label', `${this.labelName}: ${label}`);
            }
            this.trigger.classList.toggle('is-placeholder', !this.select.value);
            this.trigger.disabled = this.select.disabled;
            this.trigger.setAttribute('aria-busy', this.select.dataset.loading || 'false');
            this.syncValidation();
            if (activeSelect === this && !this.trigger.disabled)
                this.render();
            if (this.trigger.disabled)
                this.close();
        }
        syncValidation() {
            const invalid = this.select.getAttribute('aria-invalid') === 'true'
                || !this.validation.hidden;
            this.validation.classList.toggle('field-error', !this.validation.hidden);
            this.trigger.setAttribute('aria-invalid', String(invalid));
            this.input.setAttribute('aria-invalid', String(invalid));
            this.input.setAttribute('aria-required', String(this.select.required));
            const describedBy = [this.select.getAttribute('aria-describedby'),
                this.validation.hidden ? '' : this.validation.id].filter(Boolean).join(' ');
            for (const target of [this.trigger, this.input]) {
                if (describedBy)
                    target.setAttribute('aria-describedby', describedBy);
                else
                    target.removeAttribute('aria-describedby');
            }
        }
        optionLabel(option) {
            return /^-+$/.test(option.label) ? 'Выберите…' : option.label;
        }
        open(query = '') {
            if (this.trigger.disabled)
                return;
            activeSelect?.close();
            activeSelect = this;
            this.popup.hidden = false;
            this.trigger.setAttribute('aria-expanded', 'true');
            this.input.setAttribute('aria-expanded', 'true');
            this.input.value = query;
            this.render();
            this.position();
            this.input.focus({ preventScroll: true });
        }
        close(restoreFocus = false) {
            this.popup.hidden = true;
            this.trigger.setAttribute('aria-expanded', 'false');
            this.input.setAttribute('aria-expanded', 'false');
            this.input.removeAttribute('aria-activedescendant');
            if (activeSelect === this)
                activeSelect = null;
            if (restoreFocus)
                this.trigger.focus({ preventScroll: true });
        }
        contains(target) {
            return this.wrapper.contains(target) || this.popup.contains(target);
        }
        position() {
            const rect = this.trigger.getBoundingClientRect();
            const viewport = window.visualViewport;
            const width = viewport?.width ?? window.innerWidth;
            const height = viewport?.height ?? window.innerHeight;
            const offsetTop = viewport?.offsetTop ?? 0;
            const offsetLeft = viewport?.offsetLeft ?? 0;
            const below = height + offsetTop - rect.bottom - 12;
            const above = rect.top - offsetTop - 12;
            const upwards = below < 220 && above > below;
            this.popup.style.width = `${Math.min(Math.max(rect.width, 300), width - 24)}px`;
            this.popup.style.maxHeight = `${Math.max(100, upwards ? above : below)}px`;
            this.popup.style.left = `${Math.max(offsetLeft + 12, Math.min(rect.left, offsetLeft + width - this.popup.offsetWidth - 12))}px`;
            this.popup.style.top = `${upwards
                ? rect.top - this.popup.offsetHeight - 6 : rect.bottom + 6}px`;
        }
        render() {
            const query = normalizeSearch(this.input.value);
            const options = Array.from(this.select.options).filter(option => (option.value && !option.hidden && !option.disabled
                && !option.parentElement?.hasAttribute('disabled')
                && normalizeSearch(this.optionLabel(option)).includes(query)));
            this.items = options.map(option => this.renderOption(option));
            this.list.replaceChildren(...this.items);
            this.status.textContent = this.select.dataset.loading === 'true'
                ? 'Загружаем варианты…' : this.select.dataset.error
                || (query ? 'Ничего не найдено' : 'Нет доступных вариантов');
            this.status.hidden = this.items.length > 0 && !this.select.dataset.error
                && this.select.dataset.loading !== 'true';
            this.clear.hidden = this.select.required || !this.select.value
                || !Array.from(this.select.options).some(option => option.value === '');
            this.footer.classList.toggle('has-clear', !this.clear.hidden);
            const selected = options.findIndex(option => option.selected);
            this.highlight(selected >= 0 ? selected : 0);
            if (activeSelect === this)
                this.position();
        }
        renderOption(option) {
            const item = element('button', 'search-select-option');
            item.type = 'button';
            item.tabIndex = -1;
            item.id = `${this.id}-option-${option.index}`;
            const label = element('span', 'search-select-option-label');
            label.textContent = this.optionLabel(option);
            item.append(label, icon('check'));
            item.setAttribute('role', 'option');
            item.setAttribute('aria-label', label.textContent);
            item.setAttribute('aria-selected', String(option.selected));
            const group = option.parentElement;
            if (group instanceof HTMLOptGroupElement) {
                item.setAttribute('aria-label', `${group.label}: ${label.textContent}`);
            }
            item.addEventListener('mousedown', event => event.preventDefault());
            item.addEventListener('click', () => this.choose(option.value));
            return item;
        }
        highlight(index) {
            this.items[this.activeIndex]?.classList.remove('is-active');
            this.activeIndex = this.items.length
                ? Math.max(0, Math.min(index, this.items.length - 1)) : -1;
            const item = this.items[this.activeIndex];
            if (!item) {
                this.input.removeAttribute('aria-activedescendant');
                return;
            }
            item.classList.add('is-active');
            this.input.setAttribute('aria-activedescendant', item.id);
            item.scrollIntoView({ block: 'nearest' });
        }
        choose(value) {
            this.select.value = value;
            this.close(true);
            this.select.dispatchEvent(new Event('input', { bubbles: true }));
            this.select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        triggerKey(event) {
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                event.preventDefault();
                this.open();
                if (event.key === 'End' || event.key === 'ArrowUp') {
                    this.highlight(this.items.length - 1);
                }
            }
            else if (event.key.length === 1 && event.key !== ' '
                && !event.ctrlKey && !event.metaKey && !event.altKey) {
                event.preventDefault();
                this.open(event.key);
            }
        }
        inputKey(event) {
            if (event.ctrlKey || event.metaKey || event.altKey)
                return;
            if (this.input.value && ['Home', 'End'].includes(event.key))
                return;
            const movements = {
                ArrowDown: this.activeIndex + 1, ArrowUp: this.activeIndex - 1,
                Home: 0, End: this.items.length - 1,
            };
            if (event.key in movements && !event.isComposing) {
                event.preventDefault();
                this.highlight(movements[event.key]);
            }
            else if (event.key === 'Enter' && !event.isComposing) {
                event.preventDefault();
                this.items[this.activeIndex]?.click();
            }
            else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.close(true);
            }
            else if (event.key === 'Tab') {
                this.tabFromSearch(event);
            }
        }
        tabFromSearch(event) {
            if (!event.shiftKey && !this.clear.hidden) {
                event.preventDefault();
                this.clear.focus();
            }
            else
                this.close(true);
        }
        clearKey(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.close(true);
            }
            else if (event.key === 'Tab' && event.shiftKey) {
                event.preventDefault();
                this.input.focus();
            }
            else if (event.key === 'Tab')
                this.close(true);
        }
    }
    function enhanceSelects(root) {
        root.querySelectorAll('select:not([multiple])').forEach(select => {
            if (!select.closest('.search-select'))
                new SearchSelect(select);
        });
    }
    function initializeSelects() {
        const ready = getComputedStyle(document.body)
            .getPropertyValue('--lims-search-select-ready').trim();
        if (ready !== '1')
            return;
        enhanceSelects(document);
        new MutationObserver(records => {
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (!(node instanceof HTMLElement))
                        continue;
                    if (node instanceof HTMLSelectElement && !node.closest('.search-select')) {
                        new SearchSelect(node);
                    }
                    else
                        enhanceSelects(node);
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
    }
    initializeSelects();
    document.addEventListener('pointerdown', event => {
        if (event.target instanceof Node && !activeSelect?.contains(event.target)) {
            activeSelect?.close();
        }
    });
    document.addEventListener('focusin', event => {
        if (event.target instanceof Node && !activeSelect?.contains(event.target)) {
            activeSelect?.close();
        }
    });
    window.addEventListener('resize', () => activeSelect?.position());
    window.addEventListener('scroll', () => activeSelect?.position(), true);
    window.visualViewport?.addEventListener('resize', () => activeSelect?.position());
    window.visualViewport?.addEventListener('scroll', () => activeSelect?.position());
}
