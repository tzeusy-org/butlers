# Off-Node Backup Copy

> **Purpose:** Copy the nightly database dumps off the k3s node into an encrypted restic
> repository on the owner's second always-on tailnet device, and show when that copy fails or
> goes stale.
> **Audience:** The owner/operator. Every live step below needs the owner's approval first.
> **Decision:** The owner chose this on 2026-10-20 (bead `bu-w9z7me`, choice A). The work is
> tracked on `bu-ivuf9u`.

---

## Why

The backup CronJob writes dumps to the `butlers-backups` PVC
([Backup and Restore](backup-restore.md)). On single-node k3s with `local-path` storage, that PVC
is on the same disk as the database. If the node's storage is lost, the data and its dumps are
lost together. This copy keeps a second, encrypted copy on another device.

## What ships in the chart

The chart ships the copy **disabled** (`offsiteCopy.enabled: false`). Nothing runs, and nothing
contacts any device, until the owner completes the steps below.

| Piece | Where |
|---|---|
| Copy script | `deploy/backup/restic_offsite_copy.sh` (helm `files/` symlink) |
| CronJob | `<release>-offsite-copy`, `templates/cronjobs.yaml`, image `restic/restic`, 03:30 UTC daily |
| Values | `offsiteCopy.*` in `deploy/helm/butlers/values.yaml` |
| Credentials | An existing Secret named by `offsiteCopy.secretName`. The chart never creates it. |
| Status receipt | `offsite_last_run.json` in the backups PVC |
| Status surface | `GET /api/system/backups` → `offsite_copy`; the System page Backups tile ("Off-node copy" row) and verdict banner |

How it works:

- **Transport.** restic's SFTP backend runs over SSH to `offsiteCopy.host`, through the node's
  tailnet route. ssh runs with `BatchMode=yes` and `StrictHostKeyChecking=yes` against a pinned
  `known_hosts`, so it refuses any host key the owner did not record.
- **Encryption.** restic encrypts the repository with the repository password before any data
  leaves the pod. The device stores only ciphertext.
- **What is copied.** The whole dump directory: every `butlers_*.sql.gz` and `last_run.json`.
  The off-node receipt and temp files are excluded. restic deduplicates, so each night uploads
  only the new dump.
- **Retention.** After each backup, `restic forget --prune` keeps 14 daily, 8 weekly and 6 monthly
  snapshots (`offsiteCopy.keep`). The local PVC keeps only 14 days, so the device holds the longer
  history.
- **First run.** The script never initializes a repository unless `offsiteCopy.initRepository`
  is `true`. Without it, a missing repository fails as `repo_unreachable`.

### Secret keys

| Key | Content |
|---|---|
| `restic-password` | The repository password |
| `ssh-private-key` | A dedicated private key for `offsiteCopy.user` on the device |
| `known-hosts` | The device's pinned host key line(s) |

The Secret is mounted as files with mode `0400`. No value is passed as an environment variable,
and the receipt holds no command output.

### How failure shows

The script rewrites `offsite_last_run.json` after every run, including runs that fail. The chart
sets `BUTLERS_OFFSITE_COPY_ENABLED=1` on the dashboard API only when the CronJob exists.

| `offsite_copy.result` | Meaning | Shown as |
|---|---|---|
| `disabled` | No copy job in this deployment | "Not configured"; no banner clause |
| `success` | The last run copied and pruned | "Copied", with the time and snapshot id |
| `failed` | The last run failed. `reason` is one of `config_missing`, `no_dumps`, `repo_unreachable`, `repo_init_failed`, `backup_failed`, `retention_failed`, `unexpected_error` | Red row; banner says "off-node backup copy failed" |
| `unknown` | Enabled, but no readable receipt yet | "No copy recorded"; banner says "off-node backup copy is stale" |

`stale` is `true` when an enabled copy has reported no run in 36 hours, whatever that run's
result. A stale copy shows a "Stale" row and a banner clause.

