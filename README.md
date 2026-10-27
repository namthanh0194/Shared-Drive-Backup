# Shared Drive Backup · aaPanel

Plugin độc lập backup website, database MySQL và thư mục lên **Bộ nhớ dùng chung Google Drive**. Có UI duyệt thư mục thật, tích hợp native Cron Job và cập nhật bằng release có chữ ký.

## Thư mục

- DEV: `/root/DEV/aapanel-shared-drive-backup`.
- Production: `/www/server/panel/plugin/shared_drive_backup`.
- `data/`: root-only config, credentials, history, verified downloads, integration snapshots, rollback code.
- `tmp/`: tests, browser tooling, generated release archives and temporary backup archives. Never committed or shipped.
- `logs/`: worker logs, root-only. `.gitignore` excludes runtime data and secrets; `.ignore` records package exclusions. Packaging additionally uses a strict file allowlist.

## Cài đặt

Requires root, Linux aaPanel with `class_v2/crontab_v2.py`, Python >=3.10 in aaPanel pyenv, GNU tar/gzip and aaPanel MySQL mysqldump for database jobs. The adapter is tested against this VPS's aaPanel 8.0.7 code; other releases are accepted only when the expected source contexts match exactly. Unknown versions fail closed.

```sh
bash /root/DEV/aapanel-shared-drive-backup/install.sh install
```

Other VPS: import the signed release ZIP via aaPanel Third-party Plug-ins → Import plugins, or extract the `shared_drive_backup/` directory and run `bash install.sh install` as root. Verify the initial package/signing-key fingerprint through a trusted channel. Missing Python libraries install into plugin `data/vendor/`, not into aaPanel's global environment. Do not import the entire DEV tree.

The modern panel hardcodes its list of backup providers. Installer records original Cron source and hashes under private `data/`, adds a provider to `data/libList.conf`, and narrowly patches provider discovery and dispatch. It does **not** edit minified JS or the old Google Drive plugin. Reload/restart **aaPanel only** when installer reports `restart_panel_required`; do not restart OLS/MySQL/websites. After a panel upgrade, open the plugin's Cron tab and use the repair button; incompatible source is rejected instead of guessed.

## Google authentication

Enable Google Drive API in your Google Cloud project. Never share a private key/token in chat, Git or screenshots.

### Service account (recommended for unattended Shared-drive backups)

Create a service account in your project and grant its email access to the intended Shared drive/folder, subject to Workspace external-sharing policy. Contributor permissions suffice for creating backups; trashing old files requires additional permissions. The plugin checks capabilities when selecting a destination and defaults retention deletion OFF.

On HTTPS aaPanel, select the service-account JSON in **Kết nối & nơi lưu** and save. On the current plain-HTTP panel, import by SSH instead:

```sh
chmod 600 /safe/path/credentials.json
/www/server/panel/pyenv/bin/python -B \
  /www/server/panel/plugin/shared_drive_backup/cli.py \
  import-credentials /safe/path/credentials.json
```

No automatic reuse of credentials from the existing Google Drive plugin.

### OAuth user account

Use your own Google OAuth **Desktop** client. On your trusted personal computer with a browser and google-auth-oauthlib installed:

```sh
python tools/oauth_desktop.py /safe/path/desktop-client.json /safe/path/authorized-user.json
```

Transfer the generated `authorized_user` file securely and import as above. This uses loopback OAuth on the personal computer, not a public HTTP-IP callback. Scope is `https://www.googleapis.com/auth/drive` to browse existing Shared-drive folders; this grants broad Drive access for the authenticated user. Consider a dedicated backup account/service account. Workspace admin policies or OAuth app testing/token expiration can prevent long-term unattended access.

## Choose the destination

1. Open **Kết nối & nơi lưu → Duyệt & chọn thư mục**.
2. Select the Shared drive (or enter its ID), open subfolders, use breadcrumbs or Folder ID, optionally create a new folder.
3. Click **Lưu vào thư mục này**. Browsing/canceling does not change the saved destination. The server verifies drive membership, folder type and `canAddChildren`.
4. Run connection check, then a small manual backup and **Tải kiểm tra** to verify end-to-end.

Remote layout: selected folder → `VPS-<stable server ID>` → `<kind>-<source/destination/task hash>` → timestamped archive. Each native Cron schedule has its own namespace; manual runs are separate from Cron retention. Each VPS generates its own ID. Never clone production `data/` onto another VPS, or both could share retention ownership.

## Cron Job

aaPanel → Cron Job → Backup Site / Backup Database / Backup Directory → **Backup to: Shared Drive Backup**. Set source, schedule, exclusion rules and retain count there. The worker reads the saved Cron row by ID, validates target/type, uses the plugin's destination, and exits nonzero on failure. `ALL`/multi-source Site and MySQL jobs run sources sequentially. There is no second scheduler in the plugin. The plugin's Cron tab lists only its own native schedules and links to the panel's scheduler.

