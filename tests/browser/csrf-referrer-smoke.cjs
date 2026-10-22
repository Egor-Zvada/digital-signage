const assert = require('node:assert/strict');
const http = require('node:http');
const {chromium} = require('playwright');
let origin;
const seen = [];
const server = http.createServer((request,response) => {
  if (request.method === 'POST') {
    seen.push({path:request.url,origin:request.headers.origin,referer:request.headers.referer});
    response.end('Saved'); return;
  }
  response.setHeader('Referrer-Policy',request.url.startsWith('/old') ? 'no-referrer' : 'same-origin');
  response.setHeader('Content-Type','text/html; charset=utf-8');
  response.end('<!doctype html><form method="post"><input name="csrfmiddlewaretoken" value="test-token"><button>Save</button></form>');
});
(async () => {
  await new Promise((resolve) => server.listen(0,'127.0.0.1',resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless:true,executablePath:process.env.SIGNAGE_TEST_BROWSER || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'});
  try {
    const page = await browser.newPage();
    for (const route of ['/old-screen/','/fixed-screen/']) {
      await page.goto(origin+route);
      await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Save'}).click()]);
    }
    assert.equal(seen[0].origin,'null','Original policy makes the form origin opaque');
    assert.equal(seen[0].referer,undefined);
    assert.equal(seen[1].origin,origin);
    assert.equal(seen[1].referer,origin+'/fixed-screen/');
    console.log('PASS: reproduced Origin:null with old policy; fixed policy preserves same-origin CSRF headers');
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
})().catch((error) => {console.error(error);server.close();process.exitCode=1;});