The QA `infra_state` source does not raise findings for this copy. In k8s the dashboard API is the
only workload with the backups volume, so the System page is where this status shows.

## Owner-gated steps

Each step needs separate owner approval, recorded on `bu-ivuf9u` before the step runs. Agents
prepare and review. They do not run these steps. No account is created and no money is spent.

### 1. Name the device

The owner names the device and the repository path, and the name is recorded on the bead. Use the
device's tailnet IP (`100.x.y.z`), or a name that resolves from **inside a pod**. MagicDNS names
often do not resolve from pods, because CoreDNS forwards to the node's upstream resolver.

Keep the host out of git. Set it in the gitignored `values.local.yaml`:

```yaml
offsiteCopy:
  host: 100.x.y.z            # owner-supplied
  repoPath: /srv/restic/butlers
  secretName: butlers-offsite-copy
```

### 2. Prepare the device (software and configuration)

This step installs or configures software on the device, so it needs its own approval. The SFTP
backend needs only an SSH server with SFTP on the device. restic itself is needed there only for
later verification or restore.

- Create a dedicated user (default `restic`) that owns `repoPath`.
- Add the copy job's **public** key to that user's `authorized_keys`, restricted to SFTP, for
  example: `restrict,command="internal-sftp" ssh-ed25519 AAAA... butlers-offsite-copy`.
- Make sure the disk has room for about 2–3× the dump directory.

### 3. Confirm pods can reach the device

This creates a temporary pod in `butlers-dev`, which is a cluster write. From a throwaway pod,
check that the device's SSH port answers through the node's tailnet route, for example with
`nc -zv <host> 22`. Delete the pod afterwards. If the port does not answer, fix routing first
(the node must be on the tailnet, and the device's ACLs must allow the node).

### 4. Create the secret material

This sets a password and a transport key, so it needs its own approval. Follow the host's secret
rules: values never enter a terminal transcript, the repo, or a log.

- Generate the SSH key pair and the restic password in a `umask 077` scratch directory outside
  the repo.
- Get the host key with `ssh-keyscan -t ed25519 <host>`. Check its fingerprint against the
  device's own `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
- **Store the restic password in the owner's password manager too.** If the cluster is lost, the
  Secret is lost with it, and without the password the repository cannot be decrypted.
- Create the Secret from files without printing them:
  `kubectl -n butlers-dev create secret generic butlers-offsite-copy --from-file=restic-password=… --from-file=ssh-private-key=… --from-file=known-hosts=…`.
- Delete the scratch files.

### 5. Enable the copy and run the first copy

Deploying and running the first copy each need approval.

- Deploy with `offsiteCopy.enabled: true` and `offsiteCopy.initRepository: true` (for example in
  `values.local.yaml`, then `make deploy-dev`).
- Start the first run now, rather than waiting for the schedule:
  `kubectl -n butlers-dev create job --from=cronjob/butlers-offsite-copy butlers-offsite-copy-first`.
- Check the job log for `[offsite] done: snapshot <id>`.
- Deploy again with `initRepository: false`, so that a later empty repository fails loudly instead
  of being silently re-created.

### 6. Verify the first copy

Record only the snapshot id and size on the bead. The System page Backups tile shows the snapshot
id. On the device, as the `restic` user, with the password supplied from the password manager (not
typed into a recorded shell):

```bash
restic -r /srv/restic/butlers snapshots --compact --latest 1
restic -r /srv/restic/butlers stats latest
```

### 7. Restore (not part of this work)

No restore is run as part of this work. A restore, or a restore drill from the off-node copy,
needs its own owner approval and its own bead. Restore-drill evidence stays with `bu-lw18o`.

## Disabling

Set `offsiteCopy.enabled: false` and deploy. The CronJob and the dashboard flag are removed, and
the tile reads "Not configured". The repository and the Secret stay until the owner removes them.
