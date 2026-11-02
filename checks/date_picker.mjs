import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1000, height: 800}});
  const errors = [];
  page.on('pageerror', error => errors.push(error));
  const fixture = `<html lang="ru"><head><meta charset="utf-8"></head><body class="amai-ui"><form style="width:400px;margin:40px">
    <label for="date">Дата испытания</label><input type="date" id="date" name="tested_on" value="2026-11-03" required min="2026-01-01" max="2027-12-31">
    <label for="other-date">Дата изготовления</label><input type="date" id="other-date" name="manufactured_on">
    <button type="reset">Сбросить</button><button type="submit">Сохранить</button></form></body></html>`;
  const styles = ['amai-theme.css', 'amai-components.css', 'app.css', 'date-picker.css']
    .map(file => `<style>${readFileSync(new URL(`../lab/static/lab/${file}`, import.meta.url), 'utf8')}</style>`).join('');
  const script = readFileSync(new URL('../lab/static/lab/date-picker.js', import.meta.url), 'utf8');
  await page.route('http://date-picker.test/**', route => route.fulfill({
    contentType: 'text/html', body: fixture.replace('</body>', `${styles}<script>${script}</script></body>`),
  }));
  await page.goto('http://date-picker.test/tests/new/?recipe=135');
  const input = page.getByLabel('Дата испытания', {exact: true});
  const trigger = page.getByRole('button', {name: 'Открыть календарь: Дата испытания'});
  const popup = page.getByRole('dialog');
  assert.equal(await input.inputValue(), '03.11.2026');
  await trigger.click();
  await popup.waitFor({state: 'visible'});
  assert.equal(await popup.getByRole('button', {name: 'Выбрать месяц', exact: true}).innerText(), 'Ноябрь');
  assert.equal(await popup.getByRole('button', {name: 'Выбрать год', exact: true}).innerText(), '2026');
  assert.equal(await popup.locator('.date-picker-weekdays').innerText(), 'Пн\nВт\nСр\nЧт\nПт\nСб\nВс');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Enter');
  assert.equal(await input.inputValue(), '04.11.2026');
  assert.equal(await page.evaluate(() => new FormData(document.querySelector('form')).get('tested_on')), '2026-11-04');
  await input.fill('31.02.2026');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  await input.fill('01.01.2025');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  await input.fill('29.02.2024');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  await input.fill('28.02.2026');
  assert.equal(await input.evaluate(node => node.checkValidity()), true);
  await trigger.click();
  await page.keyboard.press('Escape');
  assert.equal(await popup.isVisible(), false);
  await trigger.click();
  await popup.getByRole('button', {name: 'Очистить дату'}).click();
  assert.equal(await input.inputValue(), '__.__.____');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  await page.getByRole('button', {name: 'Сбросить'}).click();
  await page.waitForTimeout(30);
  assert.equal(await input.inputValue(), '03.11.2026');
  // Header arrows are centered independently of font metrics.
  await trigger.click();
  for (const arrow of await popup.locator('.date-picker-nav').all()) {
    const outer = await arrow.boundingBox();
    const inner = await arrow.locator('svg').boundingBox();
    const shape = await arrow.locator('path').evaluate(node => { const box = node.getBBox(); return {x: box.x, y: box.y, width: box.width, height: box.height}; });
    assert.equal(shape.x + shape.width / 2, 12);
    assert.equal(shape.y + shape.height / 2, 12);
    assert.ok(Math.abs(outer.x + outer.width / 2 - inner.x - inner.width / 2) < .5);
    assert.ok(Math.abs(outer.y + outer.height / 2 - inner.y - inner.height / 2) < .5);
  }
  await popup.getByRole('button', {name: 'Выбрать месяц', exact: true}).click();
  assert.equal(await popup.locator('.date-picker-choice').count(), 12);
  await popup.getByRole('button', {name: 'февраль', exact: true}).click();
  assert.equal(await popup.getByRole('button', {name: 'Выбрать месяц', exact: true}).innerText(), 'Февраль');
  await popup.getByRole('button', {name: 'Выбрать год', exact: true}).click();
  await popup.getByRole('button', {name: '2027', exact: true}).click();
  await popup.locator('[data-date="2027-02-15"]').click();
  assert.equal(await input.inputValue(), '15.02.2027');
  assert.equal(await page.evaluate(() => new FormData(document.querySelector('form')).get('tested_on')), '2027-02-15');
  await input.fill('');
  assert.equal(await input.inputValue(), '__.__.____');
  await input.click();
  await input.pressSequentially('03112026');
  assert.equal(await input.inputValue(), '03.11.2026');
  await input.press('Backspace');
  assert.equal(await input.inputValue(), '03.11.202_');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  await input.pressSequentially('7');
  assert.equal(await input.inputValue(), '03.11.2027');
  await input.evaluate(node => node.setSelectionRange(3, 5));
  await input.pressSequentially('09');
  assert.equal(await input.inputValue(), '03.09.2027');
  await input.evaluate(node => node.setSelectionRange(0, 10));
  await input.evaluate(node => {
    const clipboardData = new DataTransfer(); clipboardData.setData('text', '25.12.2026');
    node.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
  });
  assert.equal(await input.inputValue(), '25.12.2026');
  await input.press('ControlOrMeta+a');
  await input.press('Backspace');
  assert.equal(await input.inputValue(), '__.__.____');
  await input.pressSequentially('abc');
  assert.equal(await input.inputValue(), '__.__.____');
  await input.pressSequentially('01012027');
  assert.equal(await page.evaluate(() => new FormData(document.querySelector('form')).get('tested_on')), '2027-01-01');
  // Near the placement threshold, a shorter month/year view must not flip sides.
  await input.evaluate(node => node.closest('form').style.cssText = 'position:fixed;width:300px;left:120px;top:400px;margin:0');
  await trigger.click();
  const anchoredBox = await popup.boundingBox();
  const originalScroll = await page.evaluate(() => window.scrollY);
  const assertAnchored = async () => {
    assert.deepEqual(await popup.boundingBox(), anchoredBox, 'Calendar keeps its position and size between views');
    assert.equal(await page.evaluate(() => window.scrollY), originalScroll, 'Changing views does not scroll the page');
  };
  await popup.getByRole('button', {name: 'Выбрать месяц', exact: true}).click();
  await assertAnchored();
  await popup.getByRole('button', {name: 'февраль', exact: true}).click();
  await assertAnchored();
  await popup.getByRole('button', {name: 'Выбрать год', exact: true}).click();
  await assertAnchored();
  await popup.getByRole('button', {name: '2027', exact: true}).click();
  await assertAnchored();
  await page.keyboard.press('Escape');
  await page.setViewportSize({width: 375, height: 650});
  await input.evaluate(node => node.closest('form').style.cssText = 'width:300px;margin:8px');
  await trigger.click();
  const box = await popup.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 375);
  await page.screenshot({path: '/tmp/lims-date-picker.png'});
  await page.getByRole('button', {name: 'Сохранить', exact: true}).focus();
  assert.equal(await popup.isVisible(), false);
  const other = page.getByLabel('Дата изготовления', {exact: true});
  await input.fill('12.0');
  await other.fill('2512');
  await page.reload();
  assert.equal(await input.inputValue(), '12.0_.____');
  assert.equal(await other.inputValue(), '25.12.____');
  assert.equal(await input.evaluate(node => node.checkValidity()), false);
  assert.equal(await page.evaluate(() => new FormData(document.querySelector('form')).get('tested_on')), '');
  await page.goto('http://date-picker.test/tests/new/?recipe=136');
  assert.equal(await input.inputValue(), '03.11.2026', 'Another recipe has its own date draft');
  await page.goto('http://date-picker.test/tests/new/?recipe=135');
  assert.equal(await input.inputValue(), '12.0_.____');
  // Invalid submission keeps the draft, since no submit event is fired.
  await page.getByRole('button', {name: 'Сохранить', exact: true}).click();
  await page.reload();
  assert.equal(await input.inputValue(), '12.0_.____');
  await page.getByRole('button', {name: 'Сбросить'}).click();
  await page.waitForTimeout(30);
  await page.reload();
  assert.equal(await input.inputValue(), '03.11.2026');
  assert.equal(await other.inputValue(), '__.__.____');
  await input.fill('15112026');
  await page.reload();
  assert.equal(await input.inputValue(), '15.11.2026');
  await page.evaluate(() => {
    const form = document.querySelector('form');
    form.addEventListener('submit', event => event.preventDefault(), {once: true});
    form.requestSubmit();
  });
  await page.reload();
  assert.equal(await input.inputValue(), '03.11.2026', 'A submitted form clears its date draft');
  assert.deepEqual(errors, []);
  console.log('Date picker: localized input, ISO submission, centered arrows, month/year selection, input mask editing/paste, calendar keyboard selection, validation, draft reload/isolation/cleanup, reset, dismissal and mobile bounds passed.');
} finally {
  await browser.close();
}
