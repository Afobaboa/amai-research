"use strict";
{
    const tabletViewport = window.matchMedia('(min-width: 641px) and (max-width: 1100px)');
    function updateDensity() {
        if (tabletViewport.matches)
            document.body.dataset.density = 'touch';
        else
            delete document.body.dataset.density;
    }
    tabletViewport.addEventListener('change', updateDensity);
    updateDensity();
    document.addEventListener('keydown', event => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.type !== 'number')
            return;
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
        }
    });
    const menuButton = document.querySelector('.mobile-menu');
    const sidebar = document.getElementById('sidebar');
    const mobileViewport = window.matchMedia('(max-width: 800px)');
    function retainSidebarScroll() {
        const navigation = sidebar?.querySelector('nav');
        if (!navigation)
            return;
        const storageKey = 'lims.sidebar.scrollTop';
        function save() {
            try {
                sessionStorage.setItem(storageKey, String(navigation.scrollTop));
            }
            catch { }
        }
        function restore() {
            try {
                const saved = Number(sessionStorage.getItem(storageKey));
                if (Number.isFinite(saved) && saved >= 0)
                    navigation.scrollTop = saved;
            }
            catch { }
        }
        navigation.addEventListener('scroll', save, { passive: true });
        navigation.addEventListener('click', save);
        window.addEventListener('pagehide', save);
        window.addEventListener('pageshow', event => {
            if (event.persisted)
                restore();
        });
    }
    retainSidebarScroll();
    function setMenuOpen(open, restoreFocus = false) {
        document.body.classList.toggle('menu-open', open);
        menuButton?.setAttribute('aria-expanded', String(open));
        menuButton?.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
        if (open) {
            const activeLink = sidebar?.querySelector('.nav-link.active');
            const firstLink = sidebar?.querySelector('.nav-link');
            (activeLink ?? firstLink)?.focus({ preventScroll: true });
        }
        else if (restoreFocus) {
            menuButton?.focus();
        }
    }
    menuButton?.addEventListener('click', () => {
        const open = document.body.classList.contains('menu-open');
        setMenuOpen(!open, open);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && document.body.classList.contains('menu-open')) {
            event.preventDefault();
            setMenuOpen(false, true);
        }
    });
    document.addEventListener('click', event => {
        if (!(event.target instanceof Node))
            return;
        if (!document.body.classList.contains('menu-open'))
            return;
        if (sidebar?.contains(event.target) || menuButton?.contains(event.target))
            return;
        setMenuOpen(false, true);
    });
    document.addEventListener('focusin', event => {
        if (!(event.target instanceof Node))
            return;
        if (!document.body.classList.contains('menu-open'))
            return;
        if (sidebar?.contains(event.target) || menuButton?.contains(event.target))
            return;
        setMenuOpen(false);
    });
    mobileViewport.addEventListener('change', event => {
        if (!event.matches)
            setMenuOpen(false);
    });
    function replaceFormPrefix(fragment, count) {
        fragment.querySelectorAll('*').forEach(element => {
            for (const attribute of Array.from(element.attributes)) {
                if (attribute.value.includes('__prefix__')) {
                    element.setAttribute(attribute.name, attribute.value.replaceAll('__prefix__', String(count)));
                }
            }
        });
    }
    function addForm(button) {
        const group = document.getElementById(button.dataset.addForm ?? '');
        const template = group?.querySelector('template');
        const total = group?.querySelector('input[name$="-TOTAL_FORMS"]');
        const rows = group?.querySelector('.formset-rows');
        if (!template || !total || !rows)
            return;
        const count = Number(total.value);
        const fragment = template.content.cloneNode(true);
        replaceFormPrefix(fragment, count);
        const firstInput = fragment.querySelector('input:not([type="hidden"]):not([name$="-DELETE"]), select, textarea');
        rows.appendChild(fragment);
        total.value = String(count + 1);
        refreshComponentLots();
        firstInput?.focus();
        refreshSourceChoices();
        group?.closest('form')?.dispatchEvent(new Event('formsetchange'));
    }
    function removeForm(button) {
        const group = button.closest('[data-formset-prefix]');
        const row = button.closest('.formset-row');
        const rows = group?.querySelector('.formset-rows');
        const total = group?.querySelector('input[name$="-TOTAL_FORMS"]');
        const initial = group?.querySelector('input[name$="-INITIAL_FORMS"]');
        if (!group || !row || !rows || !total || !initial)
            return;
        const index = Array.from(rows.children).indexOf(row);
        if (index < 0)
            return;
        row.remove();
        if (index < Number(initial.value))
            initial.value = String(Number(initial.value) - 1);
        // These plain formsets save the submitted list. Keep its indexes contiguous
        // so removed rows and their validation errors cannot return on submit.
        const remaining = rows.querySelectorAll('.formset-row');
        const attributes = ['name', 'id', 'for', 'aria-describedby', 'aria-labelledby', 'aria-controls'];
        remaining.forEach((item, position) => {
            const oldPrefix = item.dataset.formPrefix;
            const newPrefix = `${group.dataset.formsetPrefix}-${position}`;
            if (oldPrefix && oldPrefix !== newPrefix) {
                item.querySelectorAll('*').forEach(element => {
                    for (const name of attributes) {
                        const value = element.getAttribute(name);
                        if (value?.includes(`${oldPrefix}-`)) {
                            element.setAttribute(name, value.replaceAll(`${oldPrefix}-`, `${newPrefix}-`));
                        }
                    }
                });
            }
            item.dataset.formPrefix = newPrefix;
        });
        total.value = String(remaining.length);
        const focusRow = remaining[index] ?? remaining[index - 1];
        const focus = focusRow?.querySelector('[data-remove-form]')
            ?? group.querySelector('[data-add-form]');
        focus?.focus();
        group.closest('form')?.dispatchEvent(new Event('formsetchange'));
    }
    document.addEventListener('click', event => {
        if (!(event.target instanceof Element))
            return;
        const button = event.target.closest('[data-add-form]');
        if (button)
            addForm(button);
        const removeButton = event.target.closest('[data-remove-form]');
        if (removeButton)
            removeForm(removeButton);
    });
    const sourceChoiceCache = new Map();
    const sourceChoiceRequests = new WeakMap();
    function isSourceCellChoices(data) {
        if (typeof data !== 'object' || data === null || !('cells' in data))
            return false;
        if (!Array.isArray(data.cells))
            return false;
        return data.cells.every((cell) => (typeof cell === 'object' && cell !== null
            && 'label' in cell && typeof cell.label === 'string'
            && 'id' in cell && ['number', 'string'].includes(typeof cell.id)));
    }
    async function fetchSourceChoices(sheet) {
        const response = await fetch(`/source-cell-choices/?sheet=${encodeURIComponent(sheet)}`);
        if (!response.ok)
            throw new Error('Не удалось получить ячейки источника');
        const data = await response.json();
        if (!isSourceCellChoices(data))
            throw new Error('Неверный ответ источника');
        return data;
    }
    function cachedSourceChoices(sheet) {
        let pending = sourceChoiceCache.get(sheet);
        if (!pending) {
            pending = fetchSourceChoices(sheet);
            sourceChoiceCache.set(sheet, pending);
        }
        return pending;
    }
    function sourceOptions(data, select, preserveSelected) {
        const selected = select.value;
        const selectedLabel = select.selectedOptions[0]?.textContent;
        const options = [new Option('---------', ''), ...data.cells.map(cell => (new Option(cell.label, String(cell.id), false, preserveSelected && String(cell.id) === selected)))];
        if (preserveSelected && selected && selectedLabel
            && !data.cells.some(cell => String(cell.id) === selected)) {
            options.push(new Option(selectedLabel, selected, true, true));
        }
        return options;
    }
    async function loadSourceChoices(select, sheet, preserveSelected) {
        const request = Symbol();
        sourceChoiceRequests.set(select, request);
        select.dataset.loading = String(Boolean(sheet));
        delete select.dataset.error;
        if (!preserveSelected)
            select.replaceChildren(new Option('---------', ''));
        try {
            const data = sheet ? await cachedSourceChoices(sheet) : { cells: [] };
            if (sourceChoiceRequests.get(select) !== request)
                return;
            select.replaceChildren(...sourceOptions(data, select, preserveSelected));
        }
        catch {
            sourceChoiceCache.delete(sheet);
            if (sourceChoiceRequests.get(select) === request) {
                select.dataset.error = 'Не удалось загрузить ячейки. Выберите лист ещё раз.';
            }
        }
        finally {
            if (sourceChoiceRequests.get(select) === request) {
                select.dataset.loading = 'false';
            }
        }
    }
    function refreshSourceChoices(preserveSelected = true) {
        const sheet = document.querySelector('[name="source_sheet"]');
        document.querySelectorAll('select[data-source-cell]')
            .forEach(select => {
            const sourceSheet = sheet ? sheet.value : select.dataset.sourceSheet ?? '';
            void loadSourceChoices(select, sourceSheet, preserveSelected);
        });
    }
    document.querySelector('[name="source_sheet"]')
        ?.addEventListener('change', () => refreshSourceChoices(false));
    refreshSourceChoices(true);
    function filterComponentLots(lots) {
        const material = lots.closest('.formset-row')
            ?.querySelector('select[name$="-material"]');
        if (!material)
            return;
        const previous = lots.value;
        for (const option of lots.options) {
            if (!option.value)
                continue;
            const available = !!material.value
                && option.dataset.materialId === material.value;
            option.hidden = !available;
            option.disabled = !available;
            if (!available && option.selected)
                lots.value = '';
        }
        lots.dataset.error = material.value ? '' : 'Сначала выберите компонент.';
        if (previous !== lots.value) {
            lots.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    function refreshComponentLots() {
        document.querySelectorAll('select[data-component-lot]')
            .forEach(filterComponentLots);
    }
    document.addEventListener('change', event => {
        if (!(event.target instanceof HTMLSelectElement)
            || !event.target.name.endsWith('-material'))
            return;
        const lots = event.target.closest('.formset-row')
            ?.querySelector('select[data-component-lot]');
        if (lots)
            filterComponentLots(lots);
    });
    window.addEventListener('pageshow', refreshComponentLots);
    const draftPrefix = 'lims.form-draft.v1:';
    const pendingDraftKey = 'lims.form-draft.pending';
    function draftControls(form) {
        return Array.from(form.elements).filter((field) => ((field instanceof HTMLInputElement || field instanceof HTMLSelectElement
            || field instanceof HTMLTextAreaElement)
            && !!field.name && !field.disabled && field.name !== 'csrfmiddlewaretoken'
            && !['password', 'file', 'submit', 'reset', 'button'].includes(field.type)));
    }
    function draftFields(form, defaults = false) {
        return draftControls(form).map(control => {
            let value;
            if (control instanceof HTMLSelectElement) {
                let options = Array.from(control.options).filter(option => (defaults ? option.defaultSelected : option.selected));
                if (defaults && !control.multiple && !options.length && control.options[0]) {
                    options = [control.options[0]];
                }
                value = control.multiple ? options.map(option => option.value) : options[0]?.value ?? '';
            }
            else {
                value = defaults ? control.dataset.serverValue ?? control.defaultValue : control.value;
            }
            if (defaults && control instanceof HTMLInputElement) {
                // Firefox restores hidden management values on reload, including their
                // defaultValue, without restoring dynamic rows. Compare the draft to
                // the counts actually rendered by Django, not that browser state.
                const group = control.closest('[data-formset-prefix]');
                if (control.name.endsWith('-TOTAL_FORMS'))
                    value = group?.dataset.serverTotal ?? value;
                if (control.name.endsWith('-INITIAL_FORMS'))
                    value = group?.dataset.serverInitial ?? value;
            }
            const field = { name: control.name, value };
            if (control instanceof HTMLInputElement) {
                if (['checkbox', 'radio'].includes(control.type)) {
                    field.checked = defaults ? control.defaultChecked : control.checked;
                }
                if (control.dataset.datePicker) {
                    const original = control.dataset.serverValue ?? control.dataset.dateInitialValue ?? '';
                    if (defaults)
                        field.value = original;
                    field.display = defaults ? (original ? original.split('-').reverse().join('.') : '__.__.____')
                        : control.parentElement?.querySelector('input[type="text"]')?.value;
                }
            }
            return field;
        });
    }
    function readFormDraft(key) {
        try {
            const draft = JSON.parse(sessionStorage.getItem(key) || 'null');
            if (draft && typeof draft.initial === 'string' && typeof draft.rendered === 'string'
                && Array.isArray(draft.fields) && Array.isArray(draft.counts)
                && draft.fields.every(field => field && typeof field.name === 'string'
                    && (typeof field.value === 'string' || (Array.isArray(field.value)
                        && field.value.every(value => typeof value === 'string')))
                    && (field.checked === undefined || typeof field.checked === 'boolean')
                    && (field.display === undefined || typeof field.display === 'string'))
                && draft.counts.every(pair => Array.isArray(pair) && typeof pair[0] === 'string'
                    && Number.isInteger(pair[1]) && pair[1] >= 0 && pair[1] <= 2000))
                return draft;
        }
        catch { /* A missing or damaged draft must not prevent editing. */ }
        return null;
    }
    function restoreFormDraft(form, groups, draft) {
        for (const group of groups) {
            const count = draft.counts.find(([prefix]) => prefix === group.dataset.formsetPrefix)?.[1];
            const rows = group.querySelector('.formset-rows');
            const template = group.querySelector('template');
            if (count === undefined || !rows || !template)
                continue;
            while (rows.children.length > count)
                rows.lastElementChild?.remove();
            while (rows.children.length < count) {
                const fragment = template.content.cloneNode(true);
                replaceFormPrefix(fragment, rows.children.length);
                rows.append(fragment);
            }
        }
        for (const control of draftControls(form)) {
            const field = draft.fields.find(field => field.name === control.name
                && (field.checked === undefined || field.value === control.value));
            if (!field)
                continue;
            if (control instanceof HTMLSelectElement && Array.isArray(field.value)) {
                const values = field.value;
                Array.from(control.options).forEach(option => { option.selected = values.includes(option.value); });
            }
            else if (typeof field.value === 'string') {
                control.value = field.value;
            }
            if (control instanceof HTMLInputElement && field.checked !== undefined) {
                control.checked = field.checked;
            }
            if (control instanceof HTMLInputElement && control.dataset.datePicker) {
                control.dispatchEvent(new CustomEvent('formdraftrestore', { detail: field.display }));
            }
            control.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    function retainFormDrafts() {
        let pending = null;
        try {
            pending = sessionStorage.getItem(pendingDraftKey);
            if (pending && document.querySelector('.message.form-draft-saved')) {
                sessionStorage.removeItem(pending);
                sessionStorage.removeItem(pendingDraftKey);
                pending = null;
            }
        }
        catch { /* Editing also works when browser storage is unavailable. */ }
        document.querySelectorAll('main form').forEach((form, index) => {
            if (form.hasAttribute('data-recipe-readonly'))
                return;
            const groups = Array.from(form.querySelectorAll('[data-formset-prefix]'));
            if (!groups.length && !form.hasAttribute('data-form-draft'))
                return;
            const key = draftPrefix + JSON.stringify([
                groups[0]?.dataset.draftOwner ?? form.dataset.draftOwner, location.pathname, location.search, index,
            ]);
            const baseline = JSON.stringify(draftFields(form, true));
            const draft = readFormDraft(key);
            const submitted = pending === key && (form.dataset.draftBound === 'true'
                || groups.some(group => group.dataset.draftBound === 'true'));
            const compatible = draft && (draft.initial === baseline || draft.rendered === baseline);
            if (compatible && !submitted)
                restoreFormDraft(form, groups, draft);
            const initial = draft && (compatible || submitted) ? draft.initial : baseline;
            let discarded = false;
            const clear = () => {
                try {
                    sessionStorage.removeItem(key);
                }
                catch { /* Optional storage. */ }
            };
            if (draft && !compatible && !submitted) {
                clear();
                // Firefox may already have restored old field values. When the server
                // record changed, clearing storage alone would leave those values shown.
                restoreFormDraft(form, groups, {
                    initial: baseline, rendered: baseline,
                    fields: JSON.parse(baseline),
                    counts: groups.map(group => [
                        group.dataset.formsetPrefix,
                        Number(group.dataset.serverTotal ?? group.querySelector('.formset-rows')?.children.length ?? 0),
                    ]),
                });
            }
            const save = () => {
                if (discarded)
                    return;
                const fields = draftFields(form);
                if (JSON.stringify(fields) === initial) {
                    clear();
                    return;
                }
                const counts = groups.map(group => [
                    group.dataset.formsetPrefix,
                    group.querySelector('.formset-rows')?.children.length ?? 0,
                ]);
                try {
                    sessionStorage.setItem(key, JSON.stringify({ initial, rendered: baseline, fields, counts }));
                }
                catch { /* Storage failure must not break normal form submission. */ }
            };
            form.addEventListener('input', save);
            form.addEventListener('change', save);
            form.addEventListener('formsetchange', save);
            window.addEventListener('pagehide', save);
            form.addEventListener('submit', () => {
                save();
                try {
                    sessionStorage.setItem(pendingDraftKey, key);
                }
                catch { /* Optional storage. */ }
            });
            form.querySelectorAll('.form-actions a').forEach(link => {
                link.addEventListener('click', () => { discarded = true; clear(); });
            });
            if (submitted) {
                save();
                try {
                    sessionStorage.removeItem(pendingDraftKey);
                }
                catch { /* Optional storage. */ }
            }
        });
    }
    retainFormDrafts();
    refreshComponentLots();
    function toggleMeasurementReasons() {
        document.querySelectorAll('form[data-measurement-values]')
            .forEach(form => {
            form.querySelectorAll('[data-measurement-value]')
                .forEach(value => {
                const reason = form.elements.namedItem(`${value.name}_reason`);
                if (!(reason instanceof HTMLInputElement))
                    return;
                const field = reason.closest('.field');
                const update = () => {
                    const filled = value.value.trim() !== '';
                    if (field)
                        field.hidden = filled;
                };
                value.addEventListener('input', update);
                value.addEventListener('change', update);
                update();
            });
            form.addEventListener('formdata', event => {
                form.querySelectorAll('.field[hidden] input')
                    .forEach(reason => event.formData.delete(reason.name));
            });
        });
    }
    toggleMeasurementReasons();
}
