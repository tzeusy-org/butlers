'use strict';
const control = require('../ci-shard-watchdog-capture/dist/index.js');
if (process.env.WATCHDOG_UPLOADER === '1') control.retentionUploaderMain();
else control.runRetentionControl().then(code => {process.exitCode=code;}).catch(() => {
  process.stderr.write('watchdog retention control unavailable: UNKNOWN\n');process.exitCode=2;
});
