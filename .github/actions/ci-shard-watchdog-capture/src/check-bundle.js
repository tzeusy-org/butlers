'use strict';
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-bundle-'));
try {
  const result = spawnSync(process.execPath, ['node_modules/@vercel/ncc/dist/ncc/cli.js',
    'build', 'src/index.js', '--out', temporary, '--license', 'licenses.txt', '--minify'], {stdio: 'ignore', timeout: 300000});
  if (result.status !== 0) throw new Error('bundle-build-refused');
  fs.copyFileSync('src/supervisor.py', path.join(temporary, 'supervisor.py'));
  for (const name of fs.readdirSync(temporary)) {
    if (!fs.readFileSync(path.join(temporary, name)).equals(fs.readFileSync(path.join('dist', name))))
      throw new Error('bundle-not-reproducible');
  }
  if (fs.readdirSync(temporary).sort().join() !== fs.readdirSync('dist').sort().join()) throw new Error('bundle-members-differ');
  process.stdout.write('locked generated bundle matches\n');
} catch (_) { process.stderr.write('locked generated bundle refused\n'); process.exitCode = 2; }
finally { fs.rmSync(temporary, {recursive: true, force: true}); }
