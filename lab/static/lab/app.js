document.querySelector('.mobile-menu')?.addEventListener('click',function(){const open=document.body.classList.toggle('menu-open');this.setAttribute('aria-expanded',String(open));});
document.addEventListener('click',function(e){const button=e.target.closest('[data-add-form]');if(!button)return;const group=document.getElementById(button.dataset.addForm);const template=group?.querySelector('template');const total=group?.querySelector('input[name$="-TOTAL_FORMS"]');if(!template||!total)return;const count=Number(total.value);const fragment=template.content.cloneNode(true);fragment.querySelectorAll('[name],[id],[for]').forEach(el=>{for(const attr of ['name','id','for'])if(el.hasAttribute(attr))el.setAttribute(attr,el.getAttribute(attr).replaceAll('__prefix__',count));});group.querySelector('.formset-rows').appendChild(fragment);total.value=count+1;});
document.querySelectorAll('[data-filter-form] select').forEach(select=>select.addEventListener('change',()=>select.form.requestSubmit()));

const sourceChoiceCache = new Map();
async function loadSourceChoices(select, sheet) {
  const selected = select.value;
  if (!sheet) {
    select.replaceChildren(new Option('---------', ''));
    return;
  }
  try {
    if (!sourceChoiceCache.has(sheet)) {
      sourceChoiceCache.set(sheet, fetch(`/source-cell-choices/?sheet=${encodeURIComponent(sheet)}`)
        .then(response => {
          if (!response.ok) throw new Error('Не удалось получить ячейки источника');
          return response.json();
        }));
    }
    const data = await sourceChoiceCache.get(sheet);
    const options = [new Option('---------', ''), ...data.cells.map(cell =>
      new Option(cell.label, String(cell.id), false, String(cell.id) === selected))];
    select.replaceChildren(...options);
  } catch {
    sourceChoiceCache.delete(sheet);
  }
}
function refreshSourceChoices() {
  const sheet = document.querySelector('[name="source_sheet"]')?.value;
  document.querySelectorAll('select[data-source-cell]').forEach(select => {
    if (sheet || select.dataset.sourceSheet) {
      loadSourceChoices(select, sheet || select.dataset.sourceSheet);
    }
  });
}
document.querySelector('[name="source_sheet"]')?.addEventListener('change', refreshSourceChoices);
document.addEventListener('click', event => {
  if (event.target.closest('[data-add-form]')) refreshSourceChoices();
});
refreshSourceChoices();
