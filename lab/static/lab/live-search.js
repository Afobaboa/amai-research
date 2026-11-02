"use strict";
{
    let sequence = 0;
    class LiveSearch {
        form;
        panel;
        input;
        status = document.createElement('p');
        pathname = window.location.pathname;
        timer;
        request;
        generation = 0;
        restoring = false;
        constructor(form, panel, input) {
            this.form = form;
            this.panel = panel;
            this.input = input;
            panel.id ||= `live-search-${++sequence}`;
            panel.setAttribute('aria-busy', 'false');
            input.setAttribute('aria-controls', panel.id);
            input.autocomplete = 'off';
            this.status.className = 'live-search-status amai-sr-only';
            this.status.setAttribute('role', 'status');
            form.after(this.status);
            this.bindEvents();
        }
        bindEvents() {
            this.input.addEventListener('input', event => {
                if (!event.isComposing)
                    this.schedule();
            });
            this.input.addEventListener('compositionstart', () => {
                this.cancel();
                this.setBusy(false);
            });
            this.input.addEventListener('compositionend', () => this.schedule());
            this.form.addEventListener('submit', event => {
                event.preventDefault();
                void this.search(this.formUrl(), 'push');
            });
            this.form.addEventListener('change', event => {
                if (!this.restoring && event.target instanceof HTMLSelectElement) {
                    void this.search(this.formUrl(), 'push');
                }
            });
            this.panel.addEventListener('click', event => this.followLink(event));
            this.bindHistory();
        }
        bindHistory() {
            window.addEventListener('popstate', () => this.restore());
            window.addEventListener('pagehide', () => {
                this.cancel();
                this.setBusy(false);
            });
            window.addEventListener('pageshow', event => {
                if (event.persisted)
                    this.restore();
            });
        }
        cancel() {
            window.clearTimeout(this.timer);
            this.request?.abort();
            this.request = undefined;
            this.generation++;
        }
        schedule() {
            this.cancel();
            this.setBusy(false);
            this.timer = window.setTimeout(() => {
                void this.search(this.formUrl(), 'replace');
            }, 250);
        }
        formUrl() {
            const url = new URL(this.form.action);
            url.search = '';
            for (const [name, value] of new FormData(this.form)) {
                if (typeof value === 'string' && value)
                    url.searchParams.append(name, value);
            }
            url.searchParams.delete('page');
            return url;
        }
        async search(url, historyMode) {
            this.cancel();
            const request = new AbortController();
            const generation = this.generation;
            this.request = request;
            this.setBusy(true);
            try {
                const document = await this.load(url, request.signal);
                if (request.signal.aborted || generation !== this.generation)
                    return;
                if (document)
                    this.apply(document, url, historyMode);
            }
            catch {
                if (!request.signal.aborted && generation === this.generation) {
                    this.status.className = 'live-search-status is-error';
                    this.status.textContent = 'Список не обновился. '
                        + 'Нажмите «Найти», чтобы повторить.';
                }
            }
            finally {
                if (this.request === request) {
                    this.request = undefined;
                    this.setBusy(false);
                }
            }
        }
        async load(url, signal) {
            const response = await fetch(url, {
                signal, credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'text/html', 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (signal.aborted)
                return null;
            if (response.redirected && new URL(response.url).pathname !== this.pathname) {
                window.location.assign(response.url);
                return null;
            }
            if (!response.ok)
                throw new Error('Не удалось получить список');
            return new DOMParser().parseFromString(await response.text(), 'text/html');
        }
        apply(document, url, historyMode) {
            const form = document.querySelector('[data-live-search]');
            const panel = form?.closest('[data-live-results]');
            if (!form || !panel)
                throw new Error('Не удалось прочитать список');
            const focused = window.document.activeElement;
            const moveFocus = focused && this.panel.contains(focused)
                && !this.form.contains(focused);
            const fragment = window.document.createDocumentFragment();
            for (const child of panel.children) {
                if (child !== form)
                    fragment.append(window.document.importNode(child, true));
            }
            this.syncReset(form);
            for (const child of Array.from(this.panel.children)) {
                if (child !== this.form && child !== this.status)
                    child.remove();
            }
            this.panel.append(fragment);
            this.status.className = 'live-search-status amai-sr-only';
            this.status.textContent = this.panel.querySelector('.table-footer > span')
                ?.textContent?.trim() || 'Список обновлён';
            this.updateHistory(url, historyMode);
            if (moveFocus)
                this.focusResults();
        }
        focusResults() {
            const result = this.panel.querySelector('.table-scroll tbody a, .timeline a, .pagination a');
            (result ?? this.input).focus({ preventScroll: true });
        }
        syncReset(form) {
            const reset = form.querySelector('a.text-link');
            this.form.querySelector('a.text-link')?.remove();
            if (reset)
                this.form.append(window.document.importNode(reset, true));
        }
        updateHistory(url, mode) {
            if (mode === 'none' || url.href === window.location.href)
                return;
            if (mode === 'push')
                history.pushState(history.state, '', url);
            else
                history.replaceState(history.state, '', url);
        }
        setBusy(busy) {
            this.panel.setAttribute('aria-busy', String(busy));
            this.form.toggleAttribute('data-search-loading', busy);
            if (busy) {
                this.status.className = 'live-search-status amai-sr-only';
                this.status.textContent = 'Ищем…';
            }
        }
        followLink(event) {
            if (event.button !== 0 || event.ctrlKey || event.metaKey
                || event.shiftKey || event.altKey || !(event.target instanceof Element)) {
                return;
            }
            const link = event.target.closest('.pagination a[href], form[data-live-search] a.text-link[href]');
            if (!link || !this.panel.contains(link))
                return;
            let url = new URL(link.href);
            if (url.origin !== location.origin || url.pathname !== this.pathname)
                return;
            event.preventDefault();
            if (this.form.contains(link))
                this.fillForm(url);
            else
                url = this.paginationUrl(url);
            void this.search(url, 'push');
        }
        paginationUrl(link) {
            const url = this.formUrl();
            const previous = new URL(window.location.href);
            const changed = Array.from(new FormData(this.form)).some(([name, value]) => (typeof value === 'string'
                && value !== (previous.searchParams.get(name) ?? '')));
            const page = link.searchParams.get('page');
            if (!changed && page)
                url.searchParams.set('page', page);
            return url;
        }
        fillForm(url) {
            this.restoring = true;
            try {
                for (const field of this.form.elements) {
                    if (!(field instanceof HTMLInputElement
                        || field instanceof HTMLSelectElement) || !field.name)
                        continue;
                    field.value = url.searchParams.get(field.name) ?? '';
                    if (field instanceof HTMLSelectElement) {
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            }
            finally {
                this.restoring = false;
            }
        }
        restore() {
            const url = new URL(window.location.href);
            if (url.pathname !== this.pathname)
                return;
            this.fillForm(url);
            void this.search(url, 'none');
        }
    }
    document.querySelectorAll('form[data-live-search]').forEach(form => {
        const panel = form.closest('[data-live-results]');
        const input = form.querySelector('input[name="q"]');
        if (panel && input)
            new LiveSearch(form, panel, input);
    });
}
