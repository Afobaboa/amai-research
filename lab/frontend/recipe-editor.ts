{
  type RecordValue = Record<string, unknown>;
  type InputControl = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  interface NumericCell { value: string | number | null; reason?: string; }
  interface PreviewResponse { calculation: unknown; errors?: unknown; }
  interface CatalogEntry {
    material_id: number | string;
    density_g_ml?: string | number | null;
    price_per_kg?: string | number | null;
    price_currency?: string;
  }

  const form = document.querySelector<HTMLFormElement>('form[data-recipe-editor]');
  const readonly = form?.hasAttribute('data-recipe-readonly') ?? false;
  const numberFormat = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 6});
  let previewController: AbortController | null = null;
  let previewTimer = 0;
  let revision = 0;
  let activeInspector: HTMLDetailsElement | null = null;
  const numberFormats = new Map<number, Intl.NumberFormat>([[6, numberFormat]]);
  const popupEscapeEvents = new WeakSet<KeyboardEvent>();

  function record(value: unknown): value is RecordValue {
    return typeof value === 'object' && value !== null;
  }

  function readScript(id: string): unknown {
    const source = document.getElementById(id)?.textContent;
    if (!source) return {};
    try { return JSON.parse(source) as unknown; } catch { return {}; }
  }

  const catalog = readScript('recipe-catalog-data');

  function pathValue(source: unknown, path: string): unknown {
    let value = source;
    for (const key of path.split('.')) {
      if (!record(value)) return undefined;
      value = value[key];
    }
    return value;
  }

  function cell(value: unknown): NumericCell {
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

  function formatted(value: NumericCell, unit = '', precision = 6): string {
    if (value.value === null || value.value === '') return '—';
    const number = Number(value.value);
    if (!numberFormats.has(precision)) {
      numberFormats.set(precision, new Intl.NumberFormat('ru-RU', {
        maximumFractionDigits: precision,
      }));
    }
    const label = Number.isFinite(number)
      ? numberFormats.get(precision)!.format(number) : String(value.value);
    return unit ? `${label} ${unit}` : label;
  }

  function renderNumber(output: HTMLOutputElement, value: NumericCell): void {
    const requested = Number(output.dataset.recipePrecision ?? '6');
    const precision = Number.isInteger(requested) && requested >= 0
      && requested <= 20 ? requested : 6;
    const raw = value.value === null ? '' : String(value.value);
    const unit = output.dataset.recipeUnit ?? '';
    output.value = formatted(value, unit, precision);
    output.dataset.recipeRawValue = raw;
    const complete = raw ? [raw.replace('.', ','), unit].filter(Boolean).join(' ') : '';
    output.title = [complete, value.reason].filter(Boolean).join(' · ');
  }

  function currencyLabel(value: unknown): string {
    const currencies: Record<string, string> = {EUR: '€', RUB: '₽', USD: '$'};
    return typeof value === 'string' ? currencies[value] ?? value : '';
  }

  function renderOutputs(root: ParentNode, source: unknown): void {
    root.querySelectorAll<HTMLOutputElement>('[data-recipe-output]').forEach(output => {
      const value = cell(pathValue(source, output.dataset.recipeOutput ?? ''));
      renderNumber(output, value);
    });
    root.querySelectorAll<HTMLElement>('[data-recipe-reason]').forEach(reason => {
      const value = cell(pathValue(source, reason.dataset.recipeReason ?? ''));
      reason.textContent = value.reason ?? '';
      reason.hidden = !value.reason;
    });
    root.querySelectorAll<HTMLElement>('[data-recipe-currency]').forEach(label => {
      label.textContent = currencyLabel(pathValue(source, label.dataset.recipeCurrency ?? ''));
    });
  }

  function rowSource(source: unknown, path: string, row: HTMLElement, index: number): unknown {
    const rows = pathValue(source, path);
    if (!Array.isArray(rows)) return undefined;
    const rowId = row.querySelector<HTMLInputElement>('input[name$="-row_id"]')?.value;
    return rows.find((item: unknown) => (
      rowId && record(item) && String(item.row_id ?? item.id ?? '') === rowId
    )) ?? rows[index];
  }

  function renderRow(row: HTMLElement, source: unknown): void {
    row.querySelectorAll<HTMLOutputElement>('[data-recipe-row-output]').forEach(output => {
      const value = cell(pathValue(source, output.dataset.recipeRowOutput ?? ''));
      renderNumber(output, value);
    });
    row.querySelectorAll<HTMLElement>('[data-recipe-row-reason]').forEach(reason => {
      const value = cell(pathValue(source, reason.dataset.recipeRowReason ?? ''));
      reason.textContent = value.reason ?? '';
      reason.hidden = !value.reason;
    });
    row.querySelectorAll<HTMLElement>('[data-recipe-row-currency]').forEach(label => {
      label.textContent = currencyLabel(pathValue(source, 'price_currency'));
    });
  }

  function renderRows(source: unknown): void {
    if (!form) return;
    form.querySelectorAll<HTMLElement>('[data-recipe-rows]').forEach(group => {
      const path = group.dataset.recipeRows === 'polyol' ? 'polyol.rows' : 'filling.gases';
      const rows = group.querySelectorAll<HTMLElement>('.formset-rows > .formset-row');
      let calculationIndex = 0;
      rows.forEach(row => {
        if (!rowUsed(row)) { renderRow(row, undefined); return; }
        const filling = rowSource(source, path, row, calculationIndex);
        const gas = rowSource(source, 'gases.rows', row, calculationIndex);
        calculationIndex += 1;
        renderRow(row, path === 'filling.gases' && record(filling) && record(gas)
          ? {...gas, ...filling} : filling);
      });
    });
    const iso = form.querySelector<HTMLElement>('[data-recipe-row-kind="isocyanate"]');
    if (iso) renderRow(iso, pathValue(source, 'filling.isocyanate'));
  }

  function fillingOutput(row: HTMLTableRowElement, path: string,
    precision: number, unit = ''): void {
    const column = row.insertCell();
    column.className = 'recipe-filling-number';
    const output = document.createElement('output');
    output.dataset.recipeOutput = path;
    output.dataset.recipePrecision = String(precision);
    if (unit) output.dataset.recipeUnit = unit;
    column.append(output);
  }

  function fillingRow(source: RecordValue, index: number): HTMLTableRowElement {
    const row = document.createElement('tr');
    const kind = index === 0 ? 'A' : index === 1 ? 'B' : 'G';
    row.className = 'recipe-filling-row';
    row.dataset.recipeFillingKind = kind;
    const mark = document.createElement('span');
    mark.className = 'recipe-filling-mark';
    mark.textContent = kind;
    row.insertCell().append(mark);
    const name = document.createElement('th');
    name.scope = 'row';
    row.append(name);
    name.className = 'recipe-filling-name';
    name.textContent = typeof source.name === 'string' && source.name
      ? source.name : index === 0 ? 'Полиольная смесь A'
        : index === 1 ? 'Изоцианат' : 'Газ / газовая смесь';
    name.title = name.textContent;
    const path = index === 0 ? 'filling.polyol' : index === 1
      ? 'filling.isocyanate' : `filling.gases.${index - 2}`;
    fillingOutput(row, `${path}.mass_g`, 1);
    fillingOutput(row, `${path}.volume_ml`, 1);
    fillingOutput(row, `${path}.cost`, 3, currencyLabel(source.price_currency));
    return row;
  }

  function renderFilling(source: unknown): void {
    const body = form?.querySelector<HTMLTableSectionElement>(
      'tbody[data-recipe-filling-rows]',
    );
    if (!body) return;
    const rows = pathValue(source, 'filling.rows');
    body.replaceChildren();
    if (!Array.isArray(rows)) return;
    rows.forEach((row: unknown, index: number) => {
      if (record(row)) body.append(fillingRow(row, index));
    });
  }

  function renderBalance(source: unknown): void {
    const rows = pathValue(source, 'polyol.rows');
    form?.querySelectorAll<HTMLElement>('[data-recipe-component-count]')
      .forEach(count => { count.textContent = String(Array.isArray(rows) ? rows.length : 0); });
    const total = cell(pathValue(source, 'polyol.quantity_total'));
    const number = total.value === null ? NaN : Number(total.value);
    const balanced = Number.isFinite(number) && Math.abs(number - 100) <= .001;
    form?.querySelectorAll<HTMLElement>('[data-recipe-balance]').forEach(balance => {
      balance.dataset.state = balanced ? 'balanced' : 'warning';
      balance.textContent = `${formatted(total, '', 3)} % / 100 %`;
      balance.title = total.reason ?? '';
    });
  }

  function control(name: string): InputControl | null {
    const field = form?.elements.namedItem(name);
    return field instanceof HTMLInputElement || field instanceof HTMLSelectElement
      || field instanceof HTMLTextAreaElement ? field : null;
  }

  function computedInput(value: NumericCell): string {
    if (value.value === null) return '';
    const number = Number(value.value);
    return Number.isFinite(number)
      ? number.toFixed(8).replace(/\.?0+$/, '') : String(value.value);
  }

  function fieldInRow(row: HTMLElement, name: string): InputControl | null {
    return row.querySelector<InputControl>(`[name$="-${name}"]`);
  }

  function setDerivedInput(field: InputControl | null, value: NumericCell): void {
    if (!field) return;
    field.value = computedInput(value);
    const raw = value.value === null ? '' : String(value.value);
    field.dataset.recipeRawValue = raw;
    field.title = [raw.replace('.', ','), value.reason].filter(Boolean).join(' · ');
  }

  function updateDerivedRatio(source: unknown): void {
    const mode = control('settings-iso_ratio_mode')?.value ?? 'parts';
    const parts = control('settings-isocyanate_parts');
    const index = control('settings-iso_index');
    if (parts instanceof HTMLInputElement) parts.readOnly = mode === 'index';
    if (index instanceof HTMLInputElement) index.readOnly = mode !== 'index';
    const field = mode === 'index' ? parts : index;
    const path = mode === 'index' ? 'isocyanate.parts' : 'isocyanate.iso_index';
    const value = cell(pathValue(source, path));
    setDerivedInput(field, value);
    const editable = mode === 'index' ? index : parts;
    if (editable) { editable.title = ''; delete editable.dataset.recipeRawValue; }
    form?.querySelectorAll<HTMLElement>('[data-recipe-ratio-parts], [data-recipe-ratio-index]')
      .forEach(wrapper => {
        const derived = wrapper.hasAttribute(mode === 'index'
          ? 'data-recipe-ratio-parts' : 'data-recipe-ratio-index');
        wrapper.querySelectorAll<HTMLElement>('.recipe-reason-field')
          .forEach(reason => {
            const input = reason.querySelector<InputControl>('input, textarea');
            const value = input ? pairedValue(input) : null;
            reason.hidden = derived || Boolean(value?.value.trim());
          });
      });
  }

  function renderCalculation(source: unknown): void {
    if (!form) return;
    renderFilling(source);
    renderOutputs(form, source);
    renderRows(source);
    renderBalance(source);
    updateSelectionTitles();
    updateDerivedRatio(source);
    renderIssues({}, source);
  }

  function setStatus(message: string, state = ''): void {
    const status = form?.querySelector<HTMLElement>('[data-recipe-preview-status]');
    if (status) {
      status.textContent = message;
      status.dataset.state = state;
    }
    form?.setAttribute('aria-busy', String(state === 'loading'));
  }

  function errorLabel(path: string[]): string {
    const section = path[0];
    const list = section === 'polyol' || section === 'gases';
    const name = list ? path.slice(0, 3).join('-') : path.slice(0, 2).join('-');
    const field = control(name);
    const label = field?.labels?.[0]?.textContent?.replaceAll('*', '').trim();
    if (!label) return '';
    return list ? `${section === 'polyol' ? 'Компонент' : 'Газ'} ${Number(path[1]) + 1}, ${label}` : label;
  }

  function errorMessages(value: unknown, path: string[] = []): string[] {
    if (typeof value === 'string') {
      const label = errorLabel(path);
      return [label ? `${label}: ${value}` : value];
    }
    if (Array.isArray(value)) return value.flatMap((item: unknown, index: number) => errorMessages(item, [...path, String(index)]));
    if (record(value)) {
      if (typeof value.message === 'string') return [value.message];
      return Object.entries(value).flatMap(([key, item]) => errorMessages(item, [...path, key]));
    }
    return [];
  }

  function renderIssues(errors: unknown, calculation?: unknown): void {
    const panel = form?.querySelector<HTMLElement>('[data-recipe-preview-issues]');
    if (!panel) return;
    const messages = [...new Set([
      ...errorMessages(errors),
      ...errorMessages(record(calculation) ? calculation.issues : []),
    ])];
    panel.replaceChildren();
    panel.hidden = messages.length === 0;
    const wrapper = form?.querySelector<HTMLElement>('[data-recipe-issues-wrapper]');
    const count = form?.querySelector<HTMLElement>('[data-recipe-issue-count]');
    if (wrapper) wrapper.hidden = messages.length === 0;
    if (count) count.textContent = `(${messages.length})`;
    if (!messages.length) return;
    const list = document.createElement('ul');
    for (const message of messages) {
      const item = document.createElement('li');
      item.textContent = message;
      list.append(item);
    }
    panel.append(list);
  }

  function previewResponse(value: unknown): value is PreviewResponse {
    return record(value) && 'calculation' in value;
  }

  async function refreshPreview(requestRevision: number): Promise<void> {
    if (readonly || !form?.dataset.previewUrl) return;
    previewController?.abort();
    previewController = new AbortController();
    setStatus('Пересчитываем по введённым параметрам…', 'loading');
    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'X-CSRFToken': control('csrfmiddlewaretoken')?.value ?? ''},
        signal: previewController.signal,
      });
      if (!response.ok) throw new Error('Не удалось обновить расчёт.');
      const result: unknown = await response.json();
      if (!previewResponse(result)) throw new Error('Неверный ответ расчёта.');
      if (requestRevision !== revision) return;
      renderCalculation(result.calculation);
      renderIssues(result.errors, result.calculation);
      setStatus('Расчёт обновлён. При сохранении сервер проверит его повторно.');
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (requestRevision !== revision) return;
      setStatus('Расчёт не обновлён. Проверьте соединение и измените параметр повторно.', 'error');
    }
  }

  function queuePreview(): void {
    revision += 1;
    window.clearTimeout(previewTimer);
    previewController?.abort();
    setStatus('Исходные данные изменены. Обновляем расчёт…', 'loading');
    previewTimer = window.setTimeout(() => { void refreshPreview(revision); }, 300);
  }

  function pairedValue(reason: InputControl): InputControl | null {
    let name = reason.name.replace(/_reason$/, '');
    if (reason.name.endsWith('-lot_reason')) name = reason.name.replace(/-lot_reason$/, '-specified_lot');
    return control(name);
  }

  function rowUsed(row: HTMLElement): boolean {
    if (row.dataset.recipeRowKind === 'isocyanate') {
      const present = control('settings-isocyanate_present');
      return present instanceof HTMLInputElement && present.checked;
    }
    const identity = fieldInRow(row, 'row_id');
    if (identity?.value) return true;
    return Array.from(row.querySelectorAll<InputControl>('input, select, textarea'))
      .some(field => {
        if (field instanceof HTMLInputElement && ['hidden', 'checkbox'].includes(field.type)) return false;
        if (field.name.endsWith('-price_currency')) return false;
        return field.value.trim() !== '';
      });
  }

  function synchronizeReasons(): void {
    form?.querySelectorAll<HTMLElement>('.recipe-ingredient').forEach(row => {
      row.querySelectorAll<HTMLElement>(':scope > details, :scope > .recipe-row-results')
        .forEach(details => { details.hidden = !rowUsed(row); });
    });
    form?.querySelectorAll<InputControl>('[name$="_reason"]').forEach(reason => {
      const value = pairedValue(reason);
      const wrapper = reason.closest<HTMLElement>('.field');
      if (!value || !wrapper) return;
      wrapper.classList.add('recipe-reason-field');
      const row = reason.closest<HTMLElement>('.recipe-ingredient');
      wrapper.hidden = value.value.trim() !== '';
      if (row && !rowUsed(row)) wrapper.hidden = true;
      if (reason.name.endsWith('-ohv_reason') && row) {
        const applicable = fieldInRow(row, 'ohv_applicable');
        if (applicable instanceof HTMLInputElement && !applicable.checked) wrapper.hidden = true;
      }
      if (value instanceof HTMLInputElement && value.readOnly) wrapper.hidden = true;
    });
    synchronizeInspector();
  }

  function synchronizeIso(): void {
    const checkbox = control('settings-isocyanate_present');
    const present = checkbox instanceof HTMLInputElement ? checkbox.checked : true;
    const fields = form?.querySelector<HTMLElement>('[data-recipe-iso-fields]');
    const reason = form?.querySelector<HTMLElement>('[data-recipe-iso-absence]');
    if (fields) fields.hidden = !present;
    if (reason) reason.hidden = present;
  }

  function catalogEntry(lot: string): CatalogEntry | null {
    if (!record(catalog)) return null;
    const entry = catalog[lot];
    return record(entry) && ('material_id' in entry) ? entry as unknown as CatalogEntry : null;
  }

  function sameNumber(left: string, right: string): boolean {
    return left === right || (left !== '' && right !== '' && Number(left) === Number(right));
  }

  function applyCatalogProperty(row: HTMLElement, name: string, raw: unknown, replace: boolean): void {
    const field = fieldInRow(row, name);
    if (!field) return;
    if (!replace && field.dataset.sourceValue !== undefined) {
      if (field instanceof HTMLInputElement) field.readOnly = true;
      field.title = 'Историческое значение из исходной рецептуры';
      return;
    }
    const value = typeof raw === 'string' || typeof raw === 'number' ? String(raw) : '';
    const known = value !== '';
    if (replace || field.value === '') field.value = value;
    const fromCatalog = known && sameNumber(field.value, value);
    if (field instanceof HTMLInputElement) field.readOnly = fromCatalog;
    field.dataset.catalogValue = fromCatalog ? value : '';
    field.title = fromCatalog ? 'Значение из выбранной партии' : '';
  }

  function applyCatalog(row: HTMLElement, replace = false): void {
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

  function clearProperties(row: HTMLElement): void {
    for (const name of ['ohv', 'nco_pct', 'density_g_ml', 'price_per_kg', 'pressure_bar', 'functionality']) {
      const field = fieldInRow(row, name);
      if (field) {
        field.value = '';
        if (field instanceof HTMLInputElement) field.readOnly = false;
        delete field.dataset.catalogValue;
        delete field.dataset.sourceValue;
      }
      const reason = fieldInRow(row, `${name}_reason`);
      if (reason) reason.value = '';
    }
  }

  function changedSelection(target: EventTarget | null): void {
    if (!(target instanceof HTMLSelectElement)) return;
    const materialChanged = target.name.endsWith('-material');
    if (!materialChanged && !target.name.endsWith('-specified_lot')) return;
    const row = target.closest<HTMLElement>('.recipe-ingredient');
    if (!row) return;
    if (materialChanged) {
      const lot = fieldInRow(row, 'specified_lot');
      if (lot) lot.value = '';
      const applicable = fieldInRow(row, 'ohv_applicable');
      if (applicable instanceof HTMLInputElement) applicable.checked = true;
    }
    clearProperties(row);
    applyCatalog(row, true);
  }

  function popupOpen(): boolean {
    return Boolean(document.querySelector(
      '.search-select-trigger[aria-expanded="true"], '
        + '.date-picker-popover:not([hidden])',
    ));
  }

  function updateSelectionTitles(): void {
    form?.querySelectorAll<HTMLSelectElement>('select').forEach(select => {
      const label = select.selectedOptions[0]?.textContent?.trim() ?? '';
      select.title = label;
      const trigger = select.closest('.search-select')
        ?.querySelector<HTMLButtonElement>('.search-select-trigger');
      if (trigger) trigger.title = label;
    });
  }

  function updateInspectorTitle(details: HTMLDetailsElement): void {
    const row = details.closest<HTMLElement>('.recipe-ingredient');
    const material = row?.querySelector<HTMLSelectElement>('select[name$="-material"]');
    const fallback = row?.dataset.recipeRowKind === 'gas'
      ? 'Газ / газовая смесь' : 'Компонент рецептуры';
    const label = material?.value
      ? material.selectedOptions[0]?.textContent?.trim() ?? fallback : fallback;
    details.querySelectorAll<HTMLElement>('[data-recipe-inspector-title]')
      .forEach(title => { title.textContent = label; title.title = label; });
  }

  function closeInspector(details: HTMLDetailsElement, focus = true): void {
    details.open = false;
    details.closest('.recipe-ingredient')?.classList.remove('recipe-inspector-active');
    if (activeInspector === details) activeInspector = null;
    if (!activeInspector) document.body.classList.remove('recipe-inspector-open');
    if (focus && details.isConnected) details.querySelector<HTMLElement>('summary')?.focus();
  }

  function openInspector(details: HTMLDetailsElement): void {
    form?.querySelectorAll<HTMLDetailsElement>('details.recipe-row-extra[open]')
      .forEach(other => { if (other !== details) closeInspector(other, false); });
    activeInspector = details;
    details.closest('.recipe-ingredient')?.classList.add('recipe-inspector-active');
    document.body.classList.add('recipe-inspector-open');
    updateInspectorTitle(details);
  }

  function synchronizeInspector(): void {
    if (!activeInspector) return;
    if (!activeInspector.isConnected || activeInspector.hidden || !activeInspector.open) {
      closeInspector(activeInspector, false);
    } else updateInspectorTitle(activeInspector);
  }

  function initializeInspector(): void {
    form?.addEventListener('toggle', event => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)
          || !details.classList.contains('recipe-row-extra')) return;
      if (details.open) openInspector(details);
      else closeInspector(details, details.contains(document.activeElement));
    }, true);
    form?.addEventListener('click', event => {
      const button = event.target instanceof Element
        ? event.target.closest('[data-recipe-inspector-close]') : null;
      const details = button?.closest<HTMLDetailsElement>('details.recipe-row-extra');
      if (details) { event.preventDefault(); closeInspector(details); }
    });
    const initial = form?.querySelector<HTMLDetailsElement>('details.recipe-row-extra[open]');
    if (initial) openInspector(initial);
    updateSelectionTitles();
  }

  function navigateNumericInput(event: KeyboardEvent): void {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || popupOpen()
        || event.altKey || event.ctrlKey || event.metaKey) return;
    const match = target.name.match(
      /^(polyol|gases)-\d+-(quantity|ohv|density_g_ml|price_per_kg|pressure_bar)$/,
    );
    if (!match || !['ArrowUp', 'ArrowDown', 'Enter'].includes(event.key)) return;
    if (event.shiftKey && event.key !== 'Enter') return;
    const group = target.closest('[data-recipe-rows]');
    const inputs = Array.from(group?.querySelectorAll<HTMLInputElement>(
      `input[name$="-${match[2]}"]`,
    ) ?? []).filter(input => !input.disabled && input.getClientRects().length > 0);
    const direction = event.key === 'ArrowUp' || event.shiftKey ? -1 : 1;
    const next = inputs[inputs.indexOf(target) + direction];
    event.preventDefault();
    next?.focus();
    if (next && next.type !== 'number') next.select();
  }

  function initializeWorkspace(): void {
    const button = document.querySelector<HTMLButtonElement>('[data-recipe-expand]');
    if (!button) return;
    const expand = (expanded: boolean): void => {
      document.body.classList.toggle('recipe-workspace-expanded', expanded);
      button.setAttribute('aria-pressed', String(expanded));
      button.textContent = expanded ? 'Свернуть экран' : 'На весь экран';
    };
    button.hidden = false;
    button.addEventListener('click', () => {
      expand(button.getAttribute('aria-pressed') !== 'true');
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && popupOpen()) popupEscapeEvents.add(event);
    }, true);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (popupEscapeEvents.has(event) || popupOpen()) return;
      if (closeWorkspacePanel()) { event.preventDefault(); return; }
      if (button.getAttribute('aria-pressed') !== 'true') return;
      expand(false);
      button.focus();
    });
  }

  function closeWorkspacePanel(): boolean {
    const inspector = activeInspector?.open ? activeInspector
      : form?.querySelector<HTMLDetailsElement>('details.recipe-row-extra[open]');
    if (inspector) {
      closeInspector(inspector);
      return true;
    }
    const details = form?.querySelector<HTMLDetailsElement>(
      'details[data-recipe-calculation-details][open]',
    );
    if (!details) return false;
    details.open = false;
    details.querySelector<HTMLElement>('summary')?.focus();
    return true;
  }

  function normalizeFormData(data: FormData): void {
    form?.querySelectorAll<InputControl>('.recipe-reason-field[hidden] input, .recipe-reason-field[hidden] textarea')
      .forEach(reason => { data.delete(reason.name); });
    const present = control('settings-isocyanate_present');
    if (present instanceof HTMLInputElement && present.checked) {
      data.delete('settings-isocyanate_absence_reason');
    }
  }

  function initializeEditing(): void {
    if (!form) return;
    form.addEventListener('keydown', navigateNumericInput);
    form.querySelectorAll<HTMLElement>('.recipe-ingredient').forEach(row => applyCatalog(row));
    synchronizeReasons();
    synchronizeIso();
    renderCalculation(readScript('recipe-calculation-data'));
    form.addEventListener('input', () => { synchronizeReasons(); queuePreview(); });
    form.addEventListener('change', event => {
      changedSelection(event.target);
      updateSelectionTitles();
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

  if (form) {
    initializeWorkspace();
    initializeInspector();
    if (readonly) {
      form.addEventListener('submit', event => event.preventDefault());
      synchronizeReasons();
      synchronizeIso();
      renderCalculation(readScript('recipe-calculation-data'));
    } else initializeEditing();
  }
}
