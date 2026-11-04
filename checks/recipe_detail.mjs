import assert from 'node:assert/strict';
import {join} from 'node:path';

async function checkReadonlyControls(page) {
  const form = page.locator('form[data-recipe-readonly]');
  assert.equal(await form.count(), 1);
  assert.equal(await form.locator('input[name]:not([type=hidden]), select, textarea, .search-select-trigger')
    .evaluateAll(fields => fields.every(field => field.disabled)), true);
  assert.equal(await form.locator('[data-add-form], [data-remove-form], button[type=submit]')
    .count(), 0);
  const source = JSON.parse(await page.locator('#recipe-calculation-data').textContent());
  for (const [group, rows] of [['polyol', source.polyol.rows], ['gases', source.gases.rows]]) {
    assert.equal(await page.locator(`#${group}-formset .formset-rows > .formset-row`)
      .count(), rows.length);
  }
  assert.equal(Number(await page.locator('[data-recipe-output="filling.mass_g"]')
    .getAttribute('data-recipe-raw-value')), 790.9);
  assert.equal(await page.getByRole('heading', {name: 'Заправка одного баллона', exact: true})
    .count(), 1);
}

async function checkReadonlyLayout(page, artifacts) {
  await page.getByRole('button', {name: 'На весь экран', exact: true}).click();
  assert.equal(await page.locator('.sidebar').isVisible(), false);
  const row = page.locator('#polyol-formset .formset-rows > .formset-row').first();
  await row.locator('details > summary').click();
  assert.equal(await row.locator('.recipe-inspector').isVisible(), true);
  assert.equal(await row.getByRole('link', {name: 'Карточка материала ↗'}).count(), 1);
  await page.keyboard.press('Escape');
  assert.equal(await row.locator('details').getAttribute('open'), null);
  assert.equal(await page.locator('[data-recipe-expand]').getAttribute('aria-pressed'), 'true');
  await page.screenshot({path: join(artifacts, 'recipe-detail-desktop.png')});
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.sidebar').isVisible(), true);
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({path: join(artifacts, 'recipe-detail-mobile.png'), fullPage: true});
  await page.setViewportSize({width: 1440, height: 1000});
}

export async function checkReadonlyRecipeDetail(page, base, recipe, artifacts) {
  const previews = [];
  const onRequest = request => {
    if (request.url().includes('/preview/')) previews.push(request.url());
  };
  page.on('request', onRequest);
  await page.goto(`${base}/recipes/${recipe}/`, {waitUntil: 'networkidle'});
  await checkReadonlyControls(page);
  await page.locator('form[data-recipe-readonly]').evaluate(form => {
    form.dispatchEvent(new Event('input', {bubbles: true}));
    form.dispatchEvent(new Event('change', {bubbles: true}));
    form.dispatchEvent(new Event('formsetchange'));
    const submit = new Event('submit', {cancelable: true});
    if (form.dispatchEvent(submit)) throw new Error('Readonly form permits submission');
  });
  await checkReadonlyLayout(page, artifacts);
  assert.deepEqual(previews, [], 'Viewing a recipe never posts a live preview');
  page.off('request', onRequest);
}
