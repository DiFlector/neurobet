/**
 * Neurobet Pure SVG Interactive Charts Engine
 * Zero dependencies, high performance, fully responsive
 */

const NeurobetCharts = {
  /**
   * Render an odds movement line chart from TimescaleDB OddsSnapshots
   * @param {string} containerId - Target element ID
   * @param {Array} history - Array of { observed_at, odds, outcome }
   */
  renderOddsMovement(containerId, history) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!history || history.length === 0) {
      container.innerHTML = `
        <div class="empty-state" style="padding: 2rem;">
          <span>Нет исторических котировок для отображения графика</span>
        </div>`;
      return;
    }

    const width = container.clientWidth || 600;
    const height = 220;
    const padding = { top: 20, right: 30, bottom: 35, left: 50 };

    const sorted = [...history].sort((a, b) => new Date(a.observed_at) - new Date(b.observed_at));
    const oddsValues = sorted.map(d => parseFloat(d.odds));
    const minOdds = Math.max(1.0, Math.floor((Math.min(...oddsValues) - 0.1) * 10) / 10);
    const maxOdds = Math.ceil((Math.max(...oddsValues) + 0.1) * 10) / 10;

    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    const getX = (index) => padding.left + (index / Math.max(1, sorted.length - 1)) * plotW;
    const getY = (val) => padding.top + plotH - ((val - minOdds) / Math.max(0.1, maxOdds - minOdds)) * plotH;

    // Build SVG path
    let pathD = `M ${getX(0)} ${getY(oddsValues[0])}`;
    for (let i = 1; i < sorted.length; i++) {
      pathD += ` L ${getX(i)} ${getY(oddsValues[i])}`;
    }

    // Area path for gradient
    const areaD = `${pathD} L ${getX(sorted.length - 1)} ${padding.top + plotH} L ${getX(0)} ${padding.top + plotH} Z`;

    // Horizontal grid lines
    const gridSteps = 4;
    let gridLinesSvg = '';
    for (let i = 0; i <= gridSteps; i++) {
      const val = minOdds + (i / gridSteps) * (maxOdds - minOdds);
      const y = getY(val);
      gridLinesSvg += `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="4" />
        <text x="${padding.left - 8}" y="${y + 4}" fill="#64748b" font-size="10" font-family="JetBrains Mono" text-anchor="end">${val.toFixed(2)}</text>
      `;
    }

    // Points
    let pointsSvg = '';
    sorted.forEach((d, i) => {
      const cx = getX(i);
      const cy = getY(parseFloat(d.odds));
      const timeStr = new Date(d.observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      pointsSvg += `
        <circle cx="${cx}" cy="${cy}" r="4" fill="#ccff00" stroke="#0e1424" stroke-width="2">
          <title>${d.outcome || 'Odds'}: ${parseFloat(d.odds).toFixed(2)} (${timeStr})</title>
        </circle>
      `;
    });

    const firstTime = new Date(sorted[0].observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const lastTime = new Date(sorted[sorted.length - 1].observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    container.innerHTML = `
      <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow: visible;">
        <defs>
          <linearGradient id="oddsGrad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#ccff00" stop-opacity="0.35" />
            <stop offset="100%" stop-color="#ccff00" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        ${gridLinesSvg}
        <path d="${areaD}" fill="url(#oddsGrad)" />
        <path d="${pathD}" fill="none" stroke="#ccff00" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
        ${pointsSvg}
        <text x="${padding.left}" y="${height - 10}" fill="#64748b" font-size="10" font-family="JetBrains Mono">${firstTime}</text>
        <text x="${width - padding.right}" y="${height - 10}" fill="#64748b" font-size="10" font-family="JetBrains Mono" text-anchor="end">${lastTime}</text>
      </svg>
    `;
  },

  /**
   * Render probability comparison bar (Model Prob vs Implied Bookmaker Prob)
   */
  renderProbabilityGauge(containerId, modelProb, impliedProb, edge) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const mPct = (modelProb * 100).toFixed(1);
    const iPct = (impliedProb * 100).toFixed(1);
    const edgePct = (edge * 100).toFixed(1);
    const isPositive = edge > 0;

    container.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 0.8rem; width: 100%;">
        <div style="display: flex; justify-content: space-between; font-size: 0.82rem;">
          <span style="color: var(--text-dim);">Вероятность ML: <strong style="color: var(--accent-tennis);">${mPct}%</strong></span>
          <span style="color: var(--text-dim);">Букмекер (Implied): <strong style="color: #94a3b8;">${iPct}%</strong></span>
          <span style="color: var(--text-dim);">Edge: <strong class="${isPositive ? 'pos' : 'neg'}">${isPositive ? '+' : ''}${edgePct}%</strong></span>
        </div>
        <div style="height: 12px; background: rgba(255,255,255,0.08); border-radius: 999px; overflow: hidden; position: relative;">
          <div style="width: ${Math.min(100, mPct)}%; height: 100%; background: #10b981; border-radius: 999px; position: absolute; left: 0; z-index: 2;"></div>
          <div style="width: ${Math.min(100, iPct)}%; height: 100%; background: #64748b; border-radius: 999px; position: absolute; left: 0; z-index: 1;"></div>
        </div>
      </div>
    `;
  }
};
