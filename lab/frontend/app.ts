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

  function setMenuOpen(open: boolean, restoreFocus = false): void {
    document.body.classList.toggle('menu-open', open);
    menuButton?.setAttribute('aria-expanded', String(open));
    menuButton?.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
    if (open) {
      const activeLink = sidebar?.querySelector<HTMLAnchorElement>('.nav-link.active');
      const firstLink = sidebar?.querySelector<HTMLAnchorElement>('.nav-link');
      (activeLink ?? firstLink)?.focus();
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
      'input:not([type="hidden"]), select, textarea',
    );
    rows.appendChild(fragment);
    total.value = String(count + 1);
    firstInput?.focus();
    refreshSourceChoices();
  }

  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>('[data-add-form]');
    if (button) addForm(button);
  });

  document.querySelectorAll<HTMLSelectElement>('[data-filter-form] select')
    .forEach(select => select.addEventListener('change', () => {
      select.form?.requestSubmit();
    }));

  interface SourceCellChoice {
    id: number | string;
    label: string;
  }

  interface SourceCellChoices {
    cells: SourceCellChoice[];
  }

  const sourceChoiceCache = new Map<string, Promise<SourceCellChoices>>();

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

  async function loadSourceChoices(
    select: HTMLSelectElement, sheet: string, preserveSelected: boolean,
  ): Promise<void> {
    const selected = select.value;
    const selectedLabel = select.selectedOptions[0]?.textContent;
    if (!sheet) {
      select.replaceChildren(new Option('---------', ''));
      return;
    }
    try {
      const data = await cachedSourceChoices(sheet);
      const options = [new Option('---------', ''), ...data.cells.map(cell => (
        new Option(cell.label, String(cell.id), false, String(cell.id) === selected)
      ))];
      if (preserveSelected && selected && selectedLabel
          && !data.cells.some(cell => String(cell.id) === selected)) {
        options.push(new Option(selectedLabel, selected, true, true));
      }
      select.replaceChildren(...options);
    } catch {
      sourceChoiceCache.delete(sheet);
    }
  }

  function refreshSourceChoices(preserveSelected = true): void {
    const sheet = document.querySelector<HTMLSelectElement>('[name="source_sheet"]')
      ?.value;
    document.querySelectorAll<HTMLSelectElement>('select[data-source-cell]')
      .forEach(select => {
        const sourceSheet = sheet || select.dataset.sourceSheet;
        if (sourceSheet) void loadSourceChoices(select, sourceSheet, preserveSelected);
      });
  }

  document.querySelector('[name="source_sheet"]')
    ?.addEventListener('change', () => refreshSourceChoices(false));
  refreshSourceChoices(true);
}
