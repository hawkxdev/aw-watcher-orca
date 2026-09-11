// Orca Project Statistics Web Client (Stage R4)
// Strictly vanilla JS with no external dependencies or remote requests.

'use strict';

(function () {
  // === Application State ===
  const state = {
    token: '',
    currentRequestId: 0,
    currentReportId: null,
  };

  // === Time Formatting Helpers (UI-08: values from API, formatting only) ===

  function formatSecondsToHms(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) {
      return '—';
    }
    const sec = Math.round(totalSeconds);
    const hrs = Math.floor(sec / 3600);
    const mins = Math.floor((sec % 3600) / 60);
    const remainingSec = sec % 60;

    const parts = [];
    if (hrs > 0) {
      parts.push(`${hrs}ч`);
    }
    if (mins > 0 || hrs > 0) {
      parts.push(`${mins}м`);
    }
    parts.push(`${remainingSec}с`);
    return parts.join(' ');
  }

  function formatMicrosecondsToHms(durationUs) {
    if (durationUs === null || durationUs === undefined) {
      return '—';
    }
    return formatSecondsToHms(durationUs / 1000000);
  }

  // === State Machine (OUT-06) ===

  function setPageState(pageState, message = '') {
    const root = document.getElementById('app-root') || document.body;
    root.setAttribute('data-state', pageState);

    const statusContainer = document.getElementById('status-container');
    const statusMsg = document.getElementById('status-message');
    const resultsArea = document.getElementById('results-area');

    if (pageState === 'loading') {
      statusMsg.className = 'status-message loading';
      statusMsg.textContent = message || 'Вычисление отчёта...';
      statusContainer.classList.remove('hidden');
    } else if (pageState === 'result') {
      statusMsg.className = 'status-message';
      statusMsg.textContent = '';
      statusContainer.classList.add('hidden');
      resultsArea.classList.remove('hidden');
    } else if (pageState === 'conflict') {
      // [Fix 7] OUT-06: the conflict is a distinct reachable state that still
      // shows the per-source result (UI-06: separate sums remain visible).
      statusMsg.className = 'status-message error';
      statusMsg.textContent = message || 'Конфликт источников: общий итог скрыт, суммы показаны раздельно.';
      statusContainer.classList.remove('hidden');
      resultsArea.classList.remove('hidden');
    } else if (pageState === 'empty') {
      statusMsg.className = 'status-message empty';
      statusMsg.textContent = message || 'За выбранный период нет учтённой активности Orca.';
      statusContainer.classList.remove('hidden');
      resultsArea.classList.add('hidden');
    } else if (pageState === 'unauthorized') {
      statusMsg.className = 'status-message error';
      statusMsg.textContent = message || 'Ошибка авторизации. Требуется токен процесса.';
      statusContainer.classList.remove('hidden');
      resultsArea.classList.add('hidden');
      document.getElementById('auth-banner').classList.remove('hidden');
      document.getElementById('load-btn').disabled = true;
      document.getElementById('compare-btn').disabled = true;
    } else {
      // Error / diagnostic states: incomplete, conflict, api_unavailable, busy, limit_exceeded, expired, etc.
      statusMsg.className = 'status-message error';
      statusMsg.textContent = message || `Состояние: ${pageState}`;
      statusContainer.classList.remove('hidden');
      resultsArea.classList.add('hidden');
    }
  }


  // SEC-04: never render the full hostname — strip the domain part.
  function shortHostLabel(value) {
    return String(value || '').replace(/\.local$/, '');
  }

  // === Field Error Wiring (OUT-08: errors are tied to inputs) ===

  const FIELD_IDS = ['start-date-input', 'end-date-input', 'zone-select', 'project-filter'];

  function setFieldError(fieldId) {
    const field = document.getElementById(fieldId);
    if (!field) return;
    field.setAttribute('aria-invalid', 'true');
    field.setAttribute('aria-describedby', 'status-message');
  }

  function clearFieldErrors() {
    for (const id of FIELD_IDS) {
      const field = document.getElementById(id);
      if (!field) continue;
      field.removeAttribute('aria-invalid');
      field.removeAttribute('aria-describedby');
    }
  }

  // === Safe DOM Helpers (OUT-08, SEC-04) ===

  function createTextElement(tagName, text, className = '') {
    const el = document.createElement(tagName);
    if (className) {
      el.className = className;
    }
    el.textContent = text;
    return el;
  }

  // === Timezone Initialization (UI-03) ===

  function initTimezoneSelect() {
    const select = document.getElementById('zone-select');
    let zones = [];

    if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
      try {
        zones = Intl.supportedValuesOf('timeZone');
      } catch (e) {
        zones = [];
      }
    }

    if (!zones || zones.length === 0) {
      zones = [
        'Europe/Minsk',
        'UTC',
        'Europe/Moscow',
        'Europe/Kyiv',
        'Europe/Warsaw',
        'Europe/London',
        'America/New_York',
        'Asia/Tokyo',
      ];
    }

    if (!zones.includes('Europe/Minsk')) {
      zones.unshift('Europe/Minsk');
    }

    select.innerHTML = '';
    for (const z of zones) {
      const opt = document.createElement('option');
      opt.value = z;
      opt.textContent = z;
      if (z === 'Europe/Minsk') {
        opt.selected = true;
      }
      select.appendChild(opt);
    }
  }

  // === Default Dates Initialization ===

  function initDefaultDates() {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    const todayStr = `${yyyy}-${mm}-${dd}`;

    const startInput = document.getElementById('start-date-input');
    const endInput = document.getElementById('end-date-input');

    if (!startInput.value) {
      startInput.value = todayStr;
    }
    if (!endInput.value) {
      endInput.value = todayStr;
    }
  }

  // === Token Lifecycle (SEC-01) ===

  function initToken() {
    const hash = window.location.hash;
    if (hash && hash.includes('token=')) {
      const match = hash.match(/token=([a-zA-Z0-9_-]+)/);
      if (match && match[1]) {
        state.token = match[1];
        // Clean fragment from URL immediately
        history.replaceState(null, '', window.location.pathname + window.location.search);
      }
    }

    if (!state.token) {
      setPageState('unauthorized');
    }
  }

  // === API Request Helper ===

  async function apiFetch(endpoint, options = {}) {
    const headers = {
      'Accept': 'application/json',
      'X-Report-Token': state.token,
      ...(options.headers || {}),
    };

    const response = await fetch(endpoint, {
      ...options,
      headers,
    });

    let data;
    try {
      data = await response.json();
    } catch (err) {
      data = { status: 'error', reason_code: 'malformed_response', message: 'Не удалось разобрать ответ сервиса' };
    }

    return {
      status: response.status,
      ok: response.ok,
      data,
    };
  }

  // === Check Initial Service Status ===

  async function checkServiceStatus() {
    if (!state.token) return;

    try {
      const res = await apiFetch('/api/status');
      const badge = document.getElementById('service-status-badge');
      if (res.ok && res.data.status === 'ok') {
        badge.className = 'badge badge-success';
        badge.textContent = `Сервис активен (${res.data.version || '0.1.0'})`;
      } else {
        badge.className = 'badge badge-danger';
        badge.textContent = 'Ошибка сервиса';
      }
    } catch (err) {
      const badge = document.getElementById('service-status-badge');
      badge.className = 'badge badge-danger';
      badge.textContent = 'Сервис недоступен';
    }
  }

  // === Report Rendering (UI-01..08, OUT-01..08) ===

  function renderReport(report) {
    state.currentReportId = report.report_id;

    // Enable comparison button (UI-07)
    const compareBtn = document.getElementById('compare-btn');
    compareBtn.disabled = false;

    // 1. Meta bar
    // [Fix 2] The label shows the CALENDAR period the user selected (UI-03),
    // not the UTC moment of local midnight (start_utc/end_utc are UTC dates).
    const selectedStart = document.getElementById('start-date-input').value;
    const selectedEnd = document.getElementById('end-date-input').value;
    document.getElementById('meta-period').textContent = `${selectedStart} — ${selectedEnd} (${report.zone_name})`;

    // [Fix 1] UI-06/TIME-15: the overall total is a FORBIDDEN aggregate when
    // combined_allowed=false (incomplete source sum, cross-host conflict,
    // unknown boundary). Never derive it from partial rows.
    const totalDurationEl = document.getElementById('meta-total-duration');
    if (!report.combined_allowed) {
      totalDurationEl.textContent = '—';
    } else {
      let totalSeconds = 0;
      for (const p of report.project_rows) {
        totalSeconds += (p.seconds || 0);
      }
      totalDurationEl.textContent = formatSecondsToHms(totalSeconds);
    }

    const boundaryText = report.production_boundary
      ? new Date(report.production_boundary).toLocaleString('ru-RU', { timeZone: report.zone_name })
      : (report.boundary_status === 'absent_unverified'
        ? 'Боевая история не найдена, её прежнее существование не проверено'
        : 'Не установлена');
    document.getElementById('meta-boundary').textContent = boundaryText;
    document.getElementById('meta-sources-count').textContent = `${report.source_summaries.length}`;

    // 2. Conflict Banner (UI-06)
    const conflictNotice = document.getElementById('conflict-notice');
    if (!report.combined_allowed) {
      conflictNotice.classList.remove('hidden');
      document.getElementById('conflict-reason-text').textContent =
        ` ${report.combined_prohibition_reason || 'Обнаружено пересечение данных'}. Итоги показаны раздельно.`;
    } else {
      conflictNotice.classList.add('hidden');
    }

    // 3. Freshness Warning Banner (OUT-07)
    const freshnessBanner = document.getElementById('freshness-banner');
    if (report.freshness && report.freshness.stale) {
      // Check if period includes observed_until date
      const obsDateStr = report.observed_until.split('T')[0];
      const selectedStart = document.getElementById('start-date-input').value;
      const selectedEnd = document.getElementById('end-date-input').value;
      const periodIncludesObserved = (obsDateStr >= selectedStart && obsDateStr <= selectedEnd);

      if (periodIncludesObserved) {
        freshnessBanner.classList.remove('hidden');
        const lastUpStr = report.freshness.last_updated
          ? new Date(report.freshness.last_updated).toLocaleTimeString('ru-RU')
          : '—';
        document.getElementById('freshness-details').textContent =
          `Последнее событие получено более 30 сек. назад (last_updated: ${lastUpStr}). Сборщик может быть неактивен.`;
      } else {
        freshnessBanner.classList.add('hidden');
      }
    } else {
      freshnessBanner.classList.add('hidden');
    }

    // 4. Projects and Worktrees Table
    const tbody = document.getElementById('projects-table-body');
    tbody.innerHTML = '';

    if (report.project_rows.length === 0) {
      // OUT-06: the previous report's daily summary and source diagnostics
      // must never survive under the new header.
      document.getElementById('daily-section').classList.add('hidden');
      document.getElementById('daily-table-body').innerHTML = '';
      document.getElementById('daily-chart').innerHTML = '';
      document.getElementById('sources-table-body').innerHTML = '';
      if (!report.combined_allowed) {
        setPageState('conflict', 'Конфликт источников: точные суммы недоступны, причина — в баннере выше.');
      } else {
        setPageState('empty', 'Нет проектов, соответствующих заданным критериям.');
      }
      return;
    }

    // [Fix 1] TIME-13 grouping is only a display aggregation; when combining
    // is forbidden the rows come from sources that must not be summed.
    // project_rows carry no source kind/host, so flat rendering is the only
    // honest presentation: no merging, no cross-source summation.
    if (!report.combined_allowed) {
      for (const p of report.project_rows) {
        const tr = document.createElement('tr');
        const tdName = document.createElement('td');
        tdName.appendChild(document.createTextNode(`${p.repo} — ${p.worktree}`));
        tr.appendChild(tdName);
        tr.appendChild(createTextElement('td', formatSecondsToHms(p.seconds)));
        tr.appendChild(createTextElement('td', (p.seconds || 0).toFixed(1)));
        tbody.appendChild(tr);
      }
    } else {
      // Group rows by repository (TIME-13) with collapsible worktrees.
      const repoMap = new Map();
      for (const p of report.project_rows) {
        if (!repoMap.has(p.repo)) {
          repoMap.set(p.repo, []);
        }
        repoMap.get(p.repo).push(p);
      }

      for (const [repoName, worktrees] of repoMap.entries()) {
        let repoSec = 0;
        for (const wt of worktrees) {
          repoSec += (wt.seconds || 0);
        }

        // [Fix 4] Expandable row: keyboard-accessible disclosure with
        // aria-expanded, worktree sub-rows hidden until expanded.
        const tr = document.createElement('tr');
        tr.className = 'project-row';
        tr.setAttribute('tabindex', '0');
        tr.setAttribute('role', 'button');
        tr.setAttribute('aria-expanded', 'false');

        const tdName = document.createElement('td');
        tdName.appendChild(document.createTextNode(repoName));
        if (worktrees.length > 1) {
          const badge = createTextElement('span', ` (${worktrees.length} worktree)`, 'meta-label');
          tdName.appendChild(badge);
        }
        tr.appendChild(tdName);

        tr.appendChild(createTextElement('td', formatSecondsToHms(repoSec)));
        tr.appendChild(createTextElement('td', repoSec.toFixed(1)));
        tbody.appendChild(tr);

        const wtRows = [];
        for (const wt of worktrees) {
          const wtTr = document.createElement('tr');
          wtTr.className = 'worktree-details hidden';

          const tdWt = document.createElement('td');
          tdWt.appendChild(document.createTextNode(`↳ ${wt.worktree}`));
          wtTr.appendChild(tdWt);

          wtTr.appendChild(createTextElement('td', formatSecondsToHms(wt.seconds)));
          wtTr.appendChild(createTextElement('td', (wt.seconds || 0).toFixed(1)));
          tbody.appendChild(wtTr);
          wtRows.push(wtTr);
        }

        const toggleWorktrees = () => {
          const expanded = tr.getAttribute('aria-expanded') === 'true';
          tr.setAttribute('aria-expanded', expanded ? 'false' : 'true');
          for (const wtTr of wtRows) {
            wtTr.classList.toggle('hidden', expanded);
          }
        };
        tr.addEventListener('click', toggleWorktrees);
        tr.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleWorktrees();
          }
        });
      }
    }

    // 5. Daily Summary & Chart (UI-08)
    const dailySection = document.getElementById('daily-section');
    const dailyTbody = document.getElementById('daily-table-body');
    const dailyChart = document.getElementById('daily-chart');
    dailyTbody.innerHTML = '';
    dailyChart.innerHTML = '';

    if (!report.combined_allowed) {
      // Hide or mark daily table when conflict prevents overall sum
      dailySection.classList.add('hidden');
    } else {
      dailySection.classList.remove('hidden');

      const maxDailySec = Math.max(...report.daily_rows.map(d => d.seconds || 0), 1);

      for (const d of report.daily_rows) {
        // Table row
        const tr = document.createElement('tr');
        tr.appendChild(createTextElement('td', d.day));
        tr.appendChild(createTextElement('td', formatSecondsToHms(d.seconds)));
        tr.appendChild(createTextElement('td', (d.seconds || 0).toFixed(1)));
        tr.appendChild(createTextElement('td', String(d.contributing_events || 0)));
        tr.appendChild(createTextElement('td', String(d.derived_segments || 0)));
        dailyTbody.appendChild(tr);

        // Chart bar column
        const col = document.createElement('div');
        col.className = 'chart-bar-column';

        const valSpan = createTextElement('span', formatSecondsToHms(d.seconds), 'chart-value');
        col.appendChild(valSpan);

        const wrapper = document.createElement('div');
        wrapper.className = 'chart-bar-wrapper';

        const bar = document.createElement('div');
        bar.className = 'chart-bar';
        const heightPct = Math.max(4, Math.round(((d.seconds || 0) / maxDailySec) * 100));
        bar.style.height = `${heightPct}%`;
        bar.title = `${d.day}: ${formatSecondsToHms(d.seconds)}`;
        wrapper.appendChild(bar);
        col.appendChild(wrapper);

        const labelSpan = createTextElement('span', d.day.slice(5), 'chart-label');
        col.appendChild(labelSpan);

        dailyChart.appendChild(col);
      }
    }

    // 6. Source Summaries Diagnostics (Second Level Details, OUT-02)
    const c = report.counters;
    document.getElementById('counters-line').textContent =
      `События: сырые ${c.raw_events} · в периоде ${c.period_events} · ` +
      `по фильтру ${c.matching_events} · учтённые ${c.contributing_events} · ` +
      `сегменты ${c.derived_segments}`;
    const srcTbody = document.getElementById('sources-table-body');
    srcTbody.innerHTML = '';
    document.getElementById('sources-summary-count').textContent = String(report.source_summaries.length);

    for (const s of report.source_summaries) {
      const tr = document.createElement('tr');
      tr.appendChild(createTextElement('td', s.alias));
      tr.appendChild(createTextElement('td', shortHostLabel(s.host_suffix)));
      tr.appendChild(createTextElement('td', s.source_kind));
      tr.appendChild(createTextElement('td', formatSecondsToHms(s.project_seconds)));
      tr.appendChild(createTextElement('td', formatMicrosecondsToHms(s.quality.neutral_us)));
      tr.appendChild(createTextElement('td', formatMicrosecondsToHms(s.quality.confirmed_afk_us)));
      tr.appendChild(createTextElement('td', formatMicrosecondsToHms(s.quality.unknown_afk_us)));
      tr.appendChild(createTextElement('td', formatMicrosecondsToHms(s.quality.noise_us)));

      const tdStatus = document.createElement('td');
      if (s.is_conflict) {
        tdStatus.appendChild(createTextElement('span', `Конфликт: ${s.conflict_reason || ''}`, 'badge badge-danger'));
      } else if (!s.quality.is_reliable) {
        tdStatus.appendChild(createTextElement('span', `Неполнота: ${s.quality.unreliable_reason || ''}`, 'badge badge-warning'));
      } else {
        tdStatus.appendChild(createTextElement('span', 'Достоверно', 'badge badge-success'));
      }
      tr.appendChild(tdStatus);
      srcTbody.appendChild(tr);
    }

    // [Fix 7] conflict is a reachable, distinguishable data-state (OUT-06)
    setPageState(report.combined_allowed ? 'result' : 'conflict');
  }

  // === Load Report Action (UI-01, OUT-06) ===

  async function loadReport() {
    if (!state.token) {
      setPageState('unauthorized');
      return;
    }

    const startDate = document.getElementById('start-date-input').value;
    const endDate = document.getElementById('end-date-input').value;
    const zoneName = document.getElementById('zone-select').value;
    const projectFilter = document.getElementById('project-filter').value.trim() || null;

    clearFieldErrors();

    if (!startDate || !endDate) {
      if (!startDate) {
        setFieldError('start-date-input');
      }
      if (!endDate) {
        setFieldError('end-date-input');
      }
      setPageState('error', 'Пожалуйста, выберите даты начала и окончания.');
      return;
    }

    const reqId = ++state.currentRequestId;
    setPageState('loading', 'Вычисление статистики активности...');

    // Hide comparison section from previous report
    document.getElementById('comparison-section').classList.add('hidden');

    try {
      const payload = {
        start_date: startDate,
        end_date: endDate,
        zone_name: zoneName,
        project_filter: projectFilter,
        exact_project_match: false,
      };

      const res = await apiFetch('/api/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      // Ignore late / superseded response (OUT-06)
      if (reqId !== state.currentRequestId) {
        return;
      }

      if (res.ok && res.data.status === 'ok' && res.data.report) {
        renderReport(res.data.report);
      } else {
        const reason = res.data.reason_code || 'error';
        const msg = res.data.message || 'Ошибка вычисления отчёта';

        // [Fix 9] OUT-08: validation errors are tied to the offending input
        if (reason === 'invalid_parameters') {
          if (/зон/i.test(msg) || /timezone/i.test(msg) || /IANA/i.test(msg)) {
            setFieldError('zone-select');
          } else {
            setFieldError('start-date-input');
            setFieldError('end-date-input');
          }
        }

        if (reason === 'unauthorized_token') {
          setPageState('unauthorized', msg);
        } else if (reason === 'limit_exceeded') {
          setPageState('limit_exceeded', `Превышен лимит: ${msg}`);
        } else if (reason === 'incomplete_source') {
          setPageState('incomplete', `Неполнота источников: ${msg}`);
        } else if (reason === 'calculation_busy') {
          setPageState('busy', 'Сервер занят другим расчётом. Попробуйте снова через несколько секунд.');
        } else if (reason === 'api_unavailable') {
          setPageState('api_unavailable', `ActivityWatch недоступен: ${msg}`);
        } else {
          setPageState(reason, msg);
        }
      }
    } catch (err) {
      if (reqId === state.currentRequestId) {
        setPageState('api_unavailable', 'Не удалось связаться с локальным сервисом отчётов.');
      }
    }
  }

  // === Standard Window Comparison Action (UI-07, TIME-16) ===

  async function loadComparison() {
    if (!state.currentReportId) return;

    const compSection = document.getElementById('comparison-section');
    const compStatusText = document.getElementById('comparison-status-text');
    const compTbody = document.getElementById('comparison-table-body');

    compSection.classList.remove('hidden');
    compStatusText.textContent = 'Загрузка данных штатного сравнения...';
    compTbody.innerHTML = '';

    // [Fix 5] Capture the request epoch and report identity BEFORE the await;
    // a late answer for a superseded report must never touch the view (OUT-06).
    const reqId = state.currentRequestId;
    const repId = state.currentReportId;

    try {
      const res = await apiFetch('/api/comparison', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ report_id: repId }),
      });

      // Ignore late / superseded comparison responses (OUT-06)
      if (reqId !== state.currentRequestId || repId !== state.currentReportId) {
        return;
      }

      if (res.ok && res.data.status === 'ok' && res.data.comparison) {
        const comp = res.data.comparison;

        if (!comp.is_available) {
          compStatusText.textContent = `Штатные источники недоступны: ${comp.unavailable_reason || 'нет данных'}`;
          return;
        }

        if (comp.has_cross_host_conflict) {
          compStatusText.textContent = `Обнаружен конфликт штатных окон между хостами: ${comp.conflict_reason || ''}`;
        } else {
          compStatusText.textContent = `Общее штатное время Orca: ${formatSecondsToHms(comp.standard_seconds)}`;
        }

        for (const h of comp.host_rows) {
          const tr = document.createElement('tr');
          tr.appendChild(createTextElement('td', shortHostLabel(h.host_suffix)));
          tr.appendChild(createTextElement('td', shortHostLabel(h.bucket_id)));
          tr.appendChild(createTextElement('td', formatSecondsToHms(h.seconds)));
          tr.appendChild(createTextElement('td', String(h.event_count || 0)));
          compTbody.appendChild(tr);
        }
      } else {
        const reason = res.data.reason_code;
        if (reason === 'expired_report_id') {
          compStatusText.textContent = 'Снимок отчёта истёк. Пожалуйста, обновите отчёт.';
        } else if (reason === 'unknown_report_id') {
          compStatusText.textContent = 'Отчёт не найден на сервере. Пожалуйста, обновите отчёт.';
        } else {
          compStatusText.textContent = `Ошибка сравнения: ${res.data.message || reason}`;
        }
        // [Fix 7] Distinct machine state for the snapshot-expiry family (OUT-06)
        const root = document.getElementById('app-root') || document.body;
        root.setAttribute('data-state', `comparison_${reason || 'error'}`);
      }
    } catch (err) {
      compStatusText.textContent = 'Ошибка при запросе штатного сравнения.';
    }
  }

  // === Parameter Change Guard (OUT-06, plan 4.4) ===

  function markParamsChanged() {
    // [Fix 6] New parameters must never show under the old report heading:
    // hide the previous result immediately and say why.
    // [Fix 2/7] Invalidate any in-flight request and remember whether the
    // result was actually visible before hiding it.
    state.currentRequestId += 1;
    state.currentReportId = null;
    document.getElementById('compare-btn').disabled = true;
    document.getElementById('comparison-section').classList.add('hidden');

    const resultsArea = document.getElementById('results-area');
    const wasVisible = !resultsArea.classList.contains('hidden');
    resultsArea.classList.add('hidden');

    if (wasVisible) {
      const root = document.getElementById('app-root') || document.body;
      root.setAttribute('data-state', 'stale_parameters');
      const statusMsg = document.getElementById('status-message');
      statusMsg.className = 'status-message empty';
      statusMsg.textContent = 'Параметры изменены — прежний отчёт скрыт. Нажмите «Загрузить отчёт» для пересчёта.';
      document.getElementById('status-container').classList.remove('hidden');
    }
  }

  // === Event Listeners and Initialization ===

  function initEventListeners() {
    const loadBtn = document.getElementById('load-btn');
    loadBtn.addEventListener('click', loadReport);

    const compareBtn = document.getElementById('compare-btn');
    compareBtn.addEventListener('click', loadComparison);

    const clearFilterBtn = document.getElementById('clear-filter-btn');
    clearFilterBtn.addEventListener('click', () => {
      document.getElementById('project-filter').value = '';
      loadReport();
    });

    const projectFilterInput = document.getElementById('project-filter');
    projectFilterInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        loadReport();
      }
    });
    // [Fix 6] typing in the filter also invalidates the shown result
    projectFilterInput.addEventListener('input', markParamsChanged);

    // [Fix 6] Invalidate/hide current result immediately on parameter change
    const inputs = ['start-date-input', 'end-date-input', 'zone-select'];
    for (const id of inputs) {
      document.getElementById(id).addEventListener('change', markParamsChanged);
    }
  }

  // === Boot ===

  function initApp() {
    initTimezoneSelect();
    initDefaultDates();
    initToken();
    initEventListeners();
    checkServiceStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }
})();
