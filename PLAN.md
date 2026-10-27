# Implementation plan

## Public updater — approved direction, 2026-10-27
1. Audit tracked Git history and verify the repository is public; retain private signing/runtime material outside Git.
2. Add tests for public no-token release lookup, six-hour cache, forced refresh, failure cooldown and state-preserving signed updates; implement backend cache and automatic UI notices.
3. Add project AGENTS.md and document public Releases, versioning and maintainer publishing. Build reproducible signed packages without putting artifacts/private keys in Git.
4. Verify backend, browser auto-check scenarios and package signatures, then deploy without changing existing Cron/configuration. Git commits and public publication require explicit authorization.

Binding spec: DESIGN.md. Execute inline; no extra worktrees for this new, explicitly requested DEV repository.

1. Storage and Google adapter: write failing unittest cases for permissions, path validation, shared-drive parameters, pagination, membership validation, uploads and owned-only retention. Implement; rerun.
2. Worker and native integration: test archive/source validation, process locking, Cron patch/unpatch on fixtures and generated command quoting. Implement worker, installer and reversible integration. No live Cron enabled.
3. Panel backend and folder-picker UI: implement public-method allowlist, credentials/settings, paginated shared drives/folders, breadcrumbs, folder creation/confirmation, inventory, history, manual run and verified download. Browser-test with mock Google data and smoke-test real backend without credentials.
4. Releases: test archive traversal and signature rejection; implement signed packaging, private GitHub configuration, check/update/rollback and state-preserving deployment. Add .gitignore/.ignore and setup docs.
5. Verify: run all unit tests, Python/JS syntax checks, browser checks; fresh-context review and focused fixes. Install production only after these gates, verify native provider endpoint and plugin discovery. Never claim live Drive backup/update without credentials/published release.

Review focus: credential endpoint SSRF, folder ancestry escape, malicious ZIP/symlink updates, retention across VPS/destinations, races between worker/update, error status in native Cron, failed-upload local data preservation.
