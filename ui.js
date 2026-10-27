(function () {
  'use strict';
  const root = document.getElementById('sdb-root');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const find = id => root.querySelector('#sdb-' + id);
  const state = { config: {}, inventory: { sites: [], databases: [] }, jobs: [], release: null, updateChecking: false, nextUpdateCheck: 0, restore: { site: null, offset: 0, next: null, sequence: 0 }, picker: { drive: '', folder: '', next: '', driveNext: '', sequence: 0 }, pending: 0 };
  const secure = location.protocol === 'https:';
  const labels = { queued: 'Chờ chạy', running: 'Đang chạy', success: 'Thành công', failed: 'Thất bại' };
  const phases = { preflight: 'kiểm tra', archive: 'nén', upload: 'upload', retention: 'giữ bản', download: 'tải về', validate: 'kiểm tra archive', extract: 'giải nén', snapshot: 'tạo bản dự phòng', activate: 'thay thế webroot', complete: 'hoàn tất' };
  const kinds = { site: 'Website', database: 'MySQL', path: 'Thư mục', download: 'Tải kiểm tra', restore: 'Khôi phục' };
  kinds.database_set = 'Đợt backup MySQL'; kinds.database_restore = 'Khôi phục MySQL';
  phases.import = 'nhập SQL';
  const databaseState = { inventory: null, catalog: null, offset: 0, next: null, sequence: 0 };
  function text(id, value) { find(id).textContent = value == null ? '—' : String(value); }
  function message(value, error) { const area = find('message'); area.hidden = !value; area.textContent = value || ''; area.dataset.error = String(Boolean(error)); }
  function pickerError(error) { find('picker-error').hidden = !error; find('picker-error').textContent = error ? error.message || String(error) : ''; }
  function formatSize(value) { if (value == null) return '—'; let amount = Number(value); const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let unit = 0; while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; } return amount.toLocaleString('vi-VN', { maximumFractionDigits: unit ? 1 : 0 }) + ' ' + units[unit]; }
  function date(value) { return new Date(Number(value) * 1000).toLocaleString('vi-VN', { hour12: false }); }
  function element(tag, value, className) { const node = document.createElement(tag); if (value != null) node.textContent = String(value); if (className) node.className = className; return node; }
  function button(value, callback) { const node = element('button', value, 'sdb-link'); node.type = 'button'; node.addEventListener('click', callback); return node; }
  function confirmAction(options, operation) {
    const dialog = find('confirm-dialog');
    if (dialog.open) return Promise.resolve(false);
    const previousFocus = document.activeElement;
    const accept = find('confirm-accept');
    const cancel = find('confirm-cancel');
    const close = find('confirm-close');
    const input = find('confirm-input');
    find('confirm-input-wrap').hidden = !options.confirmation;
    input.value = '';
    input.placeholder = options.confirmation || '';
    const release = options.release;
    text('confirm-title', options.title);
    text('confirm-heading', options.heading || options.title);
    text('confirm-message', options.message || '');
    text('confirm-error', '');
    find('confirm-error').hidden = true;
    find('confirm-release').hidden = !release;
    find('confirm-safety').hidden = !release;
    if (release) {
      text('confirm-current', 'v' + release.installed_version);
      text('confirm-latest', 'v' + release.version);
      const published = new Date(release.published_at || '');
      const hasDate = !Number.isNaN(published.getTime());
      find('confirm-date-label').hidden = !hasDate;
      find('confirm-date').hidden = !hasDate;
      text('confirm-date', hasDate ? published.toLocaleString('vi-VN', { hour12: false }) : '');
      text('confirm-notes', release.body || 'Chưa có ghi chú phát hành.');
    }
    text('confirm-accept', options.confirmLabel || 'Xác nhận');
    [accept, cancel, close, input].forEach(control => { control.disabled = false; });
    accept.removeAttribute('aria-busy');
    return new Promise(resolve => {
      let busy = false;
      const events = new AbortController();
      const finish = accepted => {
        events.abort();
        dialog.close();
        if (previousFocus && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
        resolve(accepted);
      };
      const dismiss = event => { event.preventDefault(); if (!busy) finish(false); };
      cancel.addEventListener('click', dismiss, { signal: events.signal });
      close.addEventListener('click', dismiss, { signal: events.signal });
      dialog.addEventListener('cancel', dismiss, { signal: events.signal });
      dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        event.preventDefault();
        const controls = [close, ...(options.confirmation ? [input] : []), cancel, accept].filter(control => !control.disabled);
        if (!controls.length) return;
        const index = controls.indexOf(document.activeElement);
        controls[(index + (event.shiftKey ? controls.length - 1 : 1)) % controls.length].focus();
      }, { signal: events.signal });
      accept.addEventListener('click', async () => {
        if (busy) return;
        if (options.confirmation && input.value !== options.confirmation) {
          text('confirm-error', 'Nhập chính xác: ' + options.confirmation);
          find('confirm-error').hidden = false;
          input.focus();
          return;
        }
        busy = true;
        [accept, cancel, close, input].forEach(control => { control.disabled = true; });
        accept.setAttribute('aria-busy', 'true');
        text('confirm-accept', 'Đang xử lý…');
        find('confirm-error').hidden = true;
        try {
          if (operation) await operation();
          finish(true);
        } catch (error) {
          text('confirm-error', error.message || 'Thao tác thất bại. Vui lòng thử lại.');
          find('confirm-error').hidden = false;
          find('confirm-error').scrollIntoView({ block: 'nearest' });
        } finally {
          busy = false;
          [accept, cancel, close, input].forEach(control => { control.disabled = false; });
          accept.removeAttribute('aria-busy');
          text('confirm-accept', options.confirmLabel || 'Xác nhận');
          if (dialog.open) cancel.focus({ preventScroll: true });
        }
      }, { signal: events.signal });
      dialog.showModal();
      dialog.querySelector('.sdb-confirm-body').scrollTop = 0;
      cancel.focus({ preventScroll: true });
    });
  }
  async function api(method, args) {
    if (!secure && ['save_credentials', 'save_github_token'].includes(method)) throw new Error('Cần HTTPS để gửi thông tin xác thực. Có thể nhập qua SSH nếu panel chạy HTTP.');
    const data = Object.assign({}, args || {});
    if (window.$ && window.$.ajax) {
      return new Promise((resolve, reject) => window.$.ajax({ type: 'POST', url: '/plugin?action=a&s=' + encodeURIComponent(method) + '&name=shared_drive_backup', data: data, dataType: 'json', timeout: 190000, success(response) { if (!response || response.status !== true) { reject(new Error(response && response.msg || 'Backend trả về lỗi.')); return; } resolve(response.data); }, error(xhr) { reject(new Error('Không gọi được plugin, HTTP ' + xhr.status + '. Kiểm tra đăng nhập aaPanel.')); } }));
    }
    const response = await fetch('/plugin?action=a&s=' + encodeURIComponent(method) + '&name=shared_drive_backup', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(data) });
    const result = await response.json();
    if (!response.ok || result.status !== true) throw new Error(result.msg || 'Không gọi được plugin.');
    return result.data;
  }
  async function action(control, operation, success) { const disabled = control.disabled; control.disabled = true; control.setAttribute('aria-busy', 'true'); try { const result = await operation(); if (success) message(success, false); return result; } catch (error) { message(error.message, true); throw error; } finally { control.disabled = (control.id === 'sdb-update' || control.id === 'sdb-header-update') ? !state.release || !state.release.available : disabled; control.removeAttribute('aria-busy'); } }
  function bind(id, callback) { find(id).addEventListener('click', event => { Promise.resolve(callback(event)).catch(() => {}); }); }
  function showTab(name) {
    root.querySelectorAll('[role=tab]').forEach(tab => { const selected = tab.dataset.tab === name; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; });
    root.querySelectorAll('[role=tabpanel]').forEach(panel => { panel.hidden = panel.id !== 'sdb-' + name; });
    adjustModalPosition();
    if (name === 'settings') checkUpdates();
    if (name === 'history') loadHistory().catch(error => message(error.message, true));
    if (name === 'cron') loadTasks().catch(error => message(error.message, true));
    if (name === 'backup') loadInventory().catch(error => message(error.message, true));
    if (name === 'restore') loadRestoreSites().catch(error => message(error.message, true));
    if (name === 'databases') loadDatabaseCatalog().catch(error => message(error.message, true));
  }
  function statusNode(job) { const node = element('span', labels[job.status] || job.status, 'sdb-state sdb-state-' + job.status); if (job.status === 'running') node.textContent += ' · ' + (phases[job.details.phase] || job.details.phase || '') + (['upload', 'download', 'extract'].includes(job.details.phase) ? ' ' + (job.details.progress || 0) + '%' : ''); return node; }
  function renderJobs(target, jobs, detailed) {
    target.replaceChildren();
    if (!jobs.length) { const row = element('tr'); const cell = element('td', 'Chưa có tác vụ. Chọn nơi lưu và chạy bản backup thử đầu tiên.', 'sdb-empty'); cell.colSpan = detailed ? 5 : 4; row.append(cell); target.append(row); return; }
    jobs.forEach(job => { const row = element('tr'); const source = element('td'); source.append(element('div', job.details.target || '—')); source.append(element('small', (kinds[job.details.kind] || job.details.kind) + ' · ' + (job.details.origin === 'cron' ? 'Cron aaPanel' : 'Thủ công'))); if (detailed && (job.details.error || job.details.warning || job.details.download_path)) source.append(element('small', job.details.error || job.details.warning || job.details.download_path)); if (detailed && job.details.local_retained) source.append(element('small', 'Archive local được giữ: ' + (job.details.archive || ''))); row.append(source, element('td', date(job.created))); const status = element('td'); status.append(statusNode(job)); row.append(status, element('td', formatSize(job.details.size), 'sdb-number')); if (detailed) { const actions = element('td'); actions.append(button('Xem log', async () => { try { const result = await api('read_log', { job_id: job.id }); find('job-log').hidden = false; text('job-log', result.log); } catch (error) { message(error.message, true); } })); if (job.status === 'success' && job.details.remote) { actions.append(button('Tải kiểm tra', async () => { if (!await confirmAction({ title: 'Tải kiểm tra backup', message: 'Tải bản backup về VPS và xác minh checksum? Không giải nén hoặc ghi đè dữ liệu production.', confirmLabel: 'Tải kiểm tra' })) return; try { const result = await api('start_download', { job_id: job.id }); message('Đã tạo tác vụ tải kiểm tra: ' + result.job_id, false); await loadHistory(); } catch (error) { message(error.message, true); } })); const remoteId = job.details.remote.id; if (/^[A-Za-z0-9_-]+$/.test(remoteId)) { const link = element('a', 'Mở Drive', 'sdb-link'); link.href = 'https://drive.google.com/file/d/' + encodeURIComponent(remoteId) + '/view'; link.target = '_blank'; link.rel = 'noopener'; actions.append(link); } } row.append(actions); } target.append(row); });
  }
  async function refresh() {
    const result = await api('status'); state.config = result.config;
    if (result.recovery_required) message('Có journal cập nhật/khôi phục chưa hoàn tất. Kiểm tra README và lịch sử trước khi chạy tiếp; không xoá journal để bỏ qua cảnh báo.', true);
    if (result.recovery_required) message('Cập nhật trước chưa hoàn tất. Không chạy backup; dùng công cụ recover_deployment.py theo README.', true);
    text('version', 'v' + result.version); text('current-version', result.version); text('repository', result.repository);
    text('server-summary', result.config.server_name + ' · VPS ID ' + result.config.server_id.slice(0, 12)); text('route-source', result.config.server_name); text('route-drive', result.config.drive_name || 'Chưa chọn Shared drive'); text('route-folder', result.config.folder_name || 'Chưa chọn thư mục');
    text('google-state', result.config.configured && result.credentials.present ? 'Đã cấu hình' : 'Chưa cấu hình'); text('worker-state', result.busy ? 'Đang xử lý' : 'Sẵn sàng'); text('disk-free', formatSize(result.disk.free)); text('disk-reserve', 'Dự phòng tối thiểu ' + formatSize(result.config.min_free_mb * 1024 * 1024));
    text('credential-state', result.credentials.present ? 'Đã lưu ' + result.credentials.type + (result.credentials.email ? ' · ' + result.credentials.email : '') : 'Chưa nhập credentials.'); text('saved-drive', result.config.drive_name || 'Chưa chọn'); text('saved-folder', result.config.folder_name || 'Chưa chọn'); text('saved-id', result.config.folder_id || '—');
    text('integration-state', result.integration.registered ? 'Adapter Cron đã đăng ký. Nếu chưa thấy lựa chọn, cần tải lại panel; xem README về nạp lại backend.' : 'Chưa đăng ký hoặc adapter không còn khớp phiên bản aaPanel. Không bật lịch thật.');
    ['server-name', 'min-free', 'max-archive'].forEach((id, index) => { if (document.activeElement !== find(id)) find(id).value = result.config[['server_name', 'min_free_mb', 'max_archive_gb'][index]]; });
    if (document.activeElement !== find('keep-local')) find('keep-local').checked = result.config.keep_local;
    if (document.activeElement !== find('prune')) find('prune').checked = result.config.prune_enabled;
    find('rollback').disabled = !result.rollback_available;
    renderJobs(find('recent-rows'), result.history, false);
    return result;
  }
  async function loadHistory() { const result = await api('history'); state.jobs = result.jobs; renderJobs(find('history-rows'), result.jobs, true); result.jobs.forEach((job, index) => { if (!['restore', 'database_restore', 'database_set'].includes(job.details.kind)) return; const cell = find('history-rows').rows[index].cells[0]; [['restore_path', 'Kết quả'], ['snapshot', 'Snapshot trước khôi phục'], ['previous_path', 'Thư mục cũ'], ['current_database', 'Database đang xử lý']].forEach(([key, label]) => { if (job.details[key]) cell.append(element('small', label + ': ' + job.details[key])); }); (job.details.snapshots || []).forEach(item => cell.append(element('small', 'Snapshot ' + item.name + ': ' + item.snapshot))); (job.details.results || []).forEach(item => cell.append(element('small', item.name + ': ' + item.state))); if (job.details.kind === 'database_set') cell.append(element('strong', job.details.complete ? 'Đợt đã hoàn tất và xác minh' : 'Đợt chưa hoàn tất — không dùng như bản full')); if (job.details.recovery_required) cell.append(element('strong', 'Có journal chưa hoàn tất. Không chạy lại; kiểm tra phục hồi trước.')); }); }

  function databaseCheckbox(container, name, description, checked, disabled = false) {
    const label = element('label', null, 'sdb-db-choice');
    const input = element('input'); input.type = 'checkbox'; input.value = name; input.checked = checked; input.disabled = disabled;
    const content = element('span', name); if (description) content.append(element('small', description));
    label.append(input, content); container.append(label); return input;
  }
  function checkedDatabases(id) { return Array.from(find(id).querySelectorAll('input:checked:not(:disabled)'), input => input.value); }
  function databaseSelectionChanged() {
    const names = checkedDatabases('db-restore-options');
    const entries = databaseState.catalog ? databaseState.catalog.entries.filter(entry => names.includes(entry.name)) : [];
    const sites = [...new Set(entries.flatMap(entry => entry.sites))].sort();
    text('db-selection-summary', names.length ? names.length + ' database sẽ ghi đè: ' + names.join(', ') + '. Website bị ảnh hưởng: ' + (sites.join(', ') || 'Chưa liên kết — phải tự xác định và dừng mọi nguồn ghi.') : 'Chưa chọn database.');
    find('db-restore').disabled = !names.length;
    const available = databaseState.catalog ? databaseState.catalog.entries.filter(entry => entry.available).length : 0;
    find('db-restore-all').checked = Boolean(databaseState.catalog && databaseState.catalog.complete && available > 0 && names.length === available);
    find('db-restore-all').indeterminate = names.length > 0 && !find('db-restore-all').checked;
  }
  function renderDatabaseLinks() {
    const current = databaseState.inventory;
    const names = (current.links || {})[find('db-link-site').value] || [];
    find('db-link-options').replaceChildren();
    current.databases.forEach(item => databaseCheckbox(find('db-link-options'), item.name, '', names.includes(item.name)));
  }
  function renderDatabaseInventory(current) {
    databaseState.inventory = current;
    const previousSite = find('db-link-site').value;
    const select = find('db-link-site'); select.replaceChildren(element('option', 'Chọn website…')); select.firstChild.value = '';
    current.sites.forEach(site => { const option = element('option', site.name); option.value = site.name; select.append(option); });
    (current.stale_links || []).filter(name => !current.sites.some(site => site.name === name)).forEach(name => { const option = element('option', name + ' · đã xóa, lưu rỗng để bỏ liên kết'); option.value = name; select.append(option); });
    select.value = previousSite;
    text('db-stale', 'Liên kết đã thay đổi, cần xác nhận/lưu lại: ' + (current.stale_links || []).join(', ')); find('db-stale').hidden = !(current.stale_links || []).length;
    renderDatabaseLinks();
    const selected = checkedDatabases('db-backup-options');
    find('db-backup-options').replaceChildren();
    current.databases.forEach(item => databaseCheckbox(find('db-backup-options'), item.name, Object.keys(current.links || {}).filter(site => current.links[site].includes(item.name)).join(', ') || 'Chưa liên kết website', selected.includes(item.name)));
    find('db-backup').disabled = !checkedDatabases('db-backup-options').length;
  }
  async function loadDatabaseCatalog(offset = databaseState.offset) {
    const sequence = ++databaseState.sequence;
    text('db-status', 'Đang tải danh sách database và các đợt backup…');
    find('db-restore').disabled = true;
    const result = await api('database_catalog', { offset });
    if (sequence !== databaseState.sequence) return;
    databaseState.offset = result.offset; databaseState.next = result.next_offset; databaseState.catalog = null;
    renderDatabaseInventory(result.inventory);
    const select = find('db-backup-source'); select.replaceChildren(element('option', 'Chọn đợt / bản backup…')); select.firstChild.value = '';
    result.backups.forEach(backup => { const option = element('option', date(backup.created) + ' · ' + backup.target + (backup.batch_id ? ' · Thành viên đợt chưa hoàn tất' : '') + (backup.complete ? '' : ' · CHƯA HOÀN TẤT') + (!backup.retained ? ' · Hết thời gian giữ' : '')); option.value = backup.id; option.disabled = !backup.retained; select.append(option); });
    find('db-prev').disabled = !result.offset; find('db-next').disabled = result.next_offset == null;
    find('db-restore-options').replaceChildren(); find('db-restore-site').disabled = true; find('db-restore-all').disabled = true; find('db-restore-all').checked = false; find('db-writers-stopped').checked = false;
    databaseSelectionChanged();
    text('db-status', result.inventory.databases.length + ' MySQL local · ' + result.backups.length + ' bản/đợt trên trang này. Chưa kết nối hoặc import MySQL khi chỉ xem danh sách.');
  }
  async function loadDatabaseMembers() {
    const sequence = ++databaseState.sequence;
    const backupId = find('db-backup-source').value;
    databaseState.catalog = null; find('db-restore-options').replaceChildren(); find('db-restore-all').disabled = true; find('db-restore-site').disabled = true; find('db-writers-stopped').checked = false; databaseSelectionChanged();
    if (!backupId) return;
    text('db-status', 'Đang tải thành viên bản backup…');
    const result = await api('database_catalog', { backup_id: backupId });
    if (sequence !== databaseState.sequence) return;
    databaseState.catalog = result;
    result.entries.forEach(entry => databaseCheckbox(find('db-restore-options'), entry.name, (entry.sites.join(', ') || 'Chưa liên kết website') + ' · ' + formatSize(entry.size) + (entry.legacy ? ' · bản cũ, xác nhận đúng tên database' : '') + (entry.error ? ' · ' + entry.error : ''), false, !entry.available));
    const select = find('db-restore-site'); select.replaceChildren(element('option', 'Chọn website hoặc chọn database riêng…')); select.firstChild.value = '';
    [...new Set(result.entries.flatMap(entry => entry.sites))].sort().forEach(site => { const option = element('option', site); option.value = site; select.append(option); }); select.disabled = false;
    find('db-restore-all').disabled = !result.complete || !result.entries.length || result.entries.some(entry => !entry.available);
    text('db-status', (result.complete ? 'Bản backup hoàn tất. ' : 'Đợt chưa hoàn tất: chỉ chọn riêng các database đã thành công. ') + 'Worker sẽ kiểm tra Drive/checksum trước khi snapshot và import.');
  }
  bind('db-refresh', event => action(event.currentTarget, () => loadDatabaseCatalog()));
  bind('db-prev', event => action(event.currentTarget, () => loadDatabaseCatalog(Math.max(0, databaseState.offset - 20))));
  bind('db-next', event => action(event.currentTarget, () => loadDatabaseCatalog(databaseState.next)));
  find('db-link-site').addEventListener('change', renderDatabaseLinks);
  find('db-backup-source').addEventListener('change', () => loadDatabaseMembers().catch(error => message(error.message, true)));
  find('db-backup-options').addEventListener('change', () => { const count = checkedDatabases('db-backup-options').length; find('db-backup').disabled = !count; find('db-backup-all').checked = count > 0 && count === databaseState.inventory.databases.length; find('db-backup-all').indeterminate = count > 0 && count < databaseState.inventory.databases.length; });
  find('db-backup-all').addEventListener('change', event => { find('db-backup-options').querySelectorAll('input').forEach(input => { input.checked = event.target.checked; }); find('db-backup').disabled = !checkedDatabases('db-backup-options').length; });
  find('db-restore-options').addEventListener('change', databaseSelectionChanged);
  find('db-restore-all').addEventListener('change', event => { find('db-restore-options').querySelectorAll('input:not(:disabled)').forEach(input => { input.checked = event.target.checked; }); databaseSelectionChanged(); });
  find('db-restore-site').addEventListener('change', event => { const site = event.target.value; find('db-restore-options').querySelectorAll('input:not(:disabled)').forEach(input => { input.checked = databaseState.catalog.entries.some(entry => entry.name === input.value && entry.sites.includes(site)); }); databaseSelectionChanged(); });
  bind('db-save-links', event => action(event.currentTarget, async () => {
    const site = find('db-link-site').value; if (!site) throw new Error('Chọn website cần liên kết trước.');
    const names = checkedDatabases('db-link-options');
    if (!await confirmAction({ title: 'Xác nhận liên kết database', message: site + ': ' + (names.join(', ') || 'Bỏ liên kết database') + '. Không sửa database, website hoặc mật khẩu.', confirmLabel: 'Lưu liên kết' })) return;
    renderDatabaseInventory(await api('save_database_links', { links: JSON.stringify({ [site]: names }) }));
    databaseState.catalog = null; find('db-restore-options').replaceChildren(); databaseSelectionChanged();
    text('db-status', 'Đã lưu liên kết. Chọn lại bản backup trước khi khôi phục.');
  }));
  bind('db-backup', event => action(event.currentTarget, async () => {
    const names = checkedDatabases('db-backup-options'); if (!names.length) throw new Error('Chọn database cần backup.');
    if (!await confirmAction({ title: 'Backup database theo đợt', message: 'Backup ' + names.length + ' database: ' + names.join(', ') + '. Mỗi database có file riêng; chỉ đánh dấu đợt hoàn tất sau khi tất cả được xác minh.', confirmLabel: 'Backup đã chọn' })) return;
    const result = await api('start_database_backup', { database_names: JSON.stringify(names), keep: find('db-keep').value }); message('Đã tạo đợt backup ' + result.job_id, false); showTab('history');
  }));
  bind('db-restore', event => action(event.currentTarget, async () => {
    const names = checkedDatabases('db-restore-options'); const catalog = databaseState.catalog;
    if (!catalog || !names.length || !find('db-writers-stopped').checked) throw new Error('Chọn database và xác nhận đã dừng mọi nguồn ghi trước.');
    const confirmation = 'RESTORE ' + names.length + ' DATABASES';
    const accepted = await confirmAction({ title: 'Ghi đè database MySQL', message: find('db-selection-summary').textContent + ' Tạo snapshot local trước khi import. Không tự rollback cả đợt nếu lỗi. Nhập: ' + confirmation, confirmation, confirmLabel: 'Snapshot và khôi phục' }, async () => {
      const result = await api('start_database_restore', { backup_id: catalog.id, database_names: JSON.stringify(names), confirmation, writers_stopped: '1' }); message('Đã tạo tác vụ khôi phục database ' + result.job_id + '. Theo dõi Lịch sử; không bật nguồn ghi cho đến khi xác minh kết quả.', false);
    });
    if (accepted) showTab('history');
  }));
  async function loadRestoreSites() {
    const previous = find('restore-site').value;
    const result = await api('restore_sites');
    const select = find('restore-site');
    select.replaceChildren(element('option', 'Chọn website…'));
    select.firstChild.value = '';
    result.sites.forEach(site => { const option = element('option', site.name); option.value = site.id; select.append(option); });
    if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
    await loadRestoreCatalog();
  }
  async function restoreAction(backup, mode) {
    const site = state.restore.site;
    if (!site) return;
    const replace = mode === 'replace';
    const download = mode === 'download';
    let result;
    const accepted = await confirmAction({ title: download ? 'Tải backup về VPS' : replace ? 'Khôi phục website' : 'Giải nén kiểm tra', message: 'Website: ' + site.name + '\nWebroot: ' + site.path + '\nBackup: ' + backup.name + '\n\n' + (replace ? 'Thay thế toàn bộ file website, không merge. Đã dừng website và các tác vụ ghi dữ liệu? Plugin tạo snapshot và giữ thư mục cũ. Không khôi phục database/OLS/SSL/DNS; không tự bật lại website.' : download ? 'Chỉ tải và xác minh checksum, không giải nén hay thay đổi website.' : 'Tải, kiểm tra checksum và giải nén vào thư mục riêng của plugin. Không thay đổi website.'), confirmation: replace ? site.name : '', confirmLabel: replace ? 'Khôi phục website' : download ? 'Tải về VPS' : 'Giải nén kiểm tra' }, async () => {
      result = download ? await api('start_download', { job_id: backup.id }) : await api('start_restore', { backup_id: backup.id, site_id: site.id, mode, confirmation: replace ? find('confirm-input').value : '' });
    });
    if (accepted) { message('Đã tạo tác vụ ' + result.job_id + '. Theo dõi tiến độ và đường dẫn kết quả trong Lịch sử.', false); showTab('history'); }
  }
  async function mapRestoreBackup(backup) {
    const site = state.restore.site;
    if (!site) return;
    const accepted = await confirmAction({ title: 'Liên kết backup cũ với website', message: 'Backup: ' + backup.name + '\nWebsite: ' + site.name + '\nĐường dẫn hiện tại: ' + site.path + '\n\nBản cũ chưa lưu ID website. Chỉ tiếp tục nếu đây đúng là website gốc; bước này chưa khôi phục file.', confirmation: site.name, confirmLabel: 'Liên kết website' }, () => api('map_backup', { backup_id: backup.id, site_id: site.id, confirmation: find('confirm-input').value }));
    if (accepted) await loadRestoreCatalog();
  }
  async function loadRestoreCatalog() {
    const sequence = ++state.restore.sequence;
    const siteId = find('restore-site').value;
    state.restore.site = null;
    find('restore-sync').disabled = true;
    find('restore-next').disabled = true;
    find('restore-prev').disabled = true;
    const rows = find('restore-rows');
    rows.replaceChildren();
    text('restore-status', siteId ? 'Đang kiểm tra metadata trên Drive…' : 'Chọn website để xem backup.');
    if (!siteId) { text('restore-target', 'Chưa chọn website.'); return; }
    try {
      const result = await api('restore_catalog', { site_id: siteId, offset: state.restore.offset });
      if (sequence !== state.restore.sequence || siteId !== find('restore-site').value) return;
      state.restore.site = result.site;
      state.restore.next = result.next_offset;
      text('restore-target', result.site.path + ' · ' + (String(result.site.status) === '0' ? 'Website đang dừng' : 'Website đang chạy — chỉ tải/giải nén kiểm tra; dừng trong aaPanel trước khi thay thế.'));
      text('restore-status', result.backups.length + ' bản trên trang này.');
      result.backups.forEach(backup => {
        const row = element('tr');
        const name = element('td');
        name.append(element('div', backup.name), element('small', (backup.origin === 'cron' ? 'Cron aaPanel' : 'Thủ công') + (backup.partial ? ' · Có exclusions' : '') + (!backup.mapped ? ' · Chưa liên kết ID website' : '')));
        const status = element('td', ({ available: 'Còn trên Drive', missing: 'Trong thùng rác', unavailable: 'Chưa xác minh' })[backup.availability] || 'Chưa xác minh');
        if (backup.error) status.append(element('small', backup.error));
        const actions = element('td');
        const available = backup.availability === 'available';
        const add = (label, callback, disabled) => { const control = button(label, () => callback().catch(error => message(error.message, true))); control.disabled = disabled; actions.append(control); };
        add('Tải về VPS', () => restoreAction(backup, 'download'), !available);
        if (!backup.mapped && available) add('Liên kết website', () => mapRestoreBackup(backup), false);
        add('Giải nén kiểm tra', () => restoreAction(backup, 'preview'), !available || !backup.mapped);
        add('Khôi phục website', () => restoreAction(backup, 'replace'), !available || !backup.mapped || backup.partial || String(result.site.status) !== '0');
        row.append(name, element('td', date(backup.created)), element('td', formatSize(backup.size)), status, actions);
        rows.append(row);
      });
      if (!result.backups.length) { const row = element('tr'); const cell = element('td', 'Không có backup website đã xác minh trên trang này.', 'sdb-empty'); cell.colSpan = 5; row.append(cell); rows.append(row); }
      find('restore-sync').disabled = !result.backups.length;
      find('restore-next').disabled = result.next_offset == null;
      find('restore-prev').disabled = state.restore.offset === 0;
    } catch (error) {
      if (sequence === state.restore.sequence) text('restore-status', 'Không kiểm tra được backup: ' + error.message);
      throw error;
    }
  }
  find('restore-site').addEventListener('change', () => { state.restore.offset = 0; loadRestoreCatalog().catch(error => message(error.message, true)); });
  bind('restore-refresh', event => action(event.currentTarget, loadRestoreSites));
  bind('restore-next', () => { if (state.restore.next == null) return; state.restore.offset = state.restore.next; return loadRestoreCatalog(); });
  bind('restore-prev', () => { state.restore.offset = Math.max(0, state.restore.offset - 20); return loadRestoreCatalog(); });
  bind('restore-sync', async () => {
    const site = state.restore.site;
    if (!site) return;
    let result;
    const offset = state.restore.offset;
    const accepted = await confirmAction({ title: 'Đồng bộ danh sách backup aaPanel', message: 'Website: ' + site.name + '\nChỉ đồng bộ tối đa 20 bản trên trang này đã liên kết và xác minh. Không tải/xoá file Drive. Chỉ bỏ dòng do plugin quản lý khi xác nhận file ở thùng rác; lỗi quyền/mạng không xoá dòng.', confirmLabel: 'Đồng bộ' }, async () => { result = await api('sync_native', { site_id: site.id, offset }); });
    if (accepted) { message('Đã ghi ' + result.registered + ' bản; bỏ ' + result.missing_removed + ' bản đã vào thùng rác khỏi danh sách native.', false); await loadRestoreCatalog(); }
  });
  async function loadInventory() { state.inventory = await api('inventory'); renderTargets(); }
  function renderTargets() { const kind = find('backup-kind').value; find('target-select-wrap').hidden = kind === 'path'; find('target-path-wrap').hidden = kind !== 'path'; find('backup-exclusions').disabled = kind === 'database'; const select = find('backup-target'); const previous = select.value; select.replaceChildren(element('option', 'Chọn nguồn backup…')); select.firstChild.value = ''; (state.inventory[kind === 'site' ? 'sites' : 'databases'] || []).forEach(item => { const option = element('option', item.name); option.value = item.name; select.append(option); }); if (Array.from(select.options).some(option => option.value === previous)) select.value = previous; }
  async function loadTasks() { const result = await api('tasks'); const rows = find('cron-rows'); rows.replaceChildren(); if (!result.tasks.length) { const row = element('tr'); const cell = element('td', 'Chưa có lịch dùng Shared Drive Backup. Tạo lịch tại Cron Job aaPanel.', 'sdb-empty'); cell.colSpan = 4; row.append(cell); rows.append(row); } result.tasks.forEach(task => { const row = element('tr'); row.append(element('td', task.name), element('td', (kinds[task.sType] || task.sType) + ' · ' + task.sName), element('td', task.type + ' · ' + String(task.where_hour).padStart(2, '0') + ':' + String(task.where_minute).padStart(2, '0')), element('td', Number(task.status) === 1 ? 'Đang bật' : 'Đang tắt')); rows.append(row); }); }
  async function loadDrives(append) { const result = await api('list_drives', { page_token: append ? state.picker.driveNext : '' }); const select = find('drive-select'); if (!append) { select.replaceChildren(element('option', 'Chọn bộ nhớ…')); select.firstChild.value = ''; } (result.drives || []).forEach(drive => { const option = element('option', drive.name); option.value = drive.id; select.append(option); }); state.picker.driveNext = result.nextPageToken || ''; find('more-drives').hidden = !state.picker.driveNext; if (state.picker.drive) select.value = state.picker.drive; }
  async function browse(driveId, folderId, append) {
    const sequence = ++state.picker.sequence; pickerError(null); find('confirm-folder').disabled = true; find('create-folder').disabled = true; find('more-folders').disabled = true;
    if (!append) find('folder-list').replaceChildren(element('p', 'Đang tải thư mục…', 'sdb-empty'));
    try {
      const result = await api('browse_folders', { drive_id: driveId, folder_id: folderId || driveId, page_token: append ? state.picker.next : '' });
      if (sequence !== state.picker.sequence || !find('folder-dialog').open) return;
      state.picker.drive = driveId; state.picker.folder = result.folder.id; state.picker.next = result.nextPageToken || ''; state.picker.folderName = result.folder.name;
      find('drive-id').value = driveId; find('folder-id').value = result.folder.id; find('drive-select').value = driveId;
      const crumbs = find('breadcrumbs'); crumbs.replaceChildren(); result.breadcrumbs.forEach((crumb, index) => { if (index) crumbs.append(element('span', '›', 'sdb-muted')); crumbs.append(button(crumb.name, () => browse(driveId, crumb.id, false))); });
      const list = find('folder-list'); if (!append) list.replaceChildren();
      (result.files || []).forEach(folder => { const row = element('button', null, 'sdb-folder-row'); row.type = 'button'; row.append(element('span', folder.name), element('small', 'Mở ›')); row.addEventListener('click', () => browse(driveId, folder.id, false)); list.append(row); });
      if (!list.children.length) list.append(element('p', 'Không có thư mục con. Bạn có thể lưu tại đây hoặc tạo thư mục mới.', 'sdb-empty'));
      const writable = Boolean(result.folder.capabilities && result.folder.capabilities.canAddChildren);
      text('picker-current', result.breadcrumbs.map(crumb => crumb.name).join(' / ')); text('picker-current-id', result.folder.id + (writable ? ' · Có quyền tạo file' : ' · Chỉ đọc, không thể chọn làm nơi lưu'));
      find('confirm-folder').disabled = !writable; find('create-folder').disabled = !writable; find('more-folders').hidden = !state.picker.next;
    } catch (error) { if (sequence === state.picker.sequence) { state.picker.folder = ''; state.picker.next = ''; text('picker-current', 'Chưa chọn thư mục hợp lệ'); pickerError(error); if (!append) find('folder-list').replaceChildren(element('p', 'Không đọc được thư mục. Kiểm tra ID, quyền và thử lại.', 'sdb-empty')); } } finally { if (sequence === state.picker.sequence) find('more-folders').disabled = false; }
  }
  async function openPicker() { const dialog = find('folder-dialog'); if (!dialog.open) dialog.showModal(); pickerError(null); state.picker.drive = state.config.drive_id || ''; state.picker.folder = ''; state.picker.next = ''; find('confirm-folder').disabled = true; find('create-folder').disabled = true; text('picker-current', 'Chưa chọn thư mục'); text('picker-current-id', 'Điều hướng chưa thay đổi cấu hình đã lưu.'); try { await loadDrives(false); if (state.config.drive_id) await browse(state.config.drive_id, state.config.folder_id || state.config.drive_id, false); } catch (error) { pickerError(error); } }
  root.querySelectorAll('[data-tab]').forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
  root.querySelector('[role=tablist]').addEventListener('keydown', event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; const tabs = Array.from(root.querySelectorAll('[role=tab]')); const index = tabs.indexOf(document.activeElement); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; event.preventDefault(); showTab(tabs[next].dataset.tab); tabs[next].focus(); });
  root.querySelectorAll('[data-go]').forEach(control => control.addEventListener('click', () => showTab(control.dataset.go)));
  root.querySelectorAll('[data-open-picker]').forEach(control => control.addEventListener('click', () => { openPicker().catch(pickerError); }));
  bind('picker-close', () => { state.picker.sequence += 1; find('folder-dialog').close(); });
  find('folder-dialog').addEventListener('cancel', () => { state.picker.sequence += 1; });
  bind('refresh', event => action(event.currentTarget, refresh, 'Đã làm mới trạng thái.'));
  bind('refresh-history', event => action(event.currentTarget, loadHistory));
  bind('check-connection', event => action(event.currentTarget, async () => { await api('connection_check'); text('google-state', 'Đã kiểm tra OK'); }, 'Google xác nhận có quyền tạo file tại thư mục đã chọn.'));
  find('credential-file').addEventListener('change', () => { text('credential-filename', find('credential-file').files[0] ? find('credential-file').files[0].name : 'Chưa chọn file.'); });
  bind('save-credentials', event => action(event.currentTarget, async () => { const file = find('credential-file').files[0]; if (!file || file.size > 65536) throw new Error('Chọn file JSON xác thực, tối đa 64 KiB.'); const documentText = await file.text(); JSON.parse(documentText); await api('save_credentials', { document: documentText }); find('credential-file').value = ''; text('credential-filename', 'Đã nhập file; không giữ nội dung trong giao diện.'); await refresh(); }, 'Đã lưu xác thực. Tiếp theo, chọn và xác nhận thư mục lưu.'));
  find('drive-select').addEventListener('change', event => { if (event.target.value) browse(event.target.value, event.target.value, false); });
  bind('open-drive', () => browse(find('drive-id').value.trim(), find('drive-id').value.trim(), false));
  bind('open-folder', () => browse(state.picker.drive || find('drive-id').value.trim(), find('folder-id').value.trim(), false));
  bind('more-drives', event => action(event.currentTarget, () => loadDrives(true)).catch(pickerError));
  bind('more-folders', () => browse(state.picker.drive, state.picker.folder, true));
  find('new-folder-form').addEventListener('submit', async event => { event.preventDefault(); try { await action(find('create-folder'), async () => { const result = await api('create_folder', { drive_id: state.picker.drive, parent_id: state.picker.folder, folder_name: find('new-folder-name').value }); find('new-folder-name').value = ''; await browse(state.picker.drive, result.id, false); }); } catch (error) { pickerError(error); } });
  bind('confirm-folder', async event => { try { await action(event.currentTarget, async () => { if (!state.picker.folder) throw new Error('Chưa chọn thư mục hợp lệ.'); await api('set_destination', { drive_id: state.picker.drive, folder_id: state.picker.folder }); find('folder-dialog').close(); await refresh(); }, 'Đã xác nhận thư mục lưu. Cron và backup thủ công sẽ dùng nơi nhận này.'); } catch (error) { pickerError(error); } });
  find('backup-kind').addEventListener('change', renderTargets);
  find('backup-form').addEventListener('submit', async event => { event.preventDefault(); const submit = event.currentTarget.querySelector('[type=submit]'); try { await action(submit, async () => { const kind = find('backup-kind').value; const target = kind === 'path' ? find('backup-path').value.trim() : find('backup-target').value; if (!target) throw new Error('Chọn nguồn backup trước.'); if (!await confirmAction({ title: 'Chạy backup', message: 'Backup ' + target + ' lên thư mục đã xác nhận? Plugin sẽ tạo archive trên đĩa VPS.', confirmLabel: 'Backup ngay' })) return; const result = await api('start_backup', { kind, target, keep: find('backup-keep').value, exclusions: kind === 'database' ? '' : find('backup-exclusions').value }); message('Đã tạo tác vụ ' + result.job_id + '. Theo dõi tiến độ trong Lịch sử.', false); showTab('history'); }); } catch (error) {} });
  find('settings-form').addEventListener('submit', async event => { event.preventDefault(); const submit = event.currentTarget.querySelector('[type=submit]'); if (find('prune').checked && !state.config.prune_enabled && !await confirmAction({ title: 'Bật dọn dẹp Drive', message: 'Cho phép đưa các bản backup cũ do plugin quản lý vào thùng rác Drive, theo số bản giữ lại của từng nguồn?', confirmLabel: 'Cho phép' })) { find('prune').checked = false; return; } try { await action(submit, async () => { await api('save_settings', { server_name: find('server-name').value, min_free_mb: find('min-free').value, max_archive_gb: find('max-archive').value, keep_local: find('keep-local').checked ? '1' : '0', prune_enabled: find('prune').checked ? '1' : '0' }); await refresh(); }, 'Đã lưu cài đặt.'); } catch (error) {} });
  function renderUpdate(result) {
    state.release = result;
    state.nextUpdateCheck = result.next_check_at;
    const headerUpdate = find('header-update');
    if (headerUpdate) {
      headerUpdate.hidden = !result.available || Boolean(result.error);
      headerUpdate.disabled = !result.available || Boolean(result.error);
      headerUpdate.textContent = result.available ? 'Cập nhật lên v' + result.version : 'Update';
    }
    text('update-status', result.error || (result.available ? 'Có phiên bản mới: v' + result.version : 'Bạn đang dùng bản mới nhất: v' + result.installed_version));
    text('update-checked', result.checked_at ? 'Lần kiểm tra: ' + date(result.checked_at) : 'Chưa kiểm tra được phiên bản.');
    find('update-banner').hidden = !result.available || Boolean(result.error);
    text('update-banner-text', result.available ? 'Đã có Shared Drive Backup v' + result.version + '.' : '');
    find('release-notes').hidden = !result.body;
    text('release-notes', result.body || '');
  }
  async function checkUpdates(force = false) {
    if (state.updateChecking || (!force && Date.now() / 1000 < state.nextUpdateCheck)) return;
    state.updateChecking = true;
    const headerUpdate = find('header-update');
    if (headerUpdate) headerUpdate.disabled = true;
    text('update-status', 'Đang kiểm tra phiên bản…');
    try {
      renderUpdate(await api('check_update', { force: force ? '1' : '0' }));
    } catch (error) {
      renderUpdate({ available: false, error: error.message, checked_at: null, next_check_at: Date.now() / 1000 + 900 });
    } finally {
      state.updateChecking = false;
    }
  }
  bind('check-update', event => action(event.currentTarget, () => checkUpdates(true)));
  async function triggerUpdate(control) {
    const release = state.release;
    if (!release || !release.available || release.error) return;
    const updated = await confirmAction({ title: '[Shared Drive Backup] - Cập nhật phiên bản', heading: 'Có bản cập nhật mới', release, confirmLabel: 'Cập nhật ngay' }, () => action(control, async () => {
      await api('update', { version: release.version });
      state.release = null;
      const headerUpdate = find('header-update');
      if (headerUpdate) {
        headerUpdate.disabled = true;
        headerUpdate.textContent = 'Đã cập nhật';
      }
      find('update-banner').hidden = true;
      text('update-status', 'Đã cập nhật xong. Đang tải lại trang…');
    }, 'Đã cập nhật xong. Đang tải lại…'));
    if (updated) {
      text('version', 'v' + release.version);
      text('current-version', release.version);
      setTimeout(() => { window.location.reload(); }, 1200);
    }
  }
  bind('header-update', event => triggerUpdate(event.currentTarget));
  bind('rollback', async event => { const control = event.currentTarget; if (!await confirmAction({ title: 'Khôi phục phiên bản', message: 'Khôi phục mã phiên bản trước, giữ dữ liệu và cấu hình?', confirmLabel: 'Khôi phục' })) return; await action(control, async () => { await api('rollback'); setTimeout(() => { window.location.reload(); }, 1200); }, 'Đã rollback. Đang tải lại…'); });
  bind('repair-integration', event => action(event.currentTarget, async () => { const result = await api('repair_integration'); await refresh(); if (result.restart_panel_required) message('Adapter đã sửa. Cần nạp lại dịch vụ panel để backend nhận thay đổi; không cần restart OLS.', false); }));
  function syncTheme() {
    try {
      let isDark = false;
      const docEl = document.documentElement;
      const themeMode = docEl.getAttribute('theme-mode');
      const dataTheme = docEl.getAttribute('data-theme');
      if (themeMode === 'dark' || dataTheme === 'dark' || docEl.classList.contains('dark')) {
        isDark = true;
      } else if (themeMode === 'light' || dataTheme === 'light' || docEl.classList.contains('light')) {
        isDark = false;
      } else {
        const rawStore = localStorage.getItem('ThemeStore');
        if (rawStore) {
          const parsed = JSON.parse(rawStore);
          const ref = parsed && (parsed.themeRef || (parsed.theme && parsed.theme.value));
          if (ref === 'dark') isDark = true;
          else if (ref === 'light') isDark = false;
          else isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        } else {
          const bg = getComputedStyle(document.body).backgroundColor;
          const rgb = bg.match(/\d+/g);
          isDark = rgb && rgb.length >= 3 ? (Number(rgb[0]) + Number(rgb[1]) + Number(rgb[2]) <= 480) : false;
        }
      }
      root.classList.toggle('sdb-light', !isDark);
      syncModalTheme();
    } catch (e) {}
  }
  function syncModalTheme() {
    const modal = root.closest('.layui-layer');
    if (!modal) return;
    const styles = getComputedStyle(root);
    ['--sdb-bg', '--sdb-surface', '--sdb-text', '--sdb-border', '--sdb-hover'].forEach(property => modal.style.setProperty(property, styles.getPropertyValue(property)));
  }
  syncTheme();
  if (window.MutationObserver) {
    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['theme-mode', 'data-theme', 'class'] });
  }
  window.addEventListener('storage', event => { if (event.key === 'ThemeStore') syncTheme(); });
  bind('theme', () => { root.classList.toggle('sdb-light'); syncModalTheme(); });
  find('http-warning').hidden = secure;
  find('save-credentials').disabled = !secure;
  find('credential-file').disabled = !secure;
  if (!secure) text('http-warning', 'Panel đang chạy HTTP: không gửi credentials/token qua trình duyệt. Bật HTTPS hoặc dùng CLI import qua SSH.');
  let modalLayoutFrame;
  function adjustModalPosition() {
    if (modalLayoutFrame) return;
    modalLayoutFrame = requestAnimationFrame(() => {
      modalLayoutFrame = null;
      if (!root.isConnected) return;
      const modal = root.closest('.layui-layer');
      if (!modal) return;
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
      const targetWidth = Math.min(1100, viewportWidth - 32);
      modal.classList.add('sdb-modal');
      Object.assign(modal.style, {
        width: targetWidth + 'px',
        height: (viewportHeight - 40) + 'px',
        maxHeight: (viewportHeight - 40) + 'px',
        left: ((viewportWidth - targetWidth) / 2) + 'px',
        top: '20px',
        transform: 'none',
        margin: '0'
      });
      const content = modal.querySelector('.layui-layer-content');
      if (content) content.style.removeProperty('height');
      const close = modal.querySelector('.layui-layer-close');
      const titleIcon = modal.querySelector('.layui-layer-title img');
      if (titleIcon) titleIcon.src = root.querySelector('.sdb-brand img').src;
      if (close) {
        close.textContent = '×';
        close.setAttribute('aria-label', 'Đóng Shared Drive Backup');
      }
      syncModalTheme();
    });
  }
  adjustModalPosition();
  window.addEventListener('resize', adjustModalPosition);
  showTab('overview'); refresh().catch(error => message(error.message, true)); checkUpdates();
  const updateTimer = setInterval(() => { if (!root.isConnected) { clearInterval(updateTimer); return; } if (!document.hidden) checkUpdates(); }, 60000);
  const timer = setInterval(() => { if (!root.isConnected) { clearInterval(timer); return; } if (document.hidden || find('folder-dialog').open || find('confirm-dialog').open) return; const selected = root.querySelector('[role=tab][aria-selected=true]'); if (selected && selected.dataset.tab === 'overview') refresh().catch(() => {}); if (selected && selected.dataset.tab === 'history') loadHistory().catch(() => {}); }, 7000);
})();
