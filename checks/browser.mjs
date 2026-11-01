import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';

// Both Django and the browser run locally. Only this run's temporary DB is used.
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), 'lims-browser-'));
const env = {
  ...process.env,
  DJANGO_SETTINGS_MODULE: 'config.local', DEBUG: '1', DEMO_MODE: '1',
  LIMS_LOCAL_DB: join(scratch, 'browser.sqlite3'),
  LIMS_LOCAL_MEDIA: join(scratch, 'media'),
  SQLITE_PATH: join(scratch, 'browser.sqlite3'),
  ALLOWED_HOSTS: '127.0.0.1,localhost', PYTHONUNBUFFERED: '1',
};
delete env.DATABASE_URL;
delete env.SITE_PASSWORD_HASH;
const python = process.env.LIMS_PYTHON || join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const headed = process.argv.includes('--headed') || process.env.LIMS_HEADED === '1';
const require = createRequire(import.meta.url);
const token = randomUUID();
let server, browser, serverClosed, artifacts, base;
let logs = '', cleanupPromise, interrupted = false;

const init = args => {
  const run = spawnSync(python, ['manage.py', ...args], {cwd: root, env, encoding: 'utf8'});
  if (run.error) throw new Error(`Cannot run ${python}. Set up Python dependencies first. ${run.error.message}`);
  assert.equal(run.status, 0, run.stdout + run.stderr);
};

// Let the OS bind port 0 in the actual server process, avoiding a free-port race.
// An explicit occupied port fails at bind; no browser request is sent to it.
const pythonServer = `
import json, os, sys
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True
application = StaticFilesHandler(get_wsgi_application())
with make_server('127.0.0.1', int(sys.argv[1]), application, server_class=Server) as httpd:
    print('LIMS_BROWSER_READY:' + json.dumps({'token': sys.argv[2], 'port': httpd.server_port}), flush=True)
    httpd.serve_forever()
`;

async function startServer(port) {
  server = spawn(python, ['-c', pythonServer, String(port), token], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  serverClosed = new Promise(resolve => server.once('close', resolve));
  await new Promise((resolveReady, reject) => {
    let output = '';
    const timeout = setTimeout(() => fail(new Error(`Local test server did not start. ${logs}`)), 15000);
    function finish() {
      clearTimeout(timeout);
      server.off('error', fail);
      server.off('exit', exited);
      server.stdout.off('data', stdout);
    }
    function fail(error) { finish(); reject(error); }
    function exited(code, signal) { fail(new Error(`Local test server exited (${signal || code}). ${logs}`)); }
    function stdout(data) {
      output += data;
      for (const line of output.split('\n')) {
        if (!line.startsWith('LIMS_BROWSER_READY:')) continue;
        try {
          const ready = JSON.parse(line.slice('LIMS_BROWSER_READY:'.length));
          if (ready.token !== token || !Number.isInteger(ready.port) || ready.port < 1 || ready.port > 65535) continue;
          base = `http://127.0.0.1:${ready.port}`;
          finish(); resolveReady(); return;
        } catch { /* Wait for a complete line. */ }
      }
    }
    server.on('error', fail);
    server.on('exit', exited);
    server.stdout.on('data', stdout);
    server.stdout.on('data', data => { logs = (logs + data).slice(-12000); });
    server.stderr.on('data', data => { logs = (logs + data).slice(-12000); });
  });
  // A server that stops unexpectedly cannot be replaced by an unrelated server
  // and silently reused by the remaining test actions.
  server.once('exit', () => {
    if (!cleanupPromise) {
      console.error('The isolated test server stopped unexpectedly.');
      process.exitCode = 1;
      void cleanup();
    }
  });
  console.log(`Isolated test server: ${base}`);
}

function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    try { await browser?.close(); }
    finally {
      if (server) {
        if (server.exitCode === null && server.signalCode === null) server.kill('SIGTERM');
        const force = setTimeout(() => {
          if (server.exitCode === null && server.signalCode === null) server.kill('SIGKILL');
        }, 3000);
        try { await serverClosed; }
        finally { clearTimeout(force); }
      }
      // SQLite is removed only once the owning server has completely exited.
      rmSync(scratch, {recursive:true, force:true});
    }
  })();
  return cleanupPromise;
}

