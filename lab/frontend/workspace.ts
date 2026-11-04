{
  function initializeFullscreen(button: HTMLButtonElement): void {
    const setExpanded = (expanded: boolean): void => {
      document.body.classList.toggle('workspace-expanded', expanded);
      button.setAttribute('aria-pressed', String(expanded));
      button.textContent = expanded ? 'Свернуть экран' : 'На весь экран';
      requestAnimationFrame(fitWorkspaceTables);
    };
    button.hidden = false;
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      setExpanded(button.getAttribute('aria-pressed') !== 'true');
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented
          || document.querySelector('.search-select-popover:not([hidden]), '
            + '.date-picker-popover:not([hidden])')) return;
      if (button.getAttribute('aria-pressed') !== 'true') return;
      event.preventDefault();
      event.stopPropagation();
      setExpanded(false);
      button.focus({preventScroll: true});
    }, true);
  }

  function trailingHeight(element: HTMLElement): number {
    let height = 0;
    let current: HTMLElement | null = element;
    while (current && current.id !== 'main-content') {
      let sibling = current.nextElementSibling;
      while (sibling) {
        if (sibling instanceof HTMLElement && !sibling.hidden) {
          const style = getComputedStyle(sibling);
          if (style.position !== 'absolute' && style.position !== 'fixed') {
            height += sibling.getBoundingClientRect().height;
            height += parseFloat(style.marginTop) + parseFloat(style.marginBottom);
          }
        }
        sibling = sibling.nextElementSibling;
      }
      const style = getComputedStyle(current);
      height += parseFloat(style.paddingBottom) + parseFloat(style.marginBottom);
      current = current.parentElement;
    }
    return height;
  }

  function fitStandaloneTable(scroll: HTMLElement): void {
    const footer = document.querySelector('.page-footer')?.getBoundingClientRect();
    const reserve = trailingHeight(scroll) + (footer?.height ?? 0) + 28;
    const height = Math.max(180, innerHeight - scroll.getBoundingClientRect().top
      - reserve);
    scroll.style.setProperty('--workspace-table-height', `${height}px`);
  }

  function scrollToFragment(): void {
    let id = '';
    try { id = decodeURIComponent(location.hash.slice(1)); } catch {}
    const target = document.getElementById(id);
    if (!target?.closest('[data-workspace]')) return;
    requestAnimationFrame(() => target.scrollIntoView({block: 'nearest'}));
  }

  function fitWorkspaceTables(): void {
    const desktop = window.matchMedia('(min-width: 1101px)').matches;
    document.querySelectorAll<HTMLElement>(
      '[data-live-results] > .table-scroll, .workspace-table-scroll',
    ).forEach(scroll => {
      if (!scroll.getClientRects().length) return;
      if (!scroll.closest('.workspace-grid')) fitStandaloneTable(scroll);
      scroll.classList.add('workspace-scroll');
      const overflow = desktop && scroll.scrollHeight > scroll.clientHeight + 1
        || scroll.scrollWidth > scroll.clientWidth + 1;
      scroll.tabIndex = overflow ? 0 : -1;
      scroll.setAttribute('role', 'region');
      if (!scroll.hasAttribute('aria-label')) {
        scroll.setAttribute('aria-label', 'Таблица данных');
      }
    });
  }

  document.querySelectorAll<HTMLButtonElement>('[data-workspace-fullscreen]')
    .forEach(initializeFullscreen);
  let pendingFrame = 0;
  const scheduleFit = (): void => {
    cancelAnimationFrame(pendingFrame);
    pendingFrame = requestAnimationFrame(fitWorkspaceTables);
  };
  window.addEventListener('resize', scheduleFit);
  window.addEventListener('hashchange', scrollToFragment);
  window.addEventListener('load', scrollToFragment);
  document.addEventListener('toggle', scheduleFit, true);
  let previousWidth = 0;
  new ResizeObserver(entries => {
    const width = entries[0]?.contentRect.width ?? 0;
    if (width === previousWidth) return;
    previousWidth = width;
    scheduleFit();
  }).observe(document.getElementById('main-content')!);
  new MutationObserver(scheduleFit).observe(document.getElementById('main-content')!, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'],
  });
  document.fonts.ready.then(scheduleFit);
  scheduleFit();
}
