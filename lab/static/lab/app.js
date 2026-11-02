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
    function setMenuOpen(open, restoreFocus = false) {
        document.body.classList.toggle('menu-open', open);
        menuButton?.setAttribute('aria-expanded', String(open));
        menuButton?.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
        if (open) {
            const activeLink = sidebar?.querySelector('.nav-link.active');
            const firstLink = sidebar?.querySelector('.nav-link');
            (activeLink ?? firstLink)?.focus();
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
        const firstInput = fragment.querySelector('input:not([type="hidden"]), select, textarea');
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
    document.querySelectorAll('[data-filter-form] select')
        .forEach(select => select.addEventListener('change', () => {
        select.form?.requestSubmit();
    }));
    const sourceChoiceCache = new Map();
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
    async function loadSourceChoices(select, sheet) {
        const selected = select.value;
        if (!sheet) {
            select.replaceChildren(new Option('---------', ''));
            return;
        }
        try {
            const data = await cachedSourceChoices(sheet);
            const options = [new Option('---------', ''), ...data.cells.map(cell => (new Option(cell.label, String(cell.id), false, String(cell.id) === selected)))];
            select.replaceChildren(...options);
        }
        catch {
            sourceChoiceCache.delete(sheet);
        }
    }
    function refreshSourceChoices() {
        const sheet = document.querySelector('[name="source_sheet"]')
            ?.value;
        document.querySelectorAll('select[data-source-cell]')
            .forEach(select => {
            const sourceSheet = sheet || select.dataset.sourceSheet;
            if (sourceSheet)
                void loadSourceChoices(select, sourceSheet);
        });
    }
    document.querySelector('[name="source_sheet"]')
        ?.addEventListener('change', refreshSourceChoices);
    refreshSourceChoices();
}
