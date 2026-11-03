"use strict";
{
    const form = document.querySelector('form[data-recipe-editor]');
    const numberFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 });
    let previewController = null;
    let previewTimer = 0;
    let revision = 0;
    function record(value) {
        return typeof value === 'object' && value !== null;
    }
    function readScript(id) {
        const source = document.getElementById(id)?.textContent;
        if (!source)
            return {};
        try {
            return JSON.parse(source);
        }
        catch {
            return {};
        }
    }
    const catalog = readScript('recipe-catalog-data');
    function pathValue(source, path) {
        let value = source;
        for (const key of path.split('.')) {
            if (!record(value))
                return undefined;
            value = value[key];
        }
        return value;
    }
    function cell(value) {
        if (record(value) && 'value' in value) {
            const raw = value.value;
            return {
                value: typeof raw === 'string' || typeof raw === 'number' ? raw : null,
                reason: typeof value.reason === 'string' ? value.reason : '',
            };
        }
        return {
            value: typeof value === 'string' || typeof value === 'number' ? value : null,
            reason: '',
        };
    }
    function formatted(value, unit = '') {
        if (value.value === null || value.value === '')
            return '—';
        const number = Number(value.value);
        const label = Number.isFinite(number) ? numberFormat.format(number) : String(value.value);
        return unit ? `${label} ${unit}` : label;
    }
    function currencyLabel(value) {
        const currencies = { EUR: '€', RUB: '₽', USD: '$' };
        return typeof value === 'string' ? currencies[value] ?? value : '';
    }
    function renderOutputs(root, source) {
        root.querySelectorAll('[data-recipe-output]').forEach(output => {
            const value = cell(pathValue(source, output.dataset.recipeOutput ?? ''));
            output.value = formatted(value, output.dataset.recipeUnit);
            output.title = value.reason ?? '';
        });
        root.querySelectorAll('[data-recipe-reason]').forEach(reason => {
            const value = cell(pathValue(source, reason.dataset.recipeReason ?? ''));
            reason.textContent = value.reason ?? '';
            reason.hidden = !value.reason;
        });
        root.querySelectorAll('[data-recipe-currency]').forEach(label => {
            label.textContent = currencyLabel(pathValue(source, label.dataset.recipeCurrency ?? ''));
        });
    }
    function rowSource(source, path, row, index) {
        const rows = pathValue(source, path);
        if (!Array.isArray(rows))
            return undefined;
        const rowId = row.querySelector('input[name$="-row_id"]')?.value;
        return rows.find((item) => (rowId && record(item) && String(item.row_id ?? item.id ?? '') === rowId)) ?? rows[index];
    }
    function renderRow(row, source) {
        row.querySelectorAll('[data-recipe-row-output]').forEach(output => {
            const value = cell(pathValue(source, output.dataset.recipeRowOutput ?? ''));
            output.value = formatted(value);
            output.title = value.reason ?? '';
        });
        row.querySelectorAll('[data-recipe-row-reason]').forEach(reason => {
            const value = cell(pathValue(source, reason.dataset.recipeRowReason ?? ''));
            reason.textContent = value.reason ?? '';
            reason.hidden = !value.reason;
        });
        row.querySelectorAll('[data-recipe-row-currency]').forEach(label => {
            label.textContent = currencyLabel(pathValue(source, 'price_currency'));
        });
    }
    function renderRows(source) {
        if (!form)
            return;
        form.querySelectorAll('[data-recipe-rows]').forEach(group => {
            const path = group.dataset.recipeRows === 'polyol' ? 'polyol.rows' : 'filling.gases';
            const rows = group.querySelectorAll('.formset-rows > .formset-row');
            rows.forEach((row, index) => {
                if (!rowUsed(row)) {
                    renderRow(row, undefined);
                    return;
                }
                const filling = rowSource(source, path, row, index);
                const gas = rowSource(source, 'gases.rows', row, index);
                renderRow(row, path === 'filling.gases' && record(filling) && record(gas)
                    ? { ...gas, ...filling } : filling);
            });
        });
        const iso = form.querySelector('[data-recipe-row-kind="isocyanate"]');
        if (iso)
            renderRow(iso, pathValue(source, 'filling.isocyanate'));
    }
    function control(name) {
        const field = form?.elements.namedItem(name);
        return field instanceof HTMLInputElement || field instanceof HTMLSelectElement
            || field instanceof HTMLTextAreaElement ? field : null;
    }
    function computedInput(value) {
        if (value.value === null)
            return '';
        const number = Number(value.value);
        return Number.isFinite(number)
            ? number.toFixed(8).replace(/\.?0+$/, '') : String(value.value);
    }
    function fieldInRow(row, name) {
        return row.querySelector(`[name$="-${name}"]`);
    }
    function updateDerivedRatio(source) {
        const mode = control('settings-iso_ratio_mode')?.value ?? 'parts';
        const parts = control('settings-isocyanate_parts');
        const index = control('settings-iso_index');
        if (parts instanceof HTMLInputElement)
            parts.readOnly = mode === 'index';
        if (index instanceof HTMLInputElement)
            index.readOnly = mode !== 'index';
        const field = mode === 'index' ? parts : index;
        const path = mode === 'index' ? 'isocyanate.parts' : 'isocyanate.iso_index';
        const value = cell(pathValue(source, path));
        if (field)
            field.value = computedInput(value);
        form?.querySelectorAll('[data-recipe-ratio-parts], [data-recipe-ratio-index]')
            .forEach(wrapper => {
            const derived = wrapper.hasAttribute(mode === 'index'
                ? 'data-recipe-ratio-parts' : 'data-recipe-ratio-index');
            wrapper.querySelectorAll('.recipe-reason-field')
                .forEach(reason => {
                const input = reason.querySelector('input, textarea');
                const value = input ? pairedValue(input) : null;
                reason.hidden = derived || Boolean(value?.value.trim());
            });
        });
    }
    function renderCalculation(source) {
        if (!form)
            return;
        renderOutputs(form, source);
        renderRows(source);
        updateDerivedRatio(source);
        renderIssues({}, source);
    }
    function setStatus(message, state = '') {
        const status = form?.querySelector('[data-recipe-preview-status]');
        if (status) {
            status.textContent = message;
            status.dataset.state = state;
        }
        form?.setAttribute('aria-busy', String(state === 'loading'));
    }
    function errorLabel(path) {
        const section = path[0];
        const list = section === 'polyol' || section === 'gases';
        const name = list ? path.slice(0, 3).join('-') : path.slice(0, 2).join('-');
        const field = control(name);
        const label = field?.labels?.[0]?.textContent?.replaceAll('*', '').trim();
        if (!label)
            return '';
        return list ? `${section === 'polyol' ? 'Компонент' : 'Газ'} ${Number(path[1]) + 1}, ${label}` : label;
    }
    function errorMessages(value, path = []) {
        if (typeof value === 'string') {
            const label = errorLabel(path);
            return [label ? `${label}: ${value}` : value];
        }
        if (Array.isArray(value))
            return value.flatMap((item, index) => errorMessages(item, [...path, String(index)]));
        if (record(value)) {
            if (typeof value.message === 'string')
                return [value.message];
            return Object.entries(value).flatMap(([key, item]) => errorMessages(item, [...path, key]));
        }
        return [];
    }
    function renderIssues(errors, calculation) {
        const panel = form?.querySelector('[data-recipe-preview-issues]');
        if (!panel)
            return;
        const messages = [...new Set([
                ...errorMessages(errors),
                ...errorMessages(record(calculation) ? calculation.issues : []),
            ])];
        panel.replaceChildren();
        panel.hidden = messages.length === 0;
        if (!messages.length)
            return;
        const list = document.createElement('ul');
        for (const message of messages) {
            const item = document.createElement('li');
            item.textContent = message;
            list.append(item);
        }
        panel.append(list);
    }
    function previewResponse(value) {
        return record(value) && 'calculation' in value;
    }
    async function refreshPreview(requestRevision) {
        if (!form?.dataset.previewUrl)
            return;
        previewController?.abort();
        previewController = new AbortController();
        setStatus('Пересчитываем по введённым параметрам…', 'loading');
        try {
            const response = await fetch(form.dataset.previewUrl, {
                method: 'POST', body: new FormData(form), credentials: 'same-origin',
                headers: { 'X-CSRFToken': control('csrfmiddlewaretoken')?.value ?? '' },
                signal: previewController.signal,
            });
            if (!response.ok)
                throw new Error('Не удалось обновить расчёт.');
            const result = await response.json();
            if (!previewResponse(result))
                throw new Error('Неверный ответ расчёта.');
            if (requestRevision !== revision)
                return;
            renderCalculation(result.calculation);
            renderIssues(result.errors, result.calculation);
            setStatus('Расчёт обновлён. При сохранении сервер проверит его повторно.');
        }
        catch (error) {
            if (error instanceof DOMException && error.name === 'AbortError')
                return;
            if (requestRevision !== revision)
                return;
            setStatus('Расчёт не обновлён. Проверьте соединение и измените параметр повторно.', 'error');
        }
    }
    function queuePreview() {
        revision += 1;
        window.clearTimeout(previewTimer);
        previewController?.abort();
        setStatus('Исходные данные изменены. Обновляем расчёт…', 'loading');
        previewTimer = window.setTimeout(() => { void refreshPreview(revision); }, 300);
    }
    function pairedValue(reason) {
        let name = reason.name.replace(/_reason$/, '');
        if (reason.name.endsWith('-lot_reason'))
            name = reason.name.replace(/-lot_reason$/, '-specified_lot');
        return control(name);
    }
    function rowUsed(row) {
        const identity = fieldInRow(row, 'row_id');
        if (identity?.value)
            return true;
        return Array.from(row.querySelectorAll('input, select, textarea'))
            .some(field => {
            if (field instanceof HTMLInputElement && ['hidden', 'checkbox'].includes(field.type))
                return false;
            if (field.name.endsWith('-price_currency'))
                return false;
            return field.value.trim() !== '';
        });
    }
    function synchronizeReasons() {
        form?.querySelectorAll('.recipe-ingredient').forEach(row => {
            row.querySelectorAll(':scope > details, :scope > .recipe-row-results')
                .forEach(details => { details.hidden = !rowUsed(row); });
        });
        form?.querySelectorAll('[name$="_reason"]').forEach(reason => {
            const value = pairedValue(reason);
            const wrapper = reason.closest('.field');
            if (!value || !wrapper)
                return;
            wrapper.classList.add('recipe-reason-field');
            const row = reason.closest('.recipe-ingredient');
            wrapper.hidden = value.value.trim() !== '';
            if (row && !rowUsed(row))
                wrapper.hidden = true;
            if (reason.name.endsWith('-ohv_reason') && row) {
                const applicable = fieldInRow(row, 'ohv_applicable');
                if (applicable instanceof HTMLInputElement && !applicable.checked)
                    wrapper.hidden = true;
            }
            if (value instanceof HTMLInputElement && value.readOnly)
                wrapper.hidden = true;
        });
    }
    function synchronizeIso() {
        const checkbox = control('settings-isocyanate_present');
        const present = checkbox instanceof HTMLInputElement ? checkbox.checked : true;
        const fields = form?.querySelector('[data-recipe-iso-fields]');
        const reason = form?.querySelector('[data-recipe-iso-absence]');
        if (fields)
            fields.hidden = !present;
        if (reason)
            reason.hidden = present;
    }
    function catalogEntry(lot) {
        if (!record(catalog))
            return null;
        const entry = catalog[lot];
        return record(entry) && ('material_id' in entry) ? entry : null;
    }
    function sameNumber(left, right) {
        return left === right || (left !== '' && right !== '' && Number(left) === Number(right));
    }
    function applyCatalogProperty(row, name, raw, replace) {
        const field = fieldInRow(row, name);
        if (!field)
            return;
        if (!replace && field.dataset.sourceValue !== undefined) {
            if (field instanceof HTMLInputElement)
                field.readOnly = true;
            field.title = 'Историческое значение из исходной рецептуры';
            return;
        }
        const value = typeof raw === 'string' || typeof raw === 'number' ? String(raw) : '';
        const known = value !== '';
        if (replace || field.value === '')
            field.value = value;
        const fromCatalog = known && sameNumber(field.value, value);
        if (field instanceof HTMLInputElement)
            field.readOnly = fromCatalog;
        field.dataset.catalogValue = fromCatalog ? value : '';
        field.title = fromCatalog ? 'Значение из выбранной партии' : '';
    }
    function applyCatalog(row, replace = false) {
        const lot = fieldInRow(row, 'specified_lot')?.value ?? '';
        const entry = catalogEntry(lot);
        const material = fieldInRow(row, 'material')?.value;
        const matching = entry && String(entry.material_id) === material ? entry : null;
        applyCatalogProperty(row, 'density_g_ml', matching?.density_g_ml, replace);
        applyCatalogProperty(row, 'price_per_kg', matching?.price_per_kg, replace);
        const currency = fieldInRow(row, 'price_currency');
        if (currency && matching?.price_currency
            && (replace || !currency.value || fieldInRow(row, 'price_per_kg')?.dataset.catalogValue)) {
            currency.value = matching.price_currency;
        }
    }
    function clearProperties(row) {
        for (const name of ['ohv', 'nco_pct', 'density_g_ml', 'price_per_kg', 'pressure_bar', 'functionality']) {
            const field = fieldInRow(row, name);
            if (field) {
                field.value = '';
                if (field instanceof HTMLInputElement)
                    field.readOnly = false;
                delete field.dataset.catalogValue;
                delete field.dataset.sourceValue;
            }
            const reason = fieldInRow(row, `${name}_reason`);
            if (reason)
                reason.value = '';
        }
    }
    function changedSelection(target) {
        if (!(target instanceof HTMLSelectElement))
            return;
        const materialChanged = target.name.endsWith('-material');
        if (!materialChanged && !target.name.endsWith('-specified_lot'))
            return;
        const row = target.closest('.recipe-ingredient');
        if (!row)
            return;
        if (materialChanged) {
            const lot = fieldInRow(row, 'specified_lot');
            if (lot)
                lot.value = '';
            const applicable = fieldInRow(row, 'ohv_applicable');
            if (applicable instanceof HTMLInputElement)
                applicable.checked = true;
        }
        clearProperties(row);
        applyCatalog(row, true);
        const details = row.querySelector('details');
        if (details)
            details.open = true;
    }
    function normalizeFormData(data) {
        form?.querySelectorAll('.recipe-reason-field[hidden] input, .recipe-reason-field[hidden] textarea')
            .forEach(reason => { data.delete(reason.name); });
        const present = control('settings-isocyanate_present');
        if (present instanceof HTMLInputElement && present.checked) {
            data.delete('settings-isocyanate_absence_reason');
        }
    }
    if (form) {
        form.querySelectorAll('.recipe-ingredient').forEach(row => applyCatalog(row));
        synchronizeReasons();
        synchronizeIso();
        renderCalculation(readScript('recipe-calculation-data'));
        form.addEventListener('input', () => { synchronizeReasons(); queuePreview(); });
        form.addEventListener('change', event => {
            changedSelection(event.target);
            synchronizeReasons();
            synchronizeIso();
            queuePreview();
        });
        form.addEventListener('formsetchange', () => {
            synchronizeReasons();
            queuePreview();
        });
        form.addEventListener('formdata', event => normalizeFormData(event.formData));
        window.addEventListener('pagehide', () => {
            previewController?.abort();
            window.clearTimeout(previewTimer);
        });
        queuePreview();
    }
}
