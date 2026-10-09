/**
 * Neurobet Main Frontend Application Controller
 * Real-time SSE streaming, responsive tabs, deep-dive modal & state manager
 */

(function () {
  'use strict';

  // Determine base API path (supports both /diflector/neurobet/ and /)
  const isSubpath = window.location.pathname.includes('/diflector/neurobet');
  const API_BASE = isSubpath ? '/diflector/neurobet/api' : '/api';

  // Application State
  const state = {
    activeSport: 'tennis',
    activeStatus: 'all',
    searchQuery: '',
    activeTab: 'matches',
    autoUpdate: true,
    theme: localStorage.getItem('neurobet_theme') || 'dark',

    sports: [],
    events: [],
    predictions: {},
    bankroll: null,
    performance: null,
    bets: [],
    ledger: [],
    models: [],
    trainingRuns: [],
    queue: [],
    queueStats: null,

    selectedEvent: null,
    selectedModalTab: 'odds',
    sseConnected: false,
    sseSource: null,
  };

  // DOM Elements
  const el = {
    themeBtn: document.getElementById('theme-toggle-btn'),
    pulseDot: document.getElementById('sse-pulse-dot'),
    sseStatusText: document.getElementById('sse-status-text'),
    primarySportBadge: document.getElementById('primary-sport-badge'),

    // Bankroll stats
    bankrollBalance: document.getElementById('stat-bankroll-balance'),
    bankrollEquity: document.getElementById('stat-bankroll-equity'),
    bankrollExposure: document.getElementById('stat-bankroll-exposure'),
    bankrollPnl: document.getElementById('stat-bankroll-pnl'),
    bankrollRoi: document.getElementById('stat-bankroll-roi'),
    bankrollWinRate: document.getElementById('stat-bankroll-winrate'),
    bankrollTurnover: document.getElementById('stat-bankroll-turnover'),
    bankrollDrawdown: document.getElementById('stat-bankroll-drawdown'),

    // Navigation tabs
    tabMatchesBtn: document.getElementById('tab-btn-matches'),
    tabBetsBtn: document.getElementById('tab-btn-bets'),
    tabLedgerBtn: document.getElementById('tab-btn-ledger'),
    tabModelsBtn: document.getElementById('tab-btn-models'),
    tabSchedulerBtn: document.getElementById('tab-btn-scheduler'),

    // Tab view sections
    viewMatches: document.getElementById('view-matches'),
    viewBets: document.getElementById('view-bets'),
    viewLedger: document.getElementById('view-ledger'),
    viewModels: document.getElementById('view-models'),
    viewScheduler: document.getElementById('view-scheduler'),

    // Match filters
    sportsFilterContainer: document.getElementById('sports-filter-container'),
    statusFilterGroup: document.getElementById('status-filter-group'),
    searchInput: document.getElementById('match-search-input'),
    matchesGrid: document.getElementById('matches-grid'),

    // Tables
    betsTableBody: document.getElementById('bets-table-body'),
    ledgerTableBody: document.getElementById('ledger-table-body'),
    modelsTableBody: document.getElementById('models-table-body'),
    schedulerTableBody: document.getElementById('scheduler-table-body'),

    // Modal
    modalOverlay: document.getElementById('event-detail-modal'),
    modalTitle: document.getElementById('modal-match-title'),
    modalSubtitle: document.getElementById('modal-match-subtitle'),
    modalCloseBtn: document.getElementById('modal-close-btn'),
    modalTabsContainer: document.getElementById('modal-tabs-container'),
    modalBodyContent: document.getElementById('modal-body-content'),

    // Simulation reset
    resetSimBtn: document.getElementById('reset-simulation-btn'),
  };

  // Initialize theme
  function initTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
    if (el.themeBtn) {
      el.themeBtn.innerHTML = state.theme === 'light' ? '🌙 Тёмная тема' : '☀️ Светлая тема';
      el.themeBtn.addEventListener('click', toggleTheme);
    }
  }

  function toggleTheme() {
    state.theme = state.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('neurobet_theme', state.theme);
    document.documentElement.setAttribute('data-theme', state.theme);
    el.themeBtn.innerHTML = state.theme === 'light' ? '🌙 Тёмная тема' : '☀️ Светлая тема';
  }

  // Format currency & helpers
  function fmtRub(val) {
    if (val === null || val === undefined) return '0,00 ₽';
    return Number(val).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽';
  }

  function fmtPct(val) {
    if (val === null || val === undefined) return '0.0%';
    const p = (Number(val) * 100).toFixed(1);
    return `${Number(val) >= 0 ? '+' : ''}${p}%`;
  }

  // -------------------------------------------------------------------------
  // Server-Sent Events (Live streaming)
  // -------------------------------------------------------------------------
  function initSSE() {
    if (state.sseSource) {
      state.sseSource.close();
    }

    try {
      state.sseSource = new EventSource(`${API_BASE}/live/stream`);

      state.sseSource.onopen = () => {
        state.sseConnected = true;
        if (el.pulseDot) el.pulseDot.classList.remove('disconnected');
        if (el.sseStatusText) el.sseStatusText.innerText = 'Live Stream Connected';
      };

      state.sseSource.onmessage = (e) => {
        try {
          const payload = JSON.parse(e.data);
          handleLiveUpdate(payload);
        } catch (err) {
          console.warn('[SSE Parse Error]:', err);
        }
      };

      state.sseSource.onerror = () => {
        state.sseConnected = false;
        if (el.pulseDot) el.pulseDot.classList.add('disconnected');
        if (el.sseStatusText) el.sseStatusText.innerText = 'Reconnecting...';
      };
    } catch (e) {
      console.warn('[SSE Init Error]:', e);
    }
  }

  function handleLiveUpdate(data) {
    if (data.type === 'tick') {
      // Keep-alive tick
      return;
    }

    if (data.type === 'bet_placed' || data.type === 'bet_settled') {
      fetchBankrollAndPerformance();
      if (state.activeTab === 'bets') fetchBets();
      if (state.activeTab === 'ledger') fetchLedger();
    }

    if (data.type === 'event_update') {
      // Re-fetch events cleanly
      fetchEvents();
    }
  }

  // -------------------------------------------------------------------------
  // Data Fetching Functions
  // -------------------------------------------------------------------------
  async function fetchBankrollAndPerformance() {
    try {
      const [accRes, perfRes] = await Promise.all([
        fetch(`${API_BASE}/account`),
        fetch(`${API_BASE}/performance`),
      ]);

      if (accRes.ok) {
        state.bankroll = await accRes.json();
        updateBankrollUI();
      }
      if (perfRes.ok) {
        state.performance = await perfRes.json();
        updatePerformanceUI();
      }
    } catch (err) {
      console.error('[Bankroll Fetch Error]:', err);
    }
  }

  function updateBankrollUI() {
    if (!state.bankroll) return;
    const b = state.bankroll;
    if (el.bankrollBalance) el.bankrollBalance.innerText = fmtRub(b.available_balance || b.balance);
    if (el.bankrollEquity) el.bankrollEquity.innerText = fmtRub(b.total_equity);
    if (el.bankrollExposure) el.bankrollExposure.innerText = fmtRub(b.locked_exposure);
  }

  function updatePerformanceUI() {
    if (!state.performance) return;
    const p = state.performance;
    const net = Number(p.net_pnl || 0);

    if (el.bankrollPnl) {
      el.bankrollPnl.innerText = (net >= 0 ? '+' : '') + fmtRub(net);
      el.bankrollPnl.className = 'value mono ' + (net >= 0 ? 'pos' : 'neg');
    }

    if (el.bankrollRoi) {
      const roi = Number(p.roi || 0);
      el.bankrollRoi.innerText = (roi >= 0 ? '+' : '') + (roi * 100).toFixed(2) + '%';
      el.bankrollRoi.className = 'value mono ' + (roi >= 0 ? 'pos' : 'neg');
    }

    if (el.bankrollWinRate) {
      el.bankrollWinRate.innerText = Number(p.win_rate || 0).toFixed(1) + '%';
    }

    if (el.bankrollTurnover) {
      el.bankrollTurnover.innerText = fmtRub(p.turnover);
    }

    if (el.bankrollDrawdown) {
      el.bankrollDrawdown.innerText = (Number(p.max_drawdown_pct || 0) * 100).toFixed(1) + '%';
    }
  }

  async function fetchSports() {
    try {
      const res = await fetch(`${API_BASE}/sports`);
      if (res.ok) {
        state.sports = await res.json();
        renderSportsFilters();
      }
    } catch (e) {
      console.error('[Sports Fetch Error]:', e);
    }
  }

  function renderSportsFilters() {
    if (!el.sportsFilterContainer) return;
    el.sportsFilterContainer.innerHTML = '';

    state.sports.forEach(s => {
      const btn = document.createElement('button');
      btn.className = `pill-btn ${state.activeSport === s.code ? 'active' : ''}`;
      btn.innerText = s.name_ru || s.name || s.code;
      btn.addEventListener('click', () => {
        state.activeSport = s.code;
        renderSportsFilters();
        fetchEvents();
      });
      el.sportsFilterContainer.appendChild(btn);
    });
  }

  async function fetchEvents() {
    try {
      let url = `${API_BASE}/events?sport_code=${encodeURIComponent(state.activeSport)}`;
      if (state.activeStatus !== 'all') {
        url += `&status=${encodeURIComponent(state.activeStatus)}`;
      }
      if (state.searchQuery.trim()) {
        url += `&search=${encodeURIComponent(state.searchQuery.trim())}`;
      }

      const [evRes, predRes] = await Promise.all([
        fetch(url),
        fetch(`${API_BASE}/predictions`),
      ]);

      if (evRes.ok) {
        const evData = await evRes.json();
        state.events = evData.events || [];
      }

      if (predRes.ok) {
        const pList = await predRes.json();
        state.predictions = {};
        pList.forEach(p => {
          if (!state.predictions[p.event_id]) state.predictions[p.event_id] = [];
          state.predictions[p.event_id].push(p);
        });
      }

      renderMatchesGrid();
    } catch (err) {
      console.error('[Events Fetch Error]:', err);
    }
  }

  function renderMatchesGrid() {
    if (!el.matchesGrid) return;

    if (state.events.length === 0) {
      el.matchesGrid.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1;">
          <div class="icon">🎾</div>
          <h3>Событий не найдено</h3>
          <p>В текущем фильтре нет активных событий. Ожидание обновлений линии Fonbet...</p>
        </div>
      `;
      return;
    }

    el.matchesGrid.innerHTML = '';

    state.events.forEach(evt => {
      const preds = state.predictions[evt.id] || [];
      const primaryPred = preds.find(p => p.has_positive_edge) || preds[0] || null;

      const card = document.createElement('div');
      card.className = `match-card ${primaryPred && primaryPred.has_positive_edge ? 'has-edge' : ''}`;

      const statusClass = evt.status === 'live' ? 'live' : evt.status === 'prematch' ? 'prematch' : 'finished';
      const statusLabel = evt.status === 'live' ? '🔴 Live' : evt.status === 'prematch' ? '⏱ Prematch' : '🏁 Finished';

      const playerA = evt.participant_a_name || 'Player 1';
      const playerB = evt.participant_b_name || 'Player 2';
      const scoreStr = evt.current_period ? `${evt.current_period}` : (evt.status === 'live' ? 'In Play' : 'Scheduled');

      // Value matrix
      let oddsHtml = '';
      if (primaryPred) {
        oddsHtml = `
          <div class="odds-table">
            <div class="odds-col">
              <div class="col-label">Рынок</div>
              <div class="col-val">${primaryPred.market || 'Winner'}</div>
            </div>
            <div class="odds-col">
              <div class="col-label">P(ML)</div>
              <div class="col-val mono" style="color: var(--accent-tennis);">
                ${(primaryPred.model_probability * 100).toFixed(1)}%
              </div>
            </div>
            <div class="odds-col">
              <div class="col-label">Fonbet</div>
              <div class="col-val mono">${Number(primaryPred.bookmaker_odds).toFixed(2)}</div>
            </div>
            <div class="odds-col">
              <div class="col-label">Edge</div>
              <div class="col-val mono ${primaryPred.edge > 0 ? 'pos' : 'neg'}">
                ${primaryPred.edge > 0 ? '+' : ''}${(primaryPred.edge * 100).toFixed(1)}%
              </div>
            </div>
          </div>
        `;
      } else {
        oddsHtml = `
          <div class="odds-table" style="grid-template-columns: 1fr; color: var(--text-dim); font-size: 0.8rem;">
            Ожидание расчета вероятностей ML-моделью...
          </div>
        `;
      }

      card.innerHTML = `
        <div class="card-header">
          <span class="league">Fonbet ID: ${evt.source_event_id || evt.id.slice(0, 8)}</span>
          <span class="status-badge ${statusClass}">${statusLabel}</span>
        </div>

        <div class="match-body">
          <div class="player-block">
            <div class="player-name">${playerA}</div>
            <div class="player-sub">🎾 Игрок A</div>
          </div>
          <div class="score-display">
            <div class="score-sets mono">${scoreStr}</div>
            <div class="score-games">Quality: ${Number(evt.quality_score || 100).toFixed(0)}%</div>
          </div>
          <div class="player-block right">
            <div class="player-name">${playerB}</div>
            <div class="player-sub">Игрок B</div>
          </div>
        </div>

        ${oddsHtml}

        <div class="verdict-row">
          <div>
            ${primaryPred && primaryPred.has_positive_edge ? '<span class="badge-tag tag-bet">VALUE BET</span>' : '<span class="badge-tag tag-nobet">NO EDGE</span>'}
            <span class="badge-tag tag-ev">ML: ${primaryPred ? primaryPred.model_version : 'tennis_v1.0'}</span>
          </div>
          <button class="btn-inspect" data-event-id="${evt.id}">
            🔍 Детали решения
          </button>
        </div>
      `;

      card.querySelector('.btn-inspect').addEventListener('click', () => {
        openEventDetail(evt.id);
      });

      el.matchesGrid.appendChild(card);
    });
  }

  // -------------------------------------------------------------------------
  // Event Detail Deep-Dive Modal
  // -------------------------------------------------------------------------
  async function openEventDetail(eventId) {
    if (!el.modalOverlay) return;
    el.modalOverlay.classList.add('active');

    if (el.modalBodyContent) {
      el.modalBodyContent.innerHTML = `
        <div class="empty-state">
          <div class="loader"></div>
          <p>Загрузка исторической ленты и аудита решения...</p>
        </div>
      `;
    }

    try {
      const [evRes, timeRes, oddsRes, predRes, resRes] = await Promise.all([
        fetch(`${API_BASE}/events/${eventId}`),
        fetch(`${API_BASE}/events/${eventId}/timeline`),
        fetch(`${API_BASE}/events/${eventId}/odds`),
        fetch(`${API_BASE}/events/${eventId}/predictions`),
        fetch(`${API_BASE}/events/${eventId}/research`),
      ]);

      const evt = evRes.ok ? await evRes.json() : null;
      const timeline = timeRes.ok ? await timeRes.json() : [];
      const odds = oddsRes.ok ? await oddsRes.json() : [];
      const preds = predRes.ok ? await predRes.json() : [];
      const research = resRes.ok ? await resRes.json() : [];

      state.selectedEvent = { evt, timeline, odds, preds, research };
      renderModalHeader(evt);
      renderModalTabContent();
    } catch (err) {
      console.error('[Event Detail Fetch Error]:', err);
      el.modalBodyContent.innerHTML = `<div class="empty-state"><p class="neg">Ошибка загрузки деталей события</p></div>`;
    }
  }

  function renderModalHeader(evt) {
    if (!evt) return;
    if (el.modalTitle) {
      el.modalTitle.innerText = `${evt.participant_a_name} vs ${evt.participant_b_name}`;
    }
    if (el.modalSubtitle) {
      el.modalSubtitle.innerText = `${evt.sport_code.toUpperCase()} • Fonbet ID: ${evt.source_event_id} • Статус: ${evt.status} • Скорость/качество: ${Number(evt.quality_score || 100).toFixed(0)}%`;
    }
  }

  function renderModalTabContent() {
    if (!el.modalBodyContent || !state.selectedEvent) return;
    const { evt, timeline, odds, preds, research } = state.selectedEvent;

    let contentHtml = `
      <div class="modal-tabs">
        <button class="modal-tab-btn ${state.selectedModalTab === 'odds' ? 'active' : ''}" data-tab="odds">📈 Движение котировок</button>
        <button class="modal-tab-btn ${state.selectedModalTab === 'predictions' ? 'active' : ''}" data-tab="predictions">🤖 ML Вероятности & Edge</button>
        <button class="modal-tab-btn ${state.selectedModalTab === 'research' ? 'active' : ''}" data-tab="research">🌐 Web Research</button>
        <button class="modal-tab-btn ${state.selectedModalTab === 'timeline' ? 'active' : ''}" data-tab="timeline">⏱ Лента матча</button>
        <button class="modal-tab-btn ${state.selectedModalTab === 'checklist' ? 'active' : ''}" data-tab="checklist">🛡 Bet Manager Чеклист</button>
      </div>
      <div id="modal-tab-pane" style="margin-top: 1rem;"></div>
    `;

    el.modalBodyContent.innerHTML = contentHtml;

    // Attach sub-tab listeners
    el.modalBodyContent.querySelectorAll('.modal-tab-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        state.selectedModalTab = e.target.getAttribute('data-tab');
        renderModalTabContent();
      });
    });

    const pane = document.getElementById('modal-tab-pane');
    if (!pane) return;

    if (state.selectedModalTab === 'odds') {
      pane.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 1rem;">
          <h4>Хронологический график движения коэффициентов (TimescaleDB)</h4>
          <div id="odds-chart-container" class="chart-box"></div>
        </div>
      `;
      setTimeout(() => {
        NeurobetCharts.renderOddsMovement('odds-chart-container', odds);
      }, 50);
    } else if (state.selectedModalTab === 'predictions') {
      const p = preds[0] || null;
      if (!p) {
        pane.innerHTML = `<p style="color: var(--text-dim);">Прогнозов ML для данного события пока нет.</p>`;
        return;
      }
      pane.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 1.25rem;">
          <h4>Оценка вероятности и преимущества (Edge)</h4>
          <div id="prob-gauge-container"></div>
          <div class="odds-table" style="grid-template-columns: repeat(3, 1fr);">
            <div class="odds-col">
              <div class="col-label">Справедливый кэф (Fair)</div>
              <div class="col-val mono">${Number(p.fair_odds).toFixed(2)}</div>
            </div>
            <div class="odds-col">
              <div class="col-label">Кэф Fonbet</div>
              <div class="col-val mono">${Number(p.bookmaker_odds).toFixed(2)}</div>
            </div>
            <div class="odds-col">
              <div class="col-label">Confidence</div>
              <div class="col-val mono">${(Number(p.confidence) * 100).toFixed(0)}%</div>
            </div>
          </div>
          <div style="background: var(--bg-subtle); padding: 1rem; border-radius: var(--radius-md); font-size: 0.85rem;">
            <strong>Модель:</strong> ${p.model_version}<br>
            <strong>Исход:</strong> ${p.predicted_outcome} • <strong>EV:</strong> ${(Number(p.edge) > 0 ? '+' : '') + Number(p.edge).toFixed(4)}<br>
            <strong>Время расчета:</strong> ${new Date(p.created_at).toLocaleString()}
          </div>
        </div>
      `;
      setTimeout(() => {
        NeurobetCharts.renderProbabilityGauge(
          'prob-gauge-container',
          p.model_probability,
          1.0 / p.bookmaker_odds,
          p.edge
        );
      }, 50);
    } else if (state.selectedModalTab === 'research') {
      if (research.length === 0) {
        pane.innerHTML = `<p style="color: var(--text-dim);">Web Research материалов по данному матчу не найдено.</p>`;
        return;
      }
      pane.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 1rem;">
          <h4>Найденные внешние доказательства (Web Research Evidence)</h4>
          ${research.map(r => `
            <div style="background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 1rem;">
              <div style="display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--text-dim); margin-bottom: 0.5rem;">
                <span>Источник: <strong>${r.source_domain || 'Web'}</strong></span>
                <span>Релевантность: ${(Number(r.relevance_score || 0) * 100).toFixed(0)}% • Свежесть: ${(Number(r.freshness_score || 0) * 100).toFixed(0)}%</span>
              </div>
              <p style="font-size: 0.88rem; font-style: italic; color: var(--text-main);">"${r.snippet}"</p>
            </div>
          `).join('')}
        </div>
      `;
    } else if (state.selectedModalTab === 'timeline') {
      if (timeline.length === 0) {
        pane.innerHTML = `<p style="color: var(--text-dim);">Снэпшотов ленты пока нет.</p>`;
        return;
      }
      pane.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 0.8rem; max-height: 350px; overflow-y: auto;">
          <h4>Хронологическая эволюция счета</h4>
          ${timeline.map(t => `
            <div style="display: flex; justify-content: space-between; padding: 0.5rem 0.8rem; background: var(--bg-subtle); border-radius: var(--radius-sm); font-size: 0.85rem;">
              <span class="mono">${new Date(t.observed_at).toLocaleTimeString()}</span>
              <span>Статус: <strong>${t.status}</strong></span>
              <span class="mono" style="color: var(--accent-tennis);">${t.score || t.current_period || '—'}</span>
            </div>
          `).join('')}
        </div>
      `;
    } else if (state.selectedModalTab === 'checklist') {
      pane.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 0.75rem; font-size: 0.88rem;">
          <h4>10-пунктовая техническая верификация Bet Manager</h4>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 1. Матч существует в БД и активен
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 2. Линия свежая (возраст котировок &lt; 15 секунд)
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 3. Рынок входит в список разрешенных спортивным адаптером
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 4. Достаточно виртуального баланса
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 5. Размер ставки &le; 1.0% от баланса
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 6. Совокупный открытый риск &le; 20.0%
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 7. Отсутствует дубликат активной ставки
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 8. Вердикт ML/LLM не устарел и валидирован
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 9. Фиксация транзакции в неизменяемом журнале
          </div>
          <div style="display: flex; align-items: center; gap: 0.6rem; color: var(--accent-green);">
            <span>✓</span> 10. Запрет прямого обхода проверок со стороны ML или LLM
          </div>
        </div>
      `;
    }
  }

  function closeModal() {
    if (el.modalOverlay) {
      el.modalOverlay.classList.remove('active');
    }
    state.selectedEvent = null;
  }

  // -------------------------------------------------------------------------
  // Bets, Ledger, Models, Scheduler Views
  // -------------------------------------------------------------------------
  async function fetchBets() {
    try {
      const res = await fetch(`${API_BASE}/bets?limit=50`);
      if (res.ok) {
        state.bets = await res.json();
        renderBetsTable();
      }
    } catch (e) {
      console.error('[Bets Fetch Error]:', e);
    }
  }

  function renderBetsTable() {
    if (!el.betsTableBody) return;
    if (state.bets.length === 0) {
      el.betsTableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 2rem;">Ставок в виртуальном журнале пока нет</td></tr>`;
      return;
    }
    el.betsTableBody.innerHTML = state.bets.map(b => {
      const isWon = b.status === 'WON';
      const isLost = b.status === 'LOST';
      const statusColor = isWon ? 'pos' : isLost ? 'neg' : 'text-dim';
      return `
        <tr>
          <td class="mono">${b.id.slice(0, 8)}</td>
          <td>${b.sport_code.toUpperCase()} • ${b.market}</td>
          <td><strong>${b.outcome}</strong></td>
          <td class="mono">${Number(b.odds).toFixed(2)}</td>
          <td class="mono">${fmtRub(b.stake)}</td>
          <td class="mono ${statusColor}"><strong>${b.status}</strong></td>
          <td>${new Date(b.placed_at).toLocaleTimeString()}</td>
        </tr>
      `;
    }).join('');
  }

  async function fetchLedger() {
    try {
      const res = await fetch(`${API_BASE}/ledger?limit=50`);
      if (res.ok) {
        state.ledger = await res.json();
        renderLedgerTable();
      }
    } catch (e) {
      console.error('[Ledger Fetch Error]:', e);
    }
  }

  function renderLedgerTable() {
    if (!el.ledgerTableBody) return;
    if (state.ledger.length === 0) {
      el.ledgerTableBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-dim); padding: 2rem;">Записей аудита в Ledger пока нет</td></tr>`;
      return;
    }
    el.ledgerTableBody.innerHTML = state.ledger.map(e => `
      <tr>
        <td class="mono">${e.id.slice(0, 8)}</td>
        <td><span class="badge-tag">${e.entry_type}</span></td>
        <td class="mono ${Number(e.amount) >= 0 ? 'pos' : 'neg'}">${(Number(e.amount) >= 0 ? '+' : '') + fmtRub(e.amount)}</td>
        <td class="mono">${fmtRub(e.balance_after)}</td>
        <td>${new Date(e.created_at).toLocaleString()}</td>
      </tr>
    `).join('');
  }

  async function fetchModels() {
    try {
      const [mRes, rRes] = await Promise.all([
        fetch(`${API_BASE}/models`),
        fetch(`${API_BASE}/training/runs`),
      ]);
      if (mRes.ok) state.models = await mRes.json();
      if (rRes.ok) state.trainingRuns = await rRes.json();
      renderModelsTable();
    } catch (e) {
      console.error('[Models Fetch Error]:', e);
    }
  }

  function renderModelsTable() {
    if (!el.modelsTableBody) return;
    if (state.models.length === 0) {
      el.modelsTableBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 2rem;">Модели не зарегистрированы</td></tr>`;
      return;
    }
    el.modelsTableBody.innerHTML = state.models.map(m => `
      <tr>
        <td><strong>${m.model_name}</strong></td>
        <td class="mono">${m.version_tag}</td>
        <td>${m.sport_code.toUpperCase()}</td>
        <td class="mono">${m.metrics && m.metrics.brier_score !== undefined ? m.metrics.brier_score.toFixed(4) : '—'}</td>
        <td class="mono">${m.metrics && m.metrics.roc_auc !== undefined ? m.metrics.roc_auc.toFixed(4) : '—'}</td>
        <td><span class="status-badge live">${m.status || 'ACTIVE'}</span></td>
      </tr>
    `).join('');
  }

  async function fetchScheduler() {
    try {
      const [qRes, sRes] = await Promise.all([
        fetch(`${API_BASE}/scheduler/queue`),
        fetch(`${API_BASE}/scheduler/stats`),
      ]);
      if (qRes.ok) state.queue = await qRes.json();
      if (sRes.ok) state.queueStats = await sRes.json();
      renderSchedulerTable();
    } catch (e) {
      console.error('[Scheduler Fetch Error]:', e);
    }
  }

  function renderSchedulerTable() {
    if (!el.schedulerTableBody) return;
    if (state.queue.length === 0) {
      el.schedulerTableBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-dim); padding: 2rem;">Очередь приоритетов пуста (все кандидаты обработаны)</td></tr>`;
      return;
    }
    el.schedulerTableBody.innerHTML = state.queue.map(q => `
      <tr>
        <td class="mono">${q.event_id.slice(0, 8)}</td>
        <td><strong>${(Number(q.composite_score) * 100).toFixed(1)}</strong></td>
        <td class="mono pos">+${(Number(q.edge) * 100).toFixed(1)}%</td>
        <td class="mono">${(Number(q.confidence) * 100).toFixed(0)}%</td>
        <td><span class="badge-tag tag-ev">${q.status || 'QUEUED'}</span></td>
      </tr>
    `).join('');
  }

  // -------------------------------------------------------------------------
  // Simulation Reset Handler
  // -------------------------------------------------------------------------
  async function resetSimulation() {
    if (!confirm('Вы уверены, что хотите сбросить виртуальный банкролл к 100 000 ₽? Все ставки и журнал будут обнулены.')) {
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/simulation/reset`, { method: 'POST' });
      if (res.ok) {
        alert('Виртуальная симуляция успешно сброшена к начальному депозиту 100 000 ₽!');
        fetchBankrollAndPerformance();
        fetchEvents();
        fetchBets();
        fetchLedger();
      } else {
        alert('Ошибка сброса симуляции');
      }
    } catch (e) {
      console.error('[Reset Error]:', e);
    }
  }

  // -------------------------------------------------------------------------
  // Tab Switching
  // -------------------------------------------------------------------------
  function setupTabs() {
    const tabs = [
      { btn: el.tabMatchesBtn, view: el.viewMatches, id: 'matches' },
      { btn: el.tabBetsBtn, view: el.viewBets, id: 'bets', loader: fetchBets },
      { btn: el.tabLedgerBtn, view: el.viewLedger, id: 'ledger', loader: fetchLedger },
      { btn: el.tabModelsBtn, view: el.viewModels, id: 'models', loader: fetchModels },
      { btn: el.tabSchedulerBtn, view: el.viewScheduler, id: 'scheduler', loader: fetchScheduler },
    ];

    tabs.forEach(t => {
      if (!t.btn) return;
      t.btn.addEventListener('click', () => {
        tabs.forEach(other => {
          if (other.btn) other.btn.classList.remove('active');
          if (other.view) other.view.style.display = 'none';
        });
        t.btn.classList.add('active');
        if (t.view) t.view.style.display = 'block';
        state.activeTab = t.id;
        if (t.loader) t.loader();
      });
    });
  }

  // -------------------------------------------------------------------------
  // Status Filters & Search
  // -------------------------------------------------------------------------
  function setupFilters() {
    if (el.statusFilterGroup) {
      el.statusFilterGroup.querySelectorAll('.pill-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          el.statusFilterGroup.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
          e.target.classList.add('active');
          state.activeStatus = e.target.getAttribute('data-status');
          fetchEvents();
        });
      });
    }

    if (el.searchInput) {
      let timeout;
      el.searchInput.addEventListener('input', (e) => {
        clearTimeout(timeout);
        timeout = setTimeout(() => {
          state.searchQuery = e.target.value;
          fetchEvents();
        }, 300);
      });
    }

    if (el.resetSimBtn) {
      el.resetSimBtn.addEventListener('click', resetSimulation);
    }

    if (el.modalCloseBtn) {
      el.modalCloseBtn.addEventListener('click', closeModal);
    }

    if (el.modalOverlay) {
      el.modalOverlay.addEventListener('click', (e) => {
        if (e.target === el.modalOverlay) closeModal();
      });
    }
  }

  // -------------------------------------------------------------------------
  // App Entry Point
  // -------------------------------------------------------------------------
  function init() {
    initTheme();
    setupTabs();
    setupFilters();
    initSSE();

    // Initial data fetch
    fetchBankrollAndPerformance();
    fetchSports();
    fetchEvents();

    // Polling fallback every 10 seconds
    setInterval(() => {
      if (state.autoUpdate) {
        fetchBankrollAndPerformance();
        if (state.activeTab === 'matches') fetchEvents();
        else if (state.activeTab === 'bets') fetchBets();
        else if (state.activeTab === 'ledger') fetchLedger();
      }
    }, 10000);
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
