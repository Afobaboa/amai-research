/* Localized date controls. The original field keeps the ISO value for Django. */
{
  const iso = (date: Date): string => `${date.getFullYear().toString().padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const parse = (value: string): Date | null => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const [y, m, d] = value.split('-').map(Number);
    const date = new Date(0);
    date.setFullYear(y, m - 1, d);
    date.setHours(12, 0, 0, 0);
    return y >= 1 && iso(date) === value ? date : null;
  };
  const display = (value: string): string => value ? value.split('-').reverse().join('.') : '__.__.____';
  let sequence = 0;
  let closeActive: (() => void) | null = null;

  function enhance(source: HTMLInputElement): void {
    if (source.dataset.datePicker || source.readOnly || source.disabled) return;
    source.dataset.datePicker = 'true';
    const wrapper = document.createElement('div');
    wrapper.className = 'date-picker';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = source.className;
    input.placeholder = 'дд.мм.гггг';
    input.inputMode = 'numeric';
    input.autocomplete = 'off';
    input.title = 'Дата в формате дд.мм.гггг. Вводите только цифры.';
    input.required = source.required;
    for (const attribute of ['aria-describedby', 'aria-invalid', 'aria-label']) {
      if (source.hasAttribute(attribute)) input.setAttribute(attribute, source.getAttribute(attribute)!);
    }
    input.id = source.id;
    source.id = `${source.id || 'date'}-iso-${++sequence}`;
    const initialValue = source.value;
    input.value = display(initialValue);
    // Scope drafts to this tab, page, form and field, and ignore them if the
    // server's initial date changed since the draft was written.
    const draftKey = `lims-date-draft:${JSON.stringify([
      location.pathname, location.search, source.form?.id ||
        Array.from(document.forms).indexOf(source.form!), source.name || input.id,
    ])}`;
    const saveDraft = (): void => {
      try { sessionStorage.setItem(draftKey, JSON.stringify({initialValue, value: input.value})); }
      catch { /* Storage may be disabled; editing must still work. */ }
    };
    const clearDraft = (): void => {
      try { sessionStorage.removeItem(draftKey); } catch { /* Optional storage. */ }
    };
    try {
      const draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
      if (draft?.initialValue === initialValue && typeof draft.value === 'string' &&
          /^[\d_]{2}\.[\d_]{2}\.[\d_]{4}$/.test(draft.value)) input.value = draft.value;
      else clearDraft();
    } catch { /* Optional storage. */ }
    source.before(wrapper);
    source.type = 'hidden';
    source.required = false;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'date-picker-trigger';
    const label = document.querySelector<HTMLLabelElement>(`label[for="${CSS.escape(input.id)}"]`)?.textContent?.trim() || 'Дата';
    trigger.setAttribute('aria-label', `Открыть календарь: ${label}`);
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="4" y="5" width="16" height="16" rx="3"/><path d="M8 3v4m8-4v4M4 11h16"/></svg>';
    const popup = document.createElement('div');
    popup.className = 'date-picker-popover';
    popup.id = `date-calendar-${sequence}`;
    popup.hidden = true;
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-label', `Календарь: ${label}`);
    trigger.setAttribute('aria-controls', popup.id);
    wrapper.append(source, input, trigger);
    document.body.append(popup);
    let cursor = parse(source.value) || new Date();
    let month = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    let view: 'days' | 'months' | 'years' = 'days';
    let yearStart = 0;
    let positioned = false;
    const allowed = (date: Date): boolean => (!source.min || iso(date) >= source.min) && (!source.max || iso(date) <= source.max);
    const close = (): void => {
      popup.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (closeActive === close) closeActive = null;
    };
    const position = (): void => {
      // Choose a side once using the full day calendar, then keep that frame
      // while the user switches to the shorter month and year grids.
      if (positioned) return;
      popup.style.maxHeight = `${window.innerHeight - 16}px`;
      const rect = wrapper.getBoundingClientRect();
      const height = popup.getBoundingClientRect().height;
      popup.style.height = `${height}px`;
      popup.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - popup.offsetWidth - 8))}px`;
      popup.style.top = `${Math.max(8, rect.bottom + height + 8 <= window.innerHeight ? rect.bottom + 8 : rect.top - height - 8)}px`;
      positioned = true;
    };
    const commit = (value: string): void => {
      source.value = value;
      input.value = display(value);
      validate();
      saveDraft();
      source.dispatchEvent(new Event('input', {bubbles: true}));
      source.dispatchEvent(new Event('change', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      close();
      input.focus({preventScroll: true});
    };
    const button = (text: string, name: string, action: () => void): HTMLButtonElement => {
      const node = document.createElement('button');
      node.type = 'button'; node.textContent = text;
      node.setAttribute('aria-label', name); node.addEventListener('click', action);
      return node;
    };
    const render = (focusDay = false): void => {
      popup.replaceChildren();
      const header = document.createElement('div');
      header.className = 'date-picker-header';
      const heading = document.createElement('div');
      heading.className = 'date-picker-heading';
      const shift = view === 'years' ? 12 : 1;
      const navigate = (direction: number): void => {
        if (view === 'years') yearStart += direction * shift;
        else if (view === 'months') month.setFullYear(month.getFullYear() + direction);
        else month.setMonth(month.getMonth() + direction);
        render();
        popup.querySelector<HTMLButtonElement>(direction < 0 ? '.date-picker-prev' : '.date-picker-next')?.focus({preventScroll: true});
      };
      const previous = button('', view === 'days' ? 'Предыдущий месяц' : 'Предыдущие годы', () => navigate(-1));
      const next = button('', view === 'days' ? 'Следующий месяц' : 'Следующие годы', () => navigate(1));
      previous.className = 'date-picker-nav date-picker-prev';
      next.className = 'date-picker-nav date-picker-next';
      for (const [node, path] of [[previous, 'm15 6-6 6 6 6'], [next, 'm9 6 6 6-6 6']] as const) {
        node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
      }
      const minYear = Number(source.min.slice(0, 4)) || 1;
      const maxYear = Number(source.max.slice(0, 4)) || 9999;
      previous.disabled = view === 'years' ? yearStart <= minYear : month.getFullYear() <= minYear && (view === 'months' || month.getMonth() === 0);
      next.disabled = view === 'years' ? yearStart + 11 >= maxYear : month.getFullYear() >= maxYear && (view === 'months' || month.getMonth() === 11);
      if (view === 'years') {
        heading.textContent = `${yearStart}–${Math.min(9999, yearStart + 11)}`;
      } else {
        const monthButton = button(month.toLocaleDateString('ru-RU', {month: 'long'}), 'Выбрать месяц', () => {
          view = 'months'; render(); popup.querySelector<HTMLButtonElement>('.date-picker-choice.is-selected')?.focus({preventScroll: true});
        });
        monthButton.className = 'date-picker-month';
        const yearButton = button(String(month.getFullYear()), 'Выбрать год', () => {
          view = 'years'; yearStart = Math.floor((month.getFullYear() - 1) / 12) * 12 + 1;
          render(); popup.querySelector<HTMLButtonElement>('.date-picker-choice.is-selected')?.focus({preventScroll: true});
        });
        yearButton.className = 'date-picker-year';
        heading.append(monthButton, yearButton);
      }
      header.append(previous, heading, next);
      if (view !== 'days') {
        const choices = document.createElement('div');
        choices.className = 'date-picker-choices';
        const selectingMonths = view === 'months';
        for (let i = 0; i < 12; i++) {
          const year = selectingMonths ? month.getFullYear() : yearStart + i;
          const index = selectingMonths ? i : month.getMonth();
          const text = selectingMonths ? new Date(2000, i, 1).toLocaleDateString('ru-RU', {month: 'long'}) : String(year);
          const choice = button(text, text, () => {
            month.setFullYear(year, index, 1); cursor = new Date(month);
            view = 'days'; render(true);
          });
          choice.className = 'date-picker-choice';
          if (selectingMonths ? i === month.getMonth() : year === month.getFullYear()) choice.classList.add('is-selected');
          choice.disabled = year < minYear || year > maxYear;
          if (selectingMonths) {
            const first = new Date(month); first.setMonth(i, 1);
            const last = new Date(first); last.setMonth(i + 1, 0);
            choice.disabled = (!!source.min && iso(last) < source.min) || (!!source.max && iso(first) > source.max);
          }
          choices.append(choice);
        }
        popup.append(header, choices); position(); return;
      }
      const weekdays = document.createElement('div');
      weekdays.className = 'date-picker-weekdays';
      for (const day of ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']) {
        const item = document.createElement('span'); item.textContent = day; weekdays.append(item);
      }
      const grid = document.createElement('div');
      grid.className = 'date-picker-days';
      const first = new Date(month.getFullYear(), month.getMonth(), 1);
      first.setDate(1 - (first.getDay() + 6) % 7);
      for (let i = 0; i < 42; i++) {
        const day = new Date(first); day.setDate(first.getDate() + i);
        const value = iso(day);
        const node = button(String(day.getDate()), day.toLocaleDateString('ru-RU', {day: 'numeric', month: 'long', year: 'numeric'}), () => commit(value));
        node.className = 'date-picker-day';
        node.dataset.date = value;
        node.disabled = !allowed(day);
        node.tabIndex = value === iso(cursor) ? 0 : -1;
        if (day.getMonth() !== month.getMonth()) node.classList.add('is-outside');
        if (value === iso(new Date())) { node.classList.add('is-today'); node.setAttribute('aria-current', 'date'); }
        if (value === source.value) { node.classList.add('is-selected'); node.setAttribute('aria-pressed', 'true'); }
        node.addEventListener('keydown', event => {
          const shifts: Record<string, number> = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -(day.getDay() + 6) % 7, End: 6 - (day.getDay() + 6) % 7};
          if (event.key in shifts || event.key === 'PageUp' || event.key === 'PageDown') {
            event.preventDefault(); cursor = new Date(day);
            if (event.key === 'PageUp' || event.key === 'PageDown') {
              cursor.setDate(1); cursor.setMonth(cursor.getMonth() + (event.key === 'PageUp' ? -1 : 1));
              const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
              cursor.setDate(Math.min(day.getDate(), last));
            } else cursor.setDate(cursor.getDate() + shifts[event.key]);
            month = new Date(cursor.getFullYear(), cursor.getMonth(), 1); render(true);
          }
        });
        grid.append(node);
      }
      const footer = document.createElement('div');
      footer.className = 'date-picker-footer';
      const today = button('Сегодня', 'Выбрать сегодняшнюю дату', () => commit(iso(new Date())));
      today.disabled = !allowed(new Date());
      footer.append(today, button('Очистить', 'Очистить дату', () => commit('')));
      popup.append(header, weekdays, grid, footer);
      position();
      if (focusDay) popup.querySelector<HTMLButtonElement>(`[data-date="${iso(cursor)}"]`)?.focus({preventScroll: true});
    };
    const open = (): void => {
      closeActive?.(); closeActive = close;
      cursor = parse(source.value) || new Date();
      if (source.min && iso(cursor) < source.min) cursor = parse(source.min)!;
      if (source.max && iso(cursor) > source.max) cursor = parse(source.max)!;
      month = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
      view = 'days';
      positioned = false;
      popup.style.height = '';
      popup.hidden = false; trigger.setAttribute('aria-expanded', 'true'); render(true);
    };
    trigger.addEventListener('click', () => popup.hidden ? open() : close());
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); open(); }
    });
    const slots = [0, 1, 3, 4, 6, 7, 8, 9];
    const template = '__.__.____';
    const validate = (): void => {
      const value = input.value;
      const hasDigits = /\d/.test(value);
      const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value);
      const date = match ? parse(`${match[3]}-${match[2]}-${match[1]}`) : null;
      source.value = date && allowed(date) ? iso(date) : '';
      input.classList.toggle('is-empty', !hasDigits);
      input.setCustomValidity(!hasDigits
        ? (input.required ? 'Введите дату.' : '')
        : source.value ? '' : 'Введите корректную дату в формате дд.мм.гггг в допустимом диапазоне.');
    };
    const notify = (): void => {
      validate(); saveDraft(); source.dispatchEvent(new Event('input', {bubbles: true}));
    };
    const edit = (text: string, deletion?: 'backward' | 'forward'): void => {
      let start = input.selectionStart ?? 0;
      const end = input.selectionEnd ?? start;
      const chars = input.value.split('');
      if (end > start) for (const slot of slots) { if (slot >= start && slot < end) chars[slot] = '_'; }
      if (deletion && end === start) {
        const slot = deletion === 'backward' ? slots.filter(slot => slot < start).at(-1) : slots.find(slot => slot >= start);
        if (slot !== undefined) { chars[slot] = '_'; start = slot; }
      }
      let caret = start;
      for (const digit of text.replace(/\D/g, '')) {
        const slot = slots.find(slot => slot >= caret);
        if (slot === undefined) break;
        chars[slot] = digit; caret = slots.find(next => next > slot) ?? 10;
      }
      input.value = chars.join('');
      input.setSelectionRange(caret, caret);
      notify();
    };
    input.addEventListener('beforeinput', event => {
      if (event.inputType.startsWith('insert') && event.data !== null) {
        event.preventDefault(); edit(event.data);
      } else if (event.inputType.startsWith('delete')) {
        event.preventDefault(); edit('', event.inputType.includes('Backward') ? 'backward' : 'forward');
      }
    });
    input.addEventListener('paste', event => {
      event.preventDefault(); edit(event.clipboardData?.getData('text') || '');
    });
    // Autofill and accessibility tools can replace the complete value directly.
    input.addEventListener('input', () => {
      const chars = template.split('');
      if (/^[\d_]{2}\.[\d_]{2}\.[\d_]{4}$/.test(input.value)) {
        notify(); return;
      }
      const digits = input.value.replace(/\D/g, '').slice(0, 8);
      [...digits].forEach((digit, index) => { chars[slots[index]] = digit; });
      input.value = chars.join(''); notify();
    });
    input.addEventListener('focus', () => {
      if (!/\d/.test(input.value)) input.setSelectionRange(0, 0);
    });
    input.addEventListener('click', () => {
      if (!/\d/.test(input.value)) input.setSelectionRange(0, 0);
    });
    input.addEventListener('change', () => source.dispatchEvent(new Event('change', {bubbles: true})));
    validate();
    document.addEventListener('pointerdown', event => {
      if (!wrapper.contains(event.target as Node) && !popup.contains(event.target as Node)) close();
    });
    document.addEventListener('focusin', event => {
      if (!wrapper.contains(event.target as Node) && !popup.contains(event.target as Node)) close();
    });
    popup.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); trigger.focus({preventScroll: true}); }
    });
    window.addEventListener('resize', close);
    window.addEventListener('scroll', event => { if (!(event.target instanceof Node && popup.contains(event.target))) close(); }, true);
    source.form?.addEventListener('submit', clearDraft);
    source.form?.addEventListener('reset', () => { clearDraft(); close(); setTimeout(() => { source.value = initialValue; input.value = display(initialValue); validate(); }, 0); });
  }
  // Keep the native control if the independent stylesheet did not load.
  if (getComputedStyle(document.body).getPropertyValue('--lims-date-picker-ready').trim() === '1') {
    document.querySelectorAll<HTMLInputElement>('input[type="date"]').forEach(enhance);
  }
}