Regular daily/weekly/monthly schedules and the panel's existing time gates are preserved. Only local MySQL databases are listed; remote servers are not backed up using the local root credential. Second-level schedules, full-server backups, PostgreSQL/Redis/MongoDB and split-table database modes are not supported in v1; do not select them for this provider. Cron notification configuration remains visible in aaPanel, but this independent worker does not implement the panel's per-channel notification callbacks; use native execution logs/status until notification support is explicitly added.

Backup exclusions are tar patterns, not shell commands. The source must be a real directory, not `/`, virtual filesystems, or a tree containing the plugin/runtime output. Tar does not follow symlinks or cross filesystem boundaries. MySQL dumps use a temporary 0600 defaults file and `--single-transaction`; nontransactional tables may still require maintenance/locking for consistent snapshots. No database password is passed in argv or written into `/etc/my.cnf`.

## Safety and restore

- One operation per VPS; no backup/update/download overlap.
- Preflight checks disk space; ongoing archive creation checks free space and output size. Estimates are conservative.
- Resumable upload with bounded retries; a fresh remote metadata read checks MD5, size and parent before success.
- Retention disabled by default. When enabled, only matching app-owned files in the current VPS/source/destination/schedule namespace are moved to Trash, after a new verified upload. A file receives its verified marker only after checksum checks; unverified uploads do not consume retention slots. Metadata is read again before trashing. Unrelated Drive files are never pruned. Unverified remote orphans are left for manual review.
- Upload failures keep completed local archives in production `tmp/`. Review disk usage and remove failed archives only after diagnosis; there is no silent deletion of the only local copy.
- **Tải kiểm tra** downloads into private `data/downloads/`, verifies MD5 and SHA-256, and displays the server path. It never extracts or restores into production. Verified downloads consume local disk until manually removed.
- History/logs persist across code updates. Review growth periodically; no automatic history purge in v1.

## Versioning / updates / GitHub

Pinned repository: `namthanh0194/aapanel-shared-drive-backup`, private by default. Each VPS needs a read-only Contents token for this repo. Store through HTTPS UI or import from a root-only file:

```sh
/www/server/panel/pyenv/bin/python -B \
  /www/server/panel/plugin/shared_drive_backup/cli.py \
  import-github-token /safe/path/github-token.txt
```

Check updates → inspect changelog → Update. The updater accepts only stable semver releases from the pinned repository with both `shared_drive_backup-X.Y.Z.zip` and `.zip.sig`, verifies Ed25519 against bundled `sdb/release_key.pub`, validates archive paths/sizes and Python syntax, preserves data/logs/config, and keeps the previous code for rollback. A release cannot replace its own trusted key. Close/reopen the plugin after updating to refresh UI; backend API runs in a fresh subprocess per request.

Before changing live files, deployment prepares a durable code snapshot and records a private recovery journal. Handled errors and SIGINT restore code before clearing it. SIGKILL/power loss cannot run a Python rollback handler; snapshots/journal remain, and backup/new updates are blocked pending recovery. Run the standalone stdlib-only helper from the trusted DEV checkout (or the installed tool if intact):

```sh
/www/server/panel/pyenv/bin/python -B \
  /root/DEV/aapanel-shared-drive-backup/tools/recover_deployment.py
```

This restores the recorded previous code/adapter without touching credentials or backup data. Preserve snapshots until checked. A first installation interrupted before a previous code snapshot exists requires checking/unregistering the recorded adapter and rerunning the trusted installer; do not delete its recovery journal blindly.

For maintainers: bump `sdb/__init__.py` and `info.json` together, update CHANGELOG, run checks, then package using the protected Ed25519 signing key. Losing this key prevents future updates under the old trust root. Back it up separately and never commit/publish it.

```sh
/www/server/panel/pyenv/bin/python -B tools/package_release.py --key /safe/path/signing-key.pem
```

Artifacts live in `tmp/dist/`. To create the private repository/push a clean committed main/publish a draft then finalized release, provide an authorized `GH_TOKEN`/`GITHUB_TOKEN` in your secure shell environment and run:

```sh
/www/server/panel/pyenv/bin/python -B tools/publish_release.py --create-repository --publish
```

This tool requires repo creation/push/release permission, distinct from the read-only token used by VPS updaters. It never reads Codex credentials. Without a publishing token the tool fails without creating anything; local packaging is not proof of a GitHub release.

## Validation

Tests live only in `tmp/` per project policy and are not committed or packaged. On the development VPS:

```sh
TMPDIR="$PWD/tmp" /www/server/panel/pyenv/bin/python -B -m unittest discover -s tmp -p 'test_*.py' -v
node --check ui.js
```

Use a small disposable dataset for the first real backup/restore verification. UI tests with mocked Google responses are not evidence of actual Workspace permissions or live Google upload. Never enable production schedules before that check.

## Uninstall

Disable/retarget the plugin's own Cron jobs first. Back up credentials/config/history separately if required. `bash uninstall.sh` restores only the exact recorded core version and removes only this provider's metadata; it refuses to overwrite unrelated panel edits. aaPanel itself may delete the plugin directory when uninstalling through its app manager. No other provider or OLS configuration is changed.
