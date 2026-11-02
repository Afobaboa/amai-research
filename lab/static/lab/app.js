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
        firstInput?.focus();
        refreshSourceChoices();
    }
    document.addEventListener('click', event => {
        if (!(event.target instanceof Element))
            return;
        const button = event.target.closest('[data-add-form]');
        if (button)
            addForm(button);
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
}
