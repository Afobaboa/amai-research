/* Demo interactions only. No API, persistence, credentials or business logic. */
(() => {
  const $ = (selector) => document.querySelector(selector);
  let toastTimer;
  const notify = (message) => {
    clearTimeout(toastTimer);
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
  };
  const records = [
    {time:'14:32', line:'Линия 1', unit:'Конвейер', text:'Отрегулировано натяжение ленты. Выполнен контрольный запуск.', checked:true},
    {time:'13:10', line:'Линия 2', unit:'Упаковка', text:'Очищены направляющие. Запись ожидает проверки.', checked:false},
    {time:'11:45', line:'Линия 1', unit:'Контроль', text:'Проверено положение датчика. Крепление зафиксировано.', checked:true}
  ];
  const el = (tag, text, className) => { const node = document.createElement(tag); node.textContent = text; if(className) node.className = className; return node; };
  function render() {
    $('#records').replaceChildren(); $('#records-list').replaceChildren();
    records.forEach(r => {
      const tr = document.createElement('tr');
      tr.append(el('td',r.time,'amai-num'),el('td',r.line+' / '+r.unit),el('td',r.text));
      const td = document.createElement('td');
      td.append(el('span',r.checked?'Проверено':'Ожидает проверки','amai-badge '+(r.checked?'amai-badge--success':'amai-badge--warning')));
      tr.append(td); $('#records').append(tr);
      const li = document.createElement('li'); li.className='amai-stack';
      li.append(el('strong',r.line+' · '+r.unit+' · '+r.time),el('p',r.text),el('span',r.checked?'Проверено':'Ожидает проверки','amai-small amai-muted'));
      $('#records-list').append(li);
    });
    $('#record-count').textContent = records.length;
  }
  function validate(input, error) {
    const valid = input.value.trim().length > 0;
    input.setAttribute('aria-invalid',String(!valid)); error.hidden=valid;
    if(!valid) input.focus(); return valid;
  }
  function addRecord(text,line='Линия 1',unit='Конвейер',checked=false) {
    records.unshift({text,line,unit,checked,time:new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}); render(); notify('Запись добавлена в журнал этой страницы');
  }
  $('#record-form').addEventListener('submit',event => {
    event.preventDefault(); if(!validate($('#description'),$('#description-error'))) return;
    addRecord($('#description').value.trim(),$('#line').value,$('#unit').value,$('#checked').checked);
    $('#record-form').reset();
  });
  $('#record-form').addEventListener('reset',() => { $('#description').removeAttribute('aria-invalid'); $('#description-error').hidden=true; });
  $('#description').addEventListener('input',() => { if($('#description').value.trim()) { $('#description').removeAttribute('aria-invalid'); $('#description-error').hidden=true; } });
  const dialog=$('#record-dialog'); let opener;
  document.querySelectorAll('[data-open-dialog]').forEach(button => button.addEventListener('click',()=> { opener=button; dialog.showModal(); }));
  $('#close-dialog').addEventListener('click',()=>dialog.close());
  $('#cancel-dialog').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{ $('#quick-form').reset(); $('#quick-description').removeAttribute('aria-invalid'); $('#quick-error').hidden=true; opener?.focus(); });
  $('#quick-form').addEventListener('submit',event=>{ event.preventDefault(); if(!validate($('#quick-description'),$('#quick-error'))) return; addRecord($('#quick-description').value.trim()); dialog.close(); });
  const tabs=Array.from(document.querySelectorAll('[role="tab"]'));
  function activate(tab,focus=false) { tabs.forEach(t=>{ const active=t===tab; t.setAttribute('aria-selected',String(active)); t.tabIndex=active?0:-1; document.getElementById(t.getAttribute('aria-controls')).hidden=!active; }); if(focus)tab.focus(); }
  tabs.forEach((tab,i)=>{ tab.addEventListener('click',()=>activate(tab)); tab.addEventListener('keydown',event=>{ let next; if(event.key==='ArrowRight')next=(i+1)%tabs.length; if(event.key==='ArrowLeft')next=(i-1+tabs.length)%tabs.length; if(event.key==='Home')next=0; if(event.key==='End')next=tabs.length-1; if(next!==undefined){event.preventDefault();activate(tabs[next],true);} }); });
  $('#touch-mode').addEventListener('change',event=>{ document.body.dataset.density=event.target.checked?'touch':'default'; });
  $('#save-example').addEventListener('click',()=>notify('Пример уведомления: изменения сохранены'));
  $('#cancel-example').addEventListener('click',()=>notify('Пример: действие отменено'));
  $('#reset-example').addEventListener('click',()=>{activate(tabs[0]);notify('Выбран вид «Таблица»');});
  $('#retry').addEventListener('click',async()=>{const b=$('#retry');b.disabled=true;b.setAttribute('aria-busy','true');b.textContent='Загрузка…'; await new Promise(resolve=>setTimeout(resolve,700)); $('#connection-error').hidden=true; $('#connection-status').hidden=false; $('#connection-status').textContent='Пример: соединение восстановлено'; });
  function setCurrent() { const hash=location.hash||'#overview'; document.querySelectorAll('.amai-nav a').forEach(a=> { if(a.hash===hash)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current'); }); }
  window.addEventListener('hashchange',setCurrent); setCurrent(); render();
})();
