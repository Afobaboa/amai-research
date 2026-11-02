{
  const tabletViewport = window.matchMedia(
    '(min-width: 641px) and (max-width: 1100px)',
  );
  function updateDensity(): void {
    if (tabletViewport.matches) document.body.dataset.density = 'touch';
    else delete document.body.dataset.density;
  }
  tabletViewport.addEventListener('change', updateDensity);
  updateDensity();

  const menuButton = document.querySelector<HTMLButtonElement>('.mobile-menu');
  const sidebar = document.getElementById('sidebar');
  const mobileViewport = window.matchMedia('(max-width: 800px)');

  function retainSidebarScroll(): void {
    const navigation = sidebar?.querySelector<HTMLElement>('nav');
    if (!navigation) return;
    const storageKey = 'lims.sidebar.scrollTop';
    function save(): void {
      try {
        sessionStorage.setItem(storageKey, String(navigation!.scrollTop));
      } catch {}
    }
    function restore(): void {
      try {
        const saved = Number(sessionStorage.getItem(storageKey));
        if (Number.isFinite(saved) && saved >= 0) navigation!.scrollTop = saved;
      } catch {}
    }
    navigation.addEventListener('scroll', save, {passive: true});
    navigation.addEventListener('click', save);
    window.addEventListener('pagehide', save);
    window.addEventListener('pageshow', event => {
      if (event.persisted) restore();
    });
  }
  retainSidebarScroll();

  function setMenuOpen(open: boolean, restoreFocus = false): void {
    document.body.classList.toggle('menu-open', open);
    menuButton?.setAttribute('aria-expanded', String(open));
    menuButton?.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
    if (open) {
      const activeLink = sidebar?.querySelector<HTMLAnchorElement>('.nav-link.active');
      const firstLink = sidebar?.querySelector<HTMLAnchorElement>('.nav-link');
      (activeLink ?? firstLink)?.focus({preventScroll: true});
    } else if (restoreFocus) {
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
    if (!(event.target instanceof Node)) return;
    if (!document.body.classList.contains('menu-open')) return;
    if (sidebar?.contains(event.target) || menuButton?.contains(event.target)) return;
    setMenuOpen(false, true);
  });

  document.addEventListener('focusin', event => {
    if (!(event.target instanceof Node)) return;
    if (!document.body.classList.contains('menu-open')) return;
    if (sidebar?.contains(event.target) || menuButton?.contains(event.target)) return;
    setMenuOpen(false);
  });

  mobileViewport.addEventListener('change', event => {
    if (!event.matches) setMenuOpen(false);
  });

  function replaceFormPrefix(fragment: DocumentFragment, count: number): void {
    fragment.querySelectorAll<HTMLElement>('*').forEach(element => {
      for (const attribute of Array.from(element.attributes)) {
        if (attribute.value.includes('__prefix__')) {
          element.setAttribute(
            attribute.name,
            attribute.value.replaceAll('__prefix__', String(count)),
          );
        }
      }
    });
  }

  function addForm(button: HTMLButtonElement): void {
    const group = document.getElementById(button.dataset.addForm ?? '');
    const template = group?.querySelector<HTMLTemplateElement>('template');
    const total = group?.querySelector<HTMLInputElement>('input[name$="-TOTAL_FORMS"]');
    const rows = group?.querySelector<HTMLElement>('.formset-rows');
    if (!template || !total || !rows) return;
    const count = Number(total.value);
    const fragment = template.content.cloneNode(true) as DocumentFragment;
    replaceFormPrefix(fragment, count);
    const firstInput = fragment.querySelector<HTMLElement>(
      'input:not([type="hidden"]):not([name$="-DELETE"]), select, textarea',
    );
    rows.appendChild(fragment);
    total.value = String(count + 1);
    firstInput?.focus();
    refreshSourceChoices();
    group?.closest('form')?.dispatchEvent(new Event('formsetchange'));
  }

  function removeForm(button: HTMLButtonElement): void {
    const group = button.closest<HTMLElement>('[data-formset-prefix]');
    const row = button.closest<HTMLElement>('.formset-row');
    const rows = group?.querySelector<HTMLElement>('.formset-rows');
    const total = group?.querySelector<HTMLInputElement>('input[name$="-TOTAL_FORMS"]');
    const initial = group?.querySelector<HTMLInputElement>('input[name$="-INITIAL_FORMS"]');
    if (!group || !row || !rows || !total || !initial) return;

    const index = Array.from(rows.children).indexOf(row);
    if (index < 0) return;
    row.remove();
    if (index < Number(initial.value)) initial.value = String(Number(initial.value) - 1);

    // These plain formsets save the submitted list. Keep its indexes contiguous
    // so removed rows and their validation errors cannot return on submit.
    const remaining = rows.querySelectorAll<HTMLElement>('.formset-row');
    const attributes = ['name', 'id', 'for', 'aria-describedby', 'aria-labelledby', 'aria-controls'];
    remaining.forEach((item, position) => {
      const oldPrefix = item.dataset.formPrefix;
      const newPrefix = `${group.dataset.formsetPrefix}-${position}`;
      if (oldPrefix && oldPrefix !== newPrefix) {
        item.querySelectorAll<HTMLElement>('*').forEach(element => {
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
    const focus = focusRow?.querySelector<HTMLButtonElement>('[data-remove-form]')
      ?? group.querySelector<HTMLButtonElement>('[data-add-form]');
    focus?.focus();
    group.closest('form')?.dispatchEvent(new Event('formsetchange'));
  }

  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>('[data-add-form]');
    if (button) addForm(button);
    const removeButton = event.target.closest<HTMLButtonElement>('[data-remove-form]');
    if (removeButton) removeForm(removeButton);
  });

  interface SourceCellChoice {
    id: number | string;
    label: string;
  }

  interface SourceCellChoices {
    cells: SourceCellChoice[];
  }

  const sourceChoiceCache = new Map<string, Promise<SourceCellChoices>>();
  const sourceChoiceRequests = new WeakMap<HTMLSelectElement, symbol>();

  function isSourceCellChoices(data: unknown): data is SourceCellChoices {
    if (typeof data !== 'object' || data === null || !('cells' in data)) return false;
    if (!Array.isArray(data.cells)) return false;
    return data.cells.every((cell: unknown) => (
      typeof cell === 'object' && cell !== null
      && 'label' in cell && typeof cell.label === 'string'
      && 'id' in cell && ['number', 'string'].includes(typeof cell.id)
    ));
  }

  async function fetchSourceChoices(sheet: string): Promise<SourceCellChoices> {
    const response = await fetch(
      `/source-cell-choices/?sheet=${encodeURIComponent(sheet)}`,
    );
    if (!response.ok) throw new Error('Не удалось получить ячейки источника');
    const data: unknown = await response.json();
    if (!isSourceCellChoices(data)) throw new Error('Неверный ответ источника');
    return data;
  }

  function cachedSourceChoices(sheet: string): Promise<SourceCellChoices> {
    let pending = sourceChoiceCache.get(sheet);
    if (!pending) {
      pending = fetchSourceChoices(sheet);
      sourceChoiceCache.set(sheet, pending);
    }
    return pending;
  }

  function sourceOptions(
    data: SourceCellChoices, select: HTMLSelectElement, preserveSelected: boolean,
  ): HTMLOptionElement[] {
    const selected = select.value;
    const selectedLabel = select.selectedOptions[0]?.textContent;
    const options = [new Option('---------', ''), ...data.cells.map(cell => (
      new Option(cell.label, String(cell.id), false,
        preserveSelected && String(cell.id) === selected)
    ))];
    if (preserveSelected && selected && selectedLabel
        && !data.cells.some(cell => String(cell.id) === selected)) {
      options.push(new Option(selectedLabel, selected, true, true));
    }
    return options;
  }

  async function loadSourceChoices(
    select: HTMLSelectElement, sheet: string, preserveSelected: boolean,
  ): Promise<void> {
    const request = Symbol();
    sourceChoiceRequests.set(select, request);
    select.dataset.loading = String(Boolean(sheet));
    delete select.dataset.error;
    if (!preserveSelected) select.replaceChildren(new Option('---------', ''));
    try {
      const data = sheet ? await cachedSourceChoices(sheet) : {cells: []};
      if (sourceChoiceRequests.get(select) !== request) return;
      select.replaceChildren(...sourceOptions(data, select, preserveSelected));
    } catch {
      sourceChoiceCache.delete(sheet);
      if (sourceChoiceRequests.get(select) === request) {
        select.dataset.error = 'Не удалось загрузить ячейки. Выберите лист ещё раз.';
      }
    } finally {
      if (sourceChoiceRequests.get(select) === request) {
        select.dataset.loading = 'false';
      }
    }
  }

  function refreshSourceChoices(preserveSelected = true): void {
    const sheet = document.querySelector<HTMLSelectElement>('[name="source_sheet"]');
    document.querySelectorAll<HTMLSelectElement>('select[data-source-cell]')
      .forEach(select => {
        const sourceSheet = sheet ? sheet.value : select.dataset.sourceSheet ?? '';
        void loadSourceChoices(select, sourceSheet, preserveSelected);
      });
  }

  document.querySelector('[name="source_sheet"]')
    ?.addEventListener('change', () => refreshSourceChoices(false));
  refreshSourceChoices(true);

  type DraftControl = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  interface DraftField {
    name: string;
    value: string | string[];
    checked?: boolean;
    display?: string;
  }
  interface FormDraft {
    initial: string;
    rendered: string;
    fields: DraftField[];
    counts: [string, number][];
  }
  const draftPrefix = 'lims.form-draft.v1:';
  const pendingDraftKey = 'lims.form-draft.pending';

  function draftControls(form: HTMLFormElement): DraftControl[] {
    return Array.from(form.elements).filter((field): field is DraftControl => (
      (field instanceof HTMLInputElement || field instanceof HTMLSelectElement
        || field instanceof HTMLTextAreaElement)
      && !!field.name && !field.disabled && field.name !== 'csrfmiddlewaretoken'
      && !['password', 'file', 'submit', 'reset', 'button'].includes(field.type)
    ));
  }

  function draftFields(form: HTMLFormElement, defaults = false): DraftField[] {
    return draftControls(form).map(control => {
      let value: string | string[];
      if (control instanceof HTMLSelectElement) {
        let options = Array.from(control.options).filter(option => (
          defaults ? option.defaultSelected : option.selected
        ));
        if (defaults && !control.multiple && !options.length && control.options[0]) {
          options = [control.options[0]];
        }
        value = control.multiple ? options.map(option => option.value) : options[0]?.value ?? '';
      } else {
        value = defaults ? control.dataset.serverValue ?? control.defaultValue : control.value;
      }
      if (defaults && control instanceof HTMLInputElement) {
        // Firefox restores hidden management values on reload, including their
        // defaultValue, without restoring dynamic rows. Compare the draft to
        // the counts actually rendered by Django, not that browser state.
        const group = control.closest<HTMLElement>('[data-formset-prefix]');
        if (control.name.endsWith('-TOTAL_FORMS')) value = group?.dataset.serverTotal ?? value;
        if (control.name.endsWith('-INITIAL_FORMS')) value = group?.dataset.serverInitial ?? value;
      }
      const field: DraftField = {name: control.name, value};
      if (control instanceof HTMLInputElement) {
        if (['checkbox', 'radio'].includes(control.type)) {
          field.checked = defaults ? control.defaultChecked : control.checked;
        }
        if (control.dataset.datePicker) {
          const original = control.dataset.serverValue ?? control.dataset.dateInitialValue ?? '';
          if (defaults) field.value = original;
          field.display = defaults ? (original ? original.split('-').reverse().join('.') : '__.__.____')
            : control.parentElement?.querySelector<HTMLInputElement>('input[type="text"]')?.value;
        }
      }
      return field;
    });
  }

  function readFormDraft(key: string): FormDraft | null {
    try {
      const draft: FormDraft = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (draft && typeof draft.initial === 'string' && typeof draft.rendered === 'string'
          && Array.isArray(draft.fields) && Array.isArray(draft.counts)
          && draft.fields.every(field => field && typeof field.name === 'string'
            && (typeof field.value === 'string' || (Array.isArray(field.value)
              && field.value.every(value => typeof value === 'string')))
            && (field.checked === undefined || typeof field.checked === 'boolean')
            && (field.display === undefined || typeof field.display === 'string'))
          && draft.counts.every(pair => Array.isArray(pair) && typeof pair[0] === 'string'
            && Number.isInteger(pair[1]) && pair[1] >= 0 && pair[1] <= 2000)) return draft;
    } catch { /* A missing or damaged draft must not prevent editing. */ }
    return null;
  }

  function restoreFormDraft(form: HTMLFormElement, groups: HTMLElement[], draft: FormDraft): void {
    for (const group of groups) {
      const count = draft.counts.find(([prefix]) => prefix === group.dataset.formsetPrefix)?.[1];
      const rows = group.querySelector<HTMLElement>('.formset-rows');
      const template = group.querySelector<HTMLTemplateElement>('template');
      if (count === undefined || !rows || !template) continue;
      while (rows.children.length > count) rows.lastElementChild?.remove();
      while (rows.children.length < count) {
        const fragment = template.content.cloneNode(true) as DocumentFragment;
        replaceFormPrefix(fragment, rows.children.length);
        rows.append(fragment);
      }
    }
    for (const control of draftControls(form)) {
      const field = draft.fields.find(field => field.name === control.name
        && (field.checked === undefined || field.value === control.value));
      if (!field) continue;
      if (control instanceof HTMLSelectElement && Array.isArray(field.value)) {
        const values = field.value;
        Array.from(control.options).forEach(option => { option.selected = values.includes(option.value); });
      } else if (typeof field.value === 'string') {
        control.value = field.value;
      }
      if (control instanceof HTMLInputElement && field.checked !== undefined) {
        control.checked = field.checked;
      }
      if (control instanceof HTMLInputElement && control.dataset.datePicker) {
        control.dispatchEvent(new CustomEvent('formdraftrestore', {detail: field.display}));
      }
      control.dispatchEvent(new Event('change', {bubbles: true}));
    }
  }

  function retainFormDrafts(): void {
    let pending: string | null = null;
    try {
      pending = sessionStorage.getItem(pendingDraftKey);
      if (pending && document.querySelector('.message.form-draft-saved')) {
        sessionStorage.removeItem(pending);
        sessionStorage.removeItem(pendingDraftKey);
        pending = null;
      }
    } catch { /* Editing also works when browser storage is unavailable. */ }

    document.querySelectorAll<HTMLFormElement>('main form').forEach((form, index) => {
      const groups = Array.from(form.querySelectorAll<HTMLElement>('[data-formset-prefix]'));
      if (!groups.length) return;
      const key = draftPrefix + JSON.stringify([
        groups[0].dataset.draftOwner, location.pathname, location.search, index,
      ]);
      const baseline = JSON.stringify(draftFields(form, true));
      const draft = readFormDraft(key);
      const submitted = pending === key && groups.some(group => group.dataset.draftBound === 'true');
      const compatible = draft && (draft.initial === baseline || draft.rendered === baseline);
      if (compatible && !submitted) restoreFormDraft(form, groups, draft);
      const initial = draft && (compatible || submitted) ? draft.initial : baseline;
      let discarded = false;
      const clear = (): void => {
        try { sessionStorage.removeItem(key); } catch { /* Optional storage. */ }
      };
      if (draft && !compatible && !submitted) {
        clear();
        // Firefox may already have restored old field values. When the server
        // record changed, clearing storage alone would leave those values shown.
        restoreFormDraft(form, groups, {
          initial: baseline, rendered: baseline,
          fields: JSON.parse(baseline) as DraftField[],
          counts: groups.map(group => [
            group.dataset.formsetPrefix!,
            Number(group.dataset.serverTotal ?? group.querySelector('.formset-rows')?.children.length ?? 0),
          ]),
        });
      }
      const save = (): void => {
        if (discarded) return;
        const fields = draftFields(form);
        if (JSON.stringify(fields) === initial) { clear(); return; }
        const counts: [string, number][] = groups.map(group => [
          group.dataset.formsetPrefix!,
          group.querySelector<HTMLElement>('.formset-rows')?.children.length ?? 0,
        ]);
        try {
          sessionStorage.setItem(key, JSON.stringify({initial, rendered: baseline, fields, counts}));
        } catch { /* Storage failure must not break normal form submission. */ }
      };
      form.addEventListener('input', save);
      form.addEventListener('change', save);
      form.addEventListener('formsetchange', save);
      window.addEventListener('pagehide', save);
      form.addEventListener('submit', () => {
        save();
        try { sessionStorage.setItem(pendingDraftKey, key); } catch { /* Optional storage. */ }
      });
      form.querySelectorAll('.form-actions a').forEach(link => {
        link.addEventListener('click', () => { discarded = true; clear(); });
      });
      if (submitted) {
        save();
        try { sessionStorage.removeItem(pendingDraftKey); } catch { /* Optional storage. */ }
      }
    });
  }
  retainFormDrafts();
}
