import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(import.meta.url)
const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'))
const version = require('@playwright/test/package.json').version
const packagePaths = {}
for (const name of ['@playwright/test', 'playwright', 'playwright-core']) {
  packagePaths[name] = require.resolve(`${name}/package.json`)
  assert.equal(require(packagePaths[name]).version, version, `Installed ${name} differs`)
  assert.equal(lock.packages[`node_modules/${name}`].version, version, `Locked ${name} differs`)
}

if (process.argv.length === 2) {
  console.log(version)
} else {
  assert.deepEqual(process.argv.slice(2), ['--verify-browser'])
  const browsers = JSON.parse(readFileSync(
    path.join(path.dirname(packagePaths['playwright-core']), 'browsers.json'), 'utf8',
  ))
  const expected = browsers.browsers.find(browser => browser.name === 'chromium').browserVersion
  const browser = await require('@playwright/test').chromium.launch({ headless: true })
  try {
    assert.equal(browser.version(), expected, 'Cached Chromium differs from the locked browser')
  } finally {
    await browser.close()
  }
}