const signalHandlers = new Map(['SIGINT', 'SIGTERM'].map(signal => {
  const handler = () => {
    interrupted = true;
    process.exitCode = signal === 'SIGINT' ? 130 : 143;
    console.error(`Stopping local browser checks (${signal}).`);
    void cleanup();
  };
  process.on(signal, handler);
  return [signal, handler];
}));

const option = async (page, selector, label) => {
  const value = await page.locator(`${selector} option`).evaluateAll((items, label) => items.find(item => item.textContent.includes(label))?.value, label);
  assert.ok(value, `Option containing ${label} in ${selector}`);
  await page.locator(selector).selectOption(value);
  return value;
};

try {
  const unknown = process.argv.slice(2).filter(arg => arg !== '--headed');
  assert.equal(unknown.length, 0, `Unknown argument: ${unknown.join(' ')}`);
  let chromium;
  try { ({chromium} = require(process.env.KUDO_PLAYWRIGHT_MODULE || 'playwright')); }
  catch(error) { throw new Error(`Playwright is unavailable. Run npm ci and npm run install:browsers. ${error.message}`); }
  const requestedPort = process.env.LIMS_BROWSER_PORT;
  assert.ok(requestedPort === undefined || /^\d+$/.test(requestedPort), 'LIMS_BROWSER_PORT must be an integer from 1 to 65535.');
  const port = requestedPort === undefined ? 0 : Number(requestedPort);
  assert.ok(requestedPort === undefined || (port >= 1 && port <= 65535), 'LIMS_BROWSER_PORT must be an integer from 1 to 65535.');
  const artifactsRoot = resolve(process.env.LIMS_ARTIFACT_DIR || join(root, 'var', 'browser-artifacts'));
  mkdirSync(artifactsRoot, {recursive:true});
  artifacts = mkdtempSync(join(artifactsRoot, 'run-'));
  init(['migrate', '--noinput']);
  init(['seed_demo', '--confirm-demo']);
  await startServer(port);
  browser = await chromium.launch({headless:!headed,args:['--no-sandbox','--disable-dev-shm-usage']});
  if (interrupted || cleanupPromise) {
    await browser.close();
    throw new Error('Local browser checks were stopped.');
  }
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  const go = async path => {
    const response = await page.goto(base + path, {waitUntil:'networkidle'});
    assert.equal(response.status(),200,path);
    assert.equal(await page.locator('main h1').count(),1,path);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${path}: desktop overflow`);
  };
  const submit = async label => {
    await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:label,exact:true}).click()]);
    assert.equal(await page.locator('.errorlist,.field-error').count(),0,(await page.locator('.errorlist,.field-error').allTextContents()).join('; '));
  };
  await page.goto(base+'/login/');
  await page.locator('#id_password').fill('incorrect-demo-password');
  await page.getByRole('button',{name:/Продолжить/}).click();
  assert.match(await page.locator('.field-error').innerText(),/Неверный пароль/);
  await page.locator('#id_password').fill('lims-demo');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:/Продолжить/}).click()]);
  await option(page,'#id_employee','Анна Волкова');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:/Открыть лабораторию/}).click()]);
  await go('/');
  await page.screenshot({path:join(artifacts, 'overview.png'),fullPage:true});
  const desktopPaths = ['/recipes/','/tests/','/history/','/sources/','/materials/','/lots/','/metrics/','/mixtures/',
    '/recipe-templates/','/calculation-templates/','/sources/new/','/materials/new/','/lots/new/','/metrics/new/',
    '/mixtures/new/','/recipe-templates/new/','/calculation-templates/new/','/recipes/new/','/tests/new/',
    '/recipes/1/','/tests/1/','/sources/1/','/lots/1/','/recipe-templates/1/','/calculation-templates/1/',
    '/calculations/1/','/tests/6/results/new/','/tests/6/observations/new/','/tests/6/conditioning/new/'];
  for (const path of desktopPaths) await go(path);
  for (const [path, filename] of [['/lots/','lots.png'],['/recipe-templates/','recipe-templates.png'],
    ['/calculation-templates/1/','calculation-template.png'],['/sources/1/','source.png'],['/calculations/1/','calculation.png']]) {
    await go(path); await page.screenshot({path:join(artifacts,filename),fullPage:true});
  }
  await go('/recipes/1/');
  await page.screenshot({path:join(artifacts, 'recipe.png'),fullPage:true});
  await go('/tests/1/');
  await page.screenshot({path:join(artifacts, 'test.png'),fullPage:true});
  console.log('Desktop pages render without overflow; pristine winter UI screenshots saved.');

  const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,storageState:await context.storageState()});
  const mobilePage=await mobile.newPage();
  mobilePage.on('pageerror',error=>errors.push(error.message));
  const mobilePaths=['/', ...desktopPaths];
  const overflows=[];
  for(const path of mobilePaths){
    const response=await mobilePage.goto(base+path,{waitUntil:'networkidle'});
    assert.equal(response.status(),200,path);
    if(await mobilePage.evaluate(()=>document.documentElement.scrollWidth>innerWidth)){
      overflows.push(path);
      console.log('Mobile overflow:',path,await mobilePage.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,elements:[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1;}).slice(0,10).map(el=>({tag:el.tagName,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))})));
    }
  }
  await mobilePage.goto(base+'/',{waitUntil:'networkidle'});
  await mobilePage.screenshot({path:join(artifacts, 'mobile.png'),fullPage:true});
  console.log('Mobile route overflow findings:',JSON.stringify(overflows));
  await mobilePage.getByRole('button',{name:'Открыть меню',exact:true}).click();
  assert.equal(await mobilePage.getByRole('button',{name:'Открыть меню',exact:true}).getAttribute('aria-expanded'),'true');
  await mobilePage.close();
  await mobile.close();

  await go('/recipes/new/');
  await page.locator('[data-add-form="components-formset"]').click();
  assert.equal(await page.locator('#id_components-TOTAL_FORMS').inputValue(),'3');
  await option(page,'#id_mixture_type','DEMO-316');
  await page.locator('#id_version_label').fill('QA-316.0');
  await page.locator('#id_title').fill('QA нулевой компонент и полный состав');
  await page.locator('#id_status').selectOption('published');
  await option(page,'#id_components-0-material','Полиол P-101');
  await page.locator('#id_components-0-quantity_value').fill('0');
  await option(page,'#id_components-1-material','Полиол P-202');
  await page.locator('#id_components-1-quantity_value').fill('100');
  await option(page,'#id_components-2-material','Стабилизатор S-10');
  await page.locator('#id_filling-0-group_label').fill('A');
  await page.locator('#id_filling-0-mass_g').fill('0');
  await submit('Сохранить рецептуру');
  const original = new URL(page.url()).pathname;
  assert.match(original,/^\/recipes\/\d+\/$/);
  assert.equal(await page.locator('h1').innerText(),'QA-316.0');
  assert.match(await page.locator('.panel').filter({has:page.getByRole('heading',{name:'Состав · группа A'})}).locator('tbody tr').first().locator('td').last().innerText(),/^0(?:[,.]0+)?$/);
  assert.equal(await page.locator('.panel').filter({has:page.getByRole('heading',{name:'Состав · группа A'})}).locator('tbody tr').last().locator('td').last().innerText(),'—');
  assert.equal((await page.request.get(base+original+'edit/')).status(),403);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('h1').innerText(),'QA-316.0');

  await go(original+'fork/');
  await page.locator('#id_version_label').fill('QA-316.1');
  await page.locator('#id_title').fill('QA новая версия');
  await page.locator('#id_status').selectOption('draft');
  await submit('Сохранить рецептуру');
  const fork = new URL(page.url()).pathname;
  await go(fork+'edit/');
  await page.locator('#id_title').fill('QA опубликованная новая версия');
  await page.locator('#id_status').selectOption('published');
  await submit('Сохранить изменения');
  assert.equal((await page.request.get(base+fork+'edit/')).status(),403);
  await go(original);
  assert.match(await page.locator('.page-heading').innerText(),/QA нулевой компонент/);

  await go('/tests/new/?recipe='+fork.split('/')[2]);
  await page.locator('#id_identifier').fill('QA-2026-001');
  await page.locator('#id_title').fill('QA нулевое измерение и история');
  await page.locator('#id_status').selectOption('active');
  await page.locator('#id_recorded_tested_on').fill('2026-11-01');
  await option(page,'#id_conditions-0-metric','Температура испытания');
  await page.locator('#id_conditions-0-numeric_value').fill('0');
  await option(page,'#id_conditions-1-metric','Относительная влажность');
  await page.locator('#id_conditions-1-numeric_value').fill('55');
  await submit('Сохранить карточку');
  const card=new URL(page.url()).pathname;
  assert.match(card,/^\/tests\/\d+\/$/);
  assert.match(await page.locator('.results-strip').innerText(),/0 °C/);
  await go(card+'results/new/');
  await option(page,'#id_metric','Выход пены');
  await page.locator('#id_numeric_value').fill('0');
  await submit('Сохранить');
  const resultPanel=page.locator('section.panel').filter({has:page.getByRole('heading',{name:'Основное испытание',exact:true})});
  const resultRow=resultPanel.locator('tbody tr').filter({hasText:'Выход пены'});
  assert.match(await resultRow.locator('td').nth(1).innerText(),/^0(?:[,.]0+)?$/);
  await go(card+'observations/new/');
  await option(page,'#id_run','Основное испытание');
  const verbatim='  Первая строка наблюдения\nВторая строка: без изменений  ';
  await page.locator('#id_text_value').fill(verbatim);
  await submit('Сохранить');
  assert.equal(await page.locator('.observation p').textContent(),verbatim);
  await go(card+'conditioning/new/');
  await page.locator('#id_target_kind').selectOption('can');
  await page.locator('#id_kind').selectOption('conditioning');
  await page.locator('#id_duration_value').fill('0');
  await page.locator('#id_duration_unit').fill('ч');
  await page.locator('#id_temperature_c').fill('0');
  await submit('Сохранить');
  await go(card+'edit/');
  await page.locator('#id_status').selectOption('completed');
  await page.locator('#id_notes').fill('QA карточка завершена после нулевого измерения');
  await submit('Сохранить карточку');
  assert.equal(await page.getByRole('link',{name:'Записать результат',exact:true}).count(),0);
  assert.equal((await page.request.get(base+card+'results/new/')).status(),403);
  assert.equal((await page.request.get(base+card+'edit/')).status(),403);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('.observation p').textContent(),verbatim);
  assert.match(await page.locator('main').innerText(),/QA карточка завершена/);
  await page.locator('details.event-details').first().locator('summary').click();
  assert.doesNotMatch(await page.locator('details.event-details').first().innerText(),/numeric_value|recorded_tested_on|recipe_matches/);
  await go('/history/?q=QA-2026-001');
  assert.match(await page.locator('main').innerText(),/QA-2026-001/);

  // Add documentary evidence through the same author-facing screens as a user.
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA источник зимней пены');
  await page.locator('#id_kind').selectOption('note');
  await page.locator('#id_notes').fill('QA исходное описание\nВторая строка источника');
  await submit('Сохранить');
  const noteSource = new URL(page.url()).pathname;
  await go(noteSource+'sheets/new/');
  await page.locator('#id_name').fill('QA свойства сырья');
  await submit('Сохранить');
  const cellLink = await page.locator('a[href$="/cells/new/"]').first().getAttribute('href');
  await go(cellLink);
  await page.locator('#id_address').fill('B12');
  await page.locator('#id_cell_kind').selectOption('numeric');
  await page.locator('#id_raw_value').fill('  1,05  ');
  await submit('Сохранить');
  assert.ok((await page.locator('main').innerText()).includes('B12'));
  assert.equal(await page.locator('tbody tr').filter({hasText:'B12'}).locator('.preserve-text').first().textContent(),'  1,05  ');
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA ссылка на протокол');
  await page.locator('#id_kind').selectOption('link');
  await page.locator('#id_url').fill('https://example.com/qa-winter-protocol');
  await submit('Сохранить');
  assert.equal(await page.locator('a[target="_blank"]').first().getAttribute('href'),'https://example.com/qa-winter-protocol');
  await go('/sources/new/');
  await page.locator('#id_title').fill('QA паспорт партии');
  await page.locator('#id_kind').selectOption('file');
  const sourceBytes=Buffer.from('QA паспорт зимней партии\nБез импорта таблиц.','utf8');
  const digest=createHash('sha256').update(sourceBytes).digest('hex');
  await page.locator('#id_upload').setInputFiles({name:'qa-winter-passport.txt',mimeType:'text/plain',buffer:sourceBytes});
  await submit('Сохранить');
  const uploadedSource=new URL(page.url()).pathname;
  assert.ok((await page.locator('main').innerText()).includes(digest));
  const download=await page.request.get(base+uploadedSource+'download/');
  assert.equal(download.status(),200);
  assert.equal(createHash('sha256').update(await download.body()).digest('hex'),digest);
  assert.match(download.headers()['content-disposition'],/attachment/);

  // A real lot can be registered, quarantined, released and traced to a plan.
  await go('/materials/new/');
  await page.locator('#id_name').fill('QA зимний полиол');
  await page.locator('#id_category').fill('Полиол');
  await page.locator('#id_supplier').fill('QA поставщик');
  await page.locator('#id_density_g_ml').fill('1.05');
  await page.locator('#id_price_per_kg').fill('200');
  await submit('Сохранить');
  const materialPath=new URL(page.url()).pathname;
  const materialId=materialPath.split('/')[2];
  await go('/lots/new/?material='+materialId);
  await page.locator('#id_lot_code').fill('QA-WINTER-LOT-001');
  await page.locator('#id_status').selectOption('quarantine');
  await page.locator('#id_density_g_ml').fill('1.1');
  await page.locator('#id_source').selectOption(uploadedSource.split('/')[2]);
  await submit('Сохранить');
  const lotPath=new URL(page.url()).pathname;
  const lotId=lotPath.split('/')[2];
  assert.ok((await page.locator('main').innerText()).includes('Карантин'));
  assert.equal(await page.locator(`a[href="${uploadedSource}"]`).count(),1);

  // Fixed template versions can be composed and instantiated through forms.
  await go('/calculation-templates/new/');
  await page.locator('#id_name').fill('QA плотность и стоимость');
  await page.locator('#id_description').fill('QA проверка выбранных расчётных операций');
  await page.locator('[name="operations"][value="volume"]').uncheck();
  await page.locator('[name="operations"][value="volume"]').check();
  await submit('Сохранить шаблон расчёта');
  const calculationTemplatePath=new URL(page.url()).pathname;
  const calculationTemplateId=calculationTemplatePath.split('/')[2];
  assert.ok((await page.locator('main').innerText()).includes('Плотность'));
  await go(calculationTemplatePath+'fork/');
  assert.equal(await page.locator('#id_version').inputValue(),'2');
  await page.locator('#id_description').fill('QA вторая версия расчёта');
  await submit('Сохранить шаблон расчёта');
  const calculationTemplateV2=new URL(page.url()).pathname;
  assert.notEqual(calculationTemplateV2,calculationTemplatePath);
  await go('/recipe-templates/new/');
  await page.locator('#id_name').fill('QA шаблон зимней пены');
  await option(page,'#id_mixture_type','DEMO-316');
  await page.locator('#id_calculation_template').selectOption(calculationTemplateId);
  await page.locator('#id_batch_mass_g').fill('1000');
  await page.locator('#id_components-0-specified_lot').selectOption(lotId);
  await page.locator('#id_components-0-quantity_value').fill('100');
  await page.locator('#id_components-0-completeness').selectOption('complete');
  await submit('Сохранить шаблон рецептуры');
  const recipeTemplatePath=new URL(page.url()).pathname;
  const recipeTemplateId=recipeTemplatePath.split('/')[2];
  await go(recipeTemplatePath+'fork/');
  assert.equal(await page.locator('#id_version').inputValue(),'2');
  await page.locator('#id_description').fill('QA вторая версия шаблона рецептуры');
  await submit('Сохранить шаблон рецептуры');
  assert.notEqual(new URL(page.url()).pathname,recipeTemplatePath);
  await go('/recipes/new/?template='+recipeTemplateId);
  assert.equal(await page.locator('#id_components-0-specified_lot').inputValue(),lotId);
  assert.equal(await page.locator('#id_components-0-quantity_value').inputValue(),'100');
  await page.locator('#id_version_label').fill('QA-WINTER-CALC.1');
  await page.locator('#id_title').fill('QA зимняя пена с расчётом');
  await page.locator('#id_status').selectOption('published');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:'Сохранить рецептуру',exact:true}).click()]);
  assert.match((await page.locator('.errorlist,.field-error').allTextContents()).join(' '),/Партия.*недоступна/);
  await page.locator('#id_status').selectOption('draft');
  await submit('Сохранить рецептуру');
  const calculatedRecipePath=new URL(page.url()).pathname;
  await go(lotPath+'edit/');
  await page.locator('#id_status').selectOption('active');
  await submit('Сохранить');
  await go(calculatedRecipePath+'edit/');
  await page.locator('#id_status').selectOption('published');
  await submit('Сохранить изменения');
  const cost=async()=>page.locator('.calculation-group').first().locator('.result-item').filter({hasText:'Стоимость группы'}).locator('strong').innerText();
  assert.match(await cost(),/^200\s*₽$/);
  const baselineCost=await cost();
  const baselineCalculation=await page.locator('a[href^="/calculations/"]').first().getAttribute('href');
  assert.ok(baselineCalculation);
  await go(lotPath);
  assert.equal(await page.locator(`a[href="${calculatedRecipePath}"]`).count(),1);
  await go(materialPath+'edit/');
  await page.locator('#id_price_per_kg').fill('400');
  await submit('Сохранить');
  await go(calculatedRecipePath);
  assert.equal(await cost(),baselineCost,'published baseline calculation changed after catalogue edit');
  await go(baselineCalculation);
  assert.equal(await cost(),baselineCost,'saved record changed after catalogue edit');
  await go(calculatedRecipePath);
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.locator(`form[action="${calculatedRecipePath}calculate/"] button`).click()]);
  assert.equal(await cost(),baselineCost,'explicit recalculation replaced original baseline');
  const calculationLinks=await page.locator('a[href^="/calculations/"]').evaluateAll(items=>[...new Set(items.map(item=>new URL(item.href).pathname))]);
  const newCalculation=calculationLinks.find(path=>path!==baselineCalculation);
  assert.ok(newCalculation,'new recalculation record not linked');
  await go(newCalculation);
  assert.match(await cost(),/^400\s*₽$/);
  await go(baselineCalculation);
  assert.equal(await cost(),baselineCost);
  await go('/history/?entity=source&q=QA');
  const historyTarget=page.locator('.event-meta a').first();
  assert.ok((await historyTarget.getAttribute('href')).startsWith('/sources/'));
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),historyTarget.click()]);
  assert.ok(new URL(page.url()).pathname.startsWith('/sources/'));
  console.log('Sources, uploads/SHA, provenance, materials/lots, quarantine, recipe/calculation templates and immutable recalculation all passed.');

  await go('/employees/');
  await option(page,'#id_employee','Елена Морозова');
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.getByRole('button',{name:/Открыть лабораторию/}).click()]);
  await go('/recipes/');
  assert.equal(await page.locator('a[href="/recipes/new/"]').count(),0);
  await go('/tests/6/');
  for(const word of ['Редактировать','Записать результат','Добавить наблюдение','Записать обработку']) assert.equal(await page.getByRole('link',{name:new RegExp(word)}).count(),0,`Reader controls: ${word}`);
  const csrf=await page.locator('[name="csrfmiddlewaretoken"]').first().inputValue();
  for(const path of ['/recipes/new/','/tests/new/','/tests/6/results/new/','/tests/6/observations/new/','/tests/6/conditioning/new/',
    '/sources/new/','/materials/new/','/lots/new/','/metrics/new/','/mixtures/new/','/recipe-templates/new/',
    '/calculation-templates/new/',calculatedRecipePath+'calculate/',uploadedSource+'sheets/new/']){
    const response=await page.request.post(base+path,{form:{csrfmiddlewaretoken:csrf},headers:{Referer:base+'/tests/6/'}});
    assert.equal(response.status(),403,`Reader direct POST ${path}`);
    assert.match(await response.text(),/читатель/);
  }
  await go(uploadedSource);
  assert.equal(await page.locator(`a[href="${uploadedSource}edit/"]`).count(),0);
  assert.equal((await page.request.get(base+uploadedSource+'download/')).status(),200);
  await page.reload({waitUntil:'networkidle'});
  assert.match(await page.locator('.profile').innerText(),/Елена Морозова/);
  assert.deepEqual(errors,[]);
  assert.deepEqual(overflows,[],`Mobile overflow: ${overflows.join(', ')}`);
  console.log(`PASS: isolated demo DB; login, author/reader roles, direct POST denial, 30 desktop/mobile routes, documentary sources and lots, fixed recipe/calculation templates, immutable saved calculations, immutable recipes, fork/edit/publish, zero and missing values, card/result/observation/treatment creation, completion locking, reload persistence, audit search. Screenshots: ${artifacts}`);
} catch(error) {
  if (!interrupted) {
    console.error(error);
    if(logs.includes('Traceback')) console.error(logs.slice(-10000));
    process.exitCode = 1;
  }
} finally {
  await cleanup();
  for (const [signal, handler] of signalHandlers) process.off(signal, handler);
}
