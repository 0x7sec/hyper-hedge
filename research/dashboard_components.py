"""
Research Dashboard UI Components
Provides CSS styles, HTML markup, and JavaScript rendering logic for the
Research & Backtesting Suite in the Bybit Telemetry Server.
Supports Jesse-style Price & Trade execution chart with entry/exit markers,
4-column comprehensive performance breakdown, and interactive trade inspection.
"""

def get_research_css() -> str:
    return """
    /* Research Suite & Tab Navigation */
    .view-nav-tabs {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      border-bottom: 1px solid #1e293b;
      padding-bottom: 14px;
    }
    .view-tab-btn {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 22px;
      background: #0f172a;
      color: #94a3b8;
      border: 1px solid #1e293b;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .view-tab-btn:hover {
      background: #1e293b;
      color: #f8fafc;
      border-color: #3b82f6;
    }
    .view-tab-btn.active {
      background: #1e293b;
      color: #38bdf8;
      border-color: #38bdf8;
      box-shadow: 0 0 16px rgba(56, 189, 248, 0.15);
    }
    .tab-indicator {
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }
    .live-ind {
      background: #22c55e;
      box-shadow: 0 0 8px #22c55e;
    }
    .research-ind {
      background: #a855f7;
      box-shadow: 0 0 8px #a855f7;
    }
    .tab-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 4px;
      letter-spacing: 0.5px;
    }
    .live-badge {
      background: rgba(34, 197, 94, 0.15);
      color: #4ade80;
    }
    .research-badge {
      background: rgba(168, 85, 247, 0.15);
      color: #c084fc;
    }
    .research-banner {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%);
      border: 1px solid #1e293b;
      border-left: 4px solid #a855f7;
      padding: 16px 20px;
      border-radius: 8px;
      margin-bottom: 20px;
    }
    .research-banner .banner-title {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .research-banner h3 {
      font-size: 15px;
      font-weight: 700;
      color: #f8fafc;
      margin-bottom: 4px;
    }
    .research-banner p {
      font-size: 12px;
      color: #94a3b8;
    }
    .banner-tag {
      padding: 4px 10px;
      border-radius: 4px;
      background: rgba(168, 85, 247, 0.2);
      color: #d8b4fe;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      flex-shrink: 0;
    }
    .research-controls-card {
      margin-bottom: 24px;
      padding: 18px 20px;
      background: #0f172a;
      border: 1px solid #1e293b;
    }
    .controls-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    .ctrl-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .ctrl-group label {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      color: #94a3b8;
      letter-spacing: 0.5px;
    }
    .ctrl-group input, .ctrl-group select {
      background: #080c14;
      border: 1px solid #1e293b;
      color: #e2e8f0;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 13px;
      font-family: inherit;
    }
    .ctrl-group input:focus, .ctrl-group select:focus {
      border-color: #38bdf8;
      outline: none;
    }
    .params-subgrid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 12px;
      padding-top: 14px;
      border-top: 1px solid #1e293b;
      margin-bottom: 16px;
    }
    .research-btn-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      padding-top: 14px;
      border-top: 1px solid #1e293b;
    }
    .btn-research-primary {
      background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
      color: #fff;
      border: 1px solid #38bdf8;
      padding: 9px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-research-primary:hover {
      background: #0284c7;
      box-shadow: 0 0 12px rgba(56, 189, 248, 0.3);
    }
    .btn-research-purple {
      background: linear-gradient(135deg, #7e22ce 0%, #6b21a8 100%);
      color: #fff;
      border: 1px solid #c084fc;
      padding: 9px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-research-purple:hover {
      background: #7e22ce;
      box-shadow: 0 0 12px rgba(192, 132, 252, 0.3);
    }
    .btn-research-amber {
      background: linear-gradient(135deg, #b45309 0%, #92400e 100%);
      color: #fff;
      border: 1px solid #fbbf24;
      padding: 9px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-research-amber:hover {
      background: #b45309;
      box-shadow: 0 0 12px rgba(251, 191, 36, 0.3);
    }
    .rs-status-idle {
      margin-left: auto;
      font-size: 12px;
      color: #64748b;
      font-family: 'JetBrains Mono', monospace;
    }
    .rs-status-running {
      margin-left: auto;
      font-size: 12px;
      color: #38bdf8;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .rs-status-done {
      margin-left: auto;
      font-size: 12px;
      color: #4ade80;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
    }
    .rs-status-error {
      margin-left: auto;
      font-size: 12px;
      color: #ef4444;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
    }

    /* Jesse-Style Price & Trades Master Chart */
    .price-trades-card {
      margin-bottom: 24px;
      padding: 18px 20px;
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
    }
    .chart-toolbar {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid #1e293b;
    }
    .toolbar-group {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .tool-btn {
      background: #080c14;
      border: 1px solid #1e293b;
      color: #94a3b8;
      padding: 5px 12px;
      border-radius: 5px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .tool-btn:hover {
      background: #1e293b;
      color: #f8fafc;
      border-color: #3b82f6;
    }
    .tool-btn.active {
      background: #1e293b;
      color: #38bdf8;
      border-color: #38bdf8;
    }
    .master-canvas-wrap {
      width: 100%;
      height: 380px;
      position: relative;
      background: #080c14;
      border-radius: 6px;
      overflow: hidden;
    }
    .master-canvas-wrap canvas {
      width: 100%;
      height: 100%;
      display: block;
      cursor: crosshair;
    }
    .chart-tooltip {
      position: absolute;
      display: none;
      pointer-events: none;
      background: rgba(15, 23, 42, 0.95);
      border: 1px solid #38bdf8;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
      padding: 10px 14px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: #e2e8f0;
      z-index: 50;
      min-width: 200px;
    }

    /* Jesse Comprehensive Performance Report Grid */
    .jesse-report-card {
      margin-bottom: 24px;
      padding: 18px 20px;
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
    }
    .jesse-report-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
    }
    @media (max-width: 1024px) {
      .jesse-report-grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }
    @media (max-width: 640px) {
      .jesse-report-grid {
        grid-template-columns: 1fr;
      }
    }
    .jesse-report-col {
      background: #080c14;
      border: 1px solid #1e293b;
      border-radius: 6px;
      padding: 14px 16px;
    }
    .col-title {
      font-size: 12px;
      font-weight: 700;
      color: #38bdf8;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 12px;
      padding-bottom: 6px;
      border-bottom: 1px solid #1e293b;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .metric-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 5px 0;
      font-size: 12px;
      border-bottom: 1px dotted rgba(30, 41, 59, 0.6);
    }
    .metric-row:last-child {
      border-bottom: none;
    }
    .metric-name {
      color: #94a3b8;
    }
    .metric-val {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      color: #f8fafc;
    }

    .charts-2x2-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }
    @media (max-width: 900px) {
      .charts-2x2-grid {
        grid-template-columns: 1fr;
      }
    }
    .chart-card {
      padding: 16px 18px;
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
    }
    .chart-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 12px;
    }
    .chart-title {
      font-size: 13px;
      font-weight: 700;
      color: #f8fafc;
      display: block;
    }
    .chart-sub {
      font-size: 11px;
      color: #64748b;
      display: block;
      margin-top: 2px;
    }
    .chart-legend {
      font-size: 11px;
      display: flex;
      gap: 10px;
      font-family: 'JetBrains Mono', monospace;
    }
    .canvas-wrap {
      width: 100%;
      height: 250px;
      position: relative;
    }
    .canvas-wrap canvas {
      width: 100%;
      height: 100%;
      display: block;
      background: #080c14;
      border-radius: 4px;
    }
    .badge-branch1 {
      background: rgba(34, 197, 94, 0.15);
      color: #4ade80;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-scenario5 {
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-exhaustion {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-timeout {
      background: rgba(100, 116, 139, 0.15);
      color: #94a3b8;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .btn-focus-trade {
      background: #080c14;
      border: 1px solid #38bdf8;
      color: #38bdf8;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }
    .btn-focus-trade:hover {
      background: #38bdf8;
      color: #080c14;
    }
    .btn-research-history {
      background: rgba(148, 163, 184, 0.12);
      border: 1px solid rgba(148, 163, 184, 0.3);
      color: #cbd5e1;
    }
    .btn-research-history:hover {
      background: rgba(148, 163, 184, 0.25);
      color: #f8fafc;
      border-color: #94a3b8;
    }
    /* Shareable Test Permlink Bar */
    .test-permlink-bar {
      background: linear-gradient(135deg, rgba(15, 23, 42, 0.95), rgba(8, 12, 20, 0.98));
      border: 1px solid rgba(56, 189, 248, 0.4);
      border-left: 4px solid #38bdf8;
      border-radius: 8px;
      padding: 12px 18px;
      margin-bottom: 20px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
      animation: fadeIn 0.25s ease-out;
    }
    .permlink-meta {
      display: flex;
      align-items: center;
      gap: 12px;
      flex: 1;
      min-width: 280px;
    }
    .permlink-icon {
      font-size: 22px;
    }
    .permlink-title-wrap {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 3px;
    }
    .permlink-badge {
      background: rgba(56, 189, 248, 0.2);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.4);
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }
    .permlink-title {
      font-size: 13px;
      font-weight: 700;
      color: #f8fafc;
    }
    .permlink-ts {
      font-size: 11px;
      color: #64748b;
    }
    .permlink-summary-line {
      font-size: 11px;
      color: #94a3b8;
      font-family: 'JetBrains Mono', monospace;
    }
    .permlink-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 320px;
      flex: 1;
      justify-content: flex-end;
    }
    .permlink-input {
      background: #020617;
      border: 1px solid #1e293b;
      border-radius: 6px;
      color: #38bdf8;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 6px 10px;
      width: 100%;
      max-width: 280px;
      outline: none;
    }
    .btn-permlink-copy {
      background: #38bdf8;
      color: #020617;
      border: none;
      font-weight: 700;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 11px;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .btn-permlink-copy:hover {
      background: #7dd3fc;
      transform: translateY(-1px);
    }
    .btn-permlink-share {
      background: rgba(255, 255, 255, 0.06);
      color: #f8fafc;
      border: 1px solid #334155;
      padding: 6px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 11px;
      font-weight: 600;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .btn-permlink-share:hover {
      background: rgba(255, 255, 255, 0.12);
    }
    /* Modal: Saved Tests */
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(2, 6, 23, 0.85);
      backdrop-filter: blur(4px);
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      animation: fadeIn 0.2s ease-out;
    }
    .modal-card {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 12px;
      width: 100%;
      max-width: 800px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.6);
      overflow: hidden;
    }
    .modal-header {
      padding: 16px 20px;
      border-bottom: 1px solid #1e293b;
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #090e1a;
    }
    .btn-close-modal {
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 22px;
      cursor: pointer;
      line-height: 1;
      padding: 0 4px;
    }
    .btn-close-modal:hover {
      color: #f8fafc;
    }
    .modal-body {
      padding: 16px 20px;
      overflow-y: auto;
      flex: 1;
    }
    .saved-test-item {
      background: #080c14;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      transition: border-color 0.15s;
    }
    .saved-test-item:hover {
      border-color: #38bdf8;
    }
    .saved-test-info {
      flex: 1;
    }
    .saved-test-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }
    .saved-test-title {
      font-size: 13px;
      font-weight: 700;
      color: #f8fafc;
    }
    .saved-test-meta {
      font-size: 11px;
      color: #64748b;
    }
    .saved-test-metrics {
      font-size: 11px;
      color: #94a3b8;
      font-family: 'JetBrains Mono', monospace;
    }
    .saved-test-btns {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn-load-test {
      background: #38bdf8;
      color: #020617;
      border: none;
      padding: 5px 12px;
      border-radius: 5px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
    }
    .btn-load-test:hover {
      background: #7dd3fc;
    }
    .btn-delete-test {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #f87171;
      padding: 5px 8px;
      border-radius: 5px;
      font-size: 11px;
      cursor: pointer;
    }
    .btn-delete-test:hover {
      background: rgba(239, 68, 68, 0.3);
      color: #fca5a5;
    }
    /* Toast Alert */
    .research-toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #020617;
      color: #38bdf8;
      border: 1px solid #38bdf8;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      box-shadow: 0 4px 20px rgba(0,0,0,0.5);
      z-index: 10000;
      display: none;
      animation: fadeIn 0.2s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    """


def get_research_html() -> str:
    return """
    <div id="view-research" style="display:none;">
      <!-- Strategy & Methodology Banner -->
      <div class="research-banner">
        <div class="banner-title">
          <span style="font-size:24px;">🔬</span>
          <div>
            <h3>Path B Tri-Modal Execution Research &amp; Quantitative Suite</h3>
            <p>1-Minute Sub-Candle Replay (Zero Intra-Bar Blind Spot) &bull; Flash-Crash Exhaustion Guard Active &bull; 5,000-Path Monte Carlo Bootstrap &bull; Walk-Forward In/Out-of-Sample Optimizer</p>
          </div>
        </div>
        <div class="banner-tag">INSTITUTIONAL GRADE</div>
      </div>

      <!-- Research Control Panel -->
      <div class="card research-controls-card">
        <div class="controls-grid">
          <div class="ctrl-group">
            <label>Trading Pair</label>
            <select id="rs-symbol" onchange="onResearchSymbolChange()">
              <option value="BTCUSDT" selected>BTCUSDT (Bitcoin)</option>
              <option value="ETHUSDT">ETHUSDT (Ethereum)</option>
              <option value="SOLUSDT">SOLUSDT (Solana)</option>
              <option value="PAXGUSDT">PAXGUSDT (Gold)</option>
            </select>
          </div>
          <div class="ctrl-group">
            <label>Historical Horizon</label>
            <select id="rs-bars">
              <option value="1000">1,000 Bars (~41 days)</option>
              <option value="2000" selected>2,000 Bars (~83 days)</option>
              <option value="4000">4,000 Bars (~166 days)</option>
              <option value="8000">8,000 Bars (~333 days)</option>
            </select>
          </div>
          <div class="ctrl-group">
            <label>Initial Capital ($)</label>
            <input type="number" id="rs-capital" value="1000" min="100" step="100" />
          </div>
          <div class="ctrl-group">
            <label>Leverage</label>
            <input type="number" id="rs-leverage" value="10" min="1" max="25" />
          </div>
        </div>

        <div class="params-subgrid">
          <div class="ctrl-group">
            <label>Base D Distance (%)</label>
            <input type="number" id="rs-d-pct" value="1.0" step="0.1" />
          </div>
          <div class="ctrl-group">
            <label>Branch 1 Confirm Mult</label>
            <input type="number" id="rs-confirm-mult" value="0.80" step="0.05" />
          </div>
          <div class="ctrl-group">
            <label>Apex TP Mult</label>
            <input type="number" id="rs-b1-tp-mult" value="2.80" step="0.1" />
          </div>
          <div class="ctrl-group">
            <label>Branch 2 Full TP Mult</label>
            <input type="number" id="rs-b2-tp-mult" value="3.50" step="0.1" />
          </div>
        </div>

        <div class="research-btn-bar">
          <button id="btn-run-backtest" class="btn btn-research-primary" onclick="runResearchBacktest()">
            ⚡ Run 1-Min Replay Backtest
          </button>
          <button id="btn-run-mc" class="btn btn-research-purple" onclick="runResearchMonteCarlo()">
            🎲 Run Monte Carlo &amp; RST (5,000 Runs)
          </button>
          <button id="btn-run-opt" class="btn btn-research-amber" onclick="runResearchOptimize()">
            🧬 Run Jesse Walk-Forward Optimizer
          </button>
          <button id="btn-saved-tests" class="btn btn-research-history" onclick="openSavedTestsModal()">
            📚 Saved Tests (<span id="saved-tests-count">0</span>)
          </button>
          <div id="rs-status-badge" class="rs-status-idle">Engine Ready</div>
        </div>
      </div>

      <!-- Test Permlink & Share Bar (Active Test Link) -->
      <div id="test-permlink-bar" class="test-permlink-bar" style="display:none;">
        <div class="permlink-meta">
          <span class="permlink-icon">🔗</span>
          <div>
            <div class="permlink-title-wrap">
              <span id="permlink-badge" class="permlink-badge">TEST RUN</span>
              <span id="permlink-title" class="permlink-title">BTCUSDT 1-Min Replay Backtest</span>
              <span id="permlink-ts" class="permlink-ts"></span>
            </div>
            <div class="permlink-summary-line" id="permlink-summary-line">
              Net Profit: +$0.00 | Win Rate: 0.0% | PF: 0.00 | Max DD: 0.0%
            </div>
          </div>
        </div>
        <div class="permlink-actions">
          <input type="text" id="permlink-input" class="permlink-input" readonly value="" onclick="this.select()" />
          <button class="btn btn-permlink-copy" onclick="copyActivePermlink()">📋 Copy Link</button>
          <button class="btn btn-permlink-share" onclick="openActivePermlinkInNewTab()">↗ Open Tab</button>
        </div>
      </div>


      <!-- Quick KPI Scorecards Grid -->
      <div class="stats-grid" id="research-kpis" style="margin-bottom:24px;">
        <div class="stat-card">
          <div class="title">Net Profit (1-Min Replay)</div>
          <div class="value" id="kpi-net-profit" style="color:#38bdf8;">$0.00</div>
          <div class="sub" id="kpi-return-pct">+0.0% &bull; PF: 0.00</div>
        </div>
        <div class="stat-card">
          <div class="title">Win Rate &amp; Shield Rate</div>
          <div class="value" id="kpi-win-rate">0.0%</div>
          <div class="sub" id="kpi-shield-rate">Shield: 0.0% &bull; 0 Cycles</div>
        </div>
        <div class="stat-card">
          <div class="title">Max Drawdown</div>
          <div class="value" id="kpi-max-dd" style="color:#ef4444;">$0.00</div>
          <div class="sub" id="kpi-max-dd-pct">0.00% intra-candle</div>
        </div>
        <div class="stat-card">
          <div class="title">Sharpe Ratio &amp; Fees</div>
          <div class="value" id="kpi-sharpe">0.00</div>
          <div class="sub" id="kpi-fees">Taker Fees: $0.00</div>
        </div>
        <div class="stat-card">
          <div class="title">Monte Carlo Median (5k Runs)</div>
          <div class="value" id="kpi-mc-median" style="color:#c084fc;">$0.00</div>
          <div class="sub" id="kpi-mc-range">5th: $0.00 | 95th: $0.00</div>
        </div>
        <div class="stat-card">
          <div class="title">RST Hypothesis Test (p-val)</div>
          <div class="value" id="kpi-rst-p">p = 0.000</div>
          <div class="sub" id="kpi-rst-sig">Permutations: 1,000</div>
        </div>
      </div>

      <!-- Jesse Master Price & Trade Execution Chart -->
      <div class="price-trades-card">
        <div class="chart-toolbar">
          <div class="toolbar-group">
            <span style="font-size:13px; font-weight:700; color:#f8fafc; display:flex; align-items:center; gap:6px;">
              <span>📈</span> Price Action &amp; Trade Execution Chart (Jesse Mode)
            </span>
            <span style="font-size:11px; color:#64748b;" id="chart-range-label">Showing bars</span>
          </div>

          <!-- Controls: Zoom & Overlays -->
          <div class="toolbar-group">
            <button class="tool-btn" onclick="setChartWindow(100)">100 Bars</button>
            <button class="tool-btn active" onclick="setChartWindow(250)">250 Bars</button>
            <button class="tool-btn" onclick="setChartWindow(500)">500 Bars</button>
            <button class="tool-btn" onclick="setChartWindow('all')">Fit All</button>
            <span style="color:#1e293b;">|</span>
            <button class="tool-btn" onclick="panChart(-50)" title="Pan Left">◀</button>
            <button class="tool-btn" onclick="panChart(50)" title="Pan Right">▶</button>
            <span style="color:#1e293b;">|</span>
            <button id="btn-toggle-ema" class="tool-btn active" onclick="toggleOverlay('ema')">EMA(9/21)</button>
            <button id="btn-toggle-rays" class="tool-btn active" onclick="toggleOverlay('rays')">Trade Rays</button>
          </div>
        </div>

        <div class="master-canvas-wrap" id="master-wrap">
          <canvas id="chart-price-trades"></canvas>
          <div id="chart-tooltip" class="chart-tooltip"></div>
        </div>

        <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; margin-top:10px; font-size:11px; font-family:'JetBrains Mono', monospace; color:#94a3b8;">
          <div style="display:flex; gap:14px; align-items:center;">
            <span><span style="color:#22c55e;">▲</span> Long Entry</span>
            <span><span style="color:#ef4444;">▼</span> Short Entry</span>
            <span><span style="color:#22c55e;">●</span> Win Exit</span>
            <span><span style="color:#ef4444;">✖</span> Loss Exit</span>
            <span><span style="color:#fbbf24;">⚡</span> Exhaustion Guard TP</span>
          </div>
          <div style="display:flex; gap:14px; align-items:center;">
            <span style="color:#38bdf8;">— EMA(9)</span>
            <span style="color:#a855f7;">— EMA(21)</span>
            <span id="chart-hover-hud" style="color:#f8fafc; font-weight:600;">Hover over chart for details</span>
          </div>
        </div>
      </div>

      <!-- Jesse-Style Comprehensive Performance Breakdown -->
      <div class="jesse-report-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
          <h4 style="font-size:14px; font-weight:700; color:#f8fafc; display:flex; align-items:center; gap:8px;">
            <span>📊</span> Quantitative Strategy Performance Report (Jesse Standard)
          </h4>
          <span style="font-size:11px; color:#64748b;">Unified 1-Minute Sub-Candle Replay Audit</span>
        </div>

        <div class="jesse-report-grid">
          <!-- Column 1: Financial & Capital Efficiency -->
          <div class="jesse-report-col">
            <div class="col-title"><span>💰</span> Capital &amp; Returns</div>
            <div class="metric-row"><span class="metric-name">Total Net Profit</span><span class="metric-val" id="jr-net-profit">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Net Return</span><span class="metric-val" id="jr-return-pct">0.00%</span></div>
            <div class="metric-row"><span class="metric-name">CAGR (Annualized)</span><span class="metric-val" id="jr-cagr">0.00%</span></div>
            <div class="metric-row"><span class="metric-name">Gross Profit</span><span class="metric-val" style="color:#4ade80;" id="jr-gross-profit">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Gross Loss</span><span class="metric-val" style="color:#f87171;" id="jr-gross-loss">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Profit Factor</span><span class="metric-val" id="jr-profit-factor">0.00</span></div>
            <div class="metric-row"><span class="metric-name">Trade Expectancy</span><span class="metric-val" id="jr-expectancy">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Total Taker Fees</span><span class="metric-val" style="color:#ef4444;" id="jr-fees">$0.00</span></div>
          </div>

          <!-- Column 2: Win / Loss Dynamics -->
          <div class="jesse-report-col">
            <div class="col-title"><span>🎯</span> Win / Loss Dynamics</div>
            <div class="metric-row"><span class="metric-name">Win Rate</span><span class="metric-val" id="jr-win-rate">0.0%</span></div>
            <div class="metric-row"><span class="metric-name">Shield / BE Rate</span><span class="metric-val" id="jr-shield-rate">0.0%</span></div>
            <div class="metric-row"><span class="metric-name">Total Closed Trades</span><span class="metric-val" id="jr-total-trades">0</span></div>
            <div class="metric-row"><span class="metric-name">Winning Trades</span><span class="metric-val" style="color:#4ade80;" id="jr-win-trades">0</span></div>
            <div class="metric-row"><span class="metric-name">Losing Trades</span><span class="metric-val" style="color:#f87171;" id="jr-loss-trades">0</span></div>
            <div class="metric-row"><span class="metric-name">Win / Loss Ratio</span><span class="metric-val" id="jr-wl-ratio">0.00</span></div>
            <div class="metric-row"><span class="metric-name">Average Win</span><span class="metric-val" style="color:#4ade80;" id="jr-avg-win">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Average Loss</span><span class="metric-val" style="color:#f87171;" id="jr-avg-loss">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Largest Win</span><span class="metric-val" style="color:#4ade80;" id="jr-large-win">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Largest Loss</span><span class="metric-val" style="color:#f87171;" id="jr-large-loss">$0.00</span></div>
          </div>

          <!-- Column 3: Directional Analysis -->
          <div class="jesse-report-col">
            <div class="col-title"><span>⚖️</span> Directional Breakdown</div>
            <div class="metric-row"><span class="metric-name">Long Trades</span><span class="metric-val" id="jr-long-trades">0</span></div>
            <div class="metric-row"><span class="metric-name">Long Win Rate</span><span class="metric-val" id="jr-long-wr">0.0%</span></div>
            <div class="metric-row"><span class="metric-name">Long Net P&amp;L</span><span class="metric-val" id="jr-long-pnl">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Short Trades</span><span class="metric-val" id="jr-short-trades">0</span></div>
            <div class="metric-row"><span class="metric-name">Short Win Rate</span><span class="metric-val" id="jr-short-wr">0.0%</span></div>
            <div class="metric-row"><span class="metric-name">Short Net P&amp;L</span><span class="metric-val" id="jr-short-pnl">$0.00</span></div>
            <div class="metric-row"><span class="metric-name">Exhaustion Guard Hits</span><span class="metric-val" style="color:#fbbf24;" id="jr-eg-hits">0</span></div>
            <div class="metric-row"><span class="metric-name">Timeouts (Branch 3)</span><span class="metric-val" id="jr-timeouts">0</span></div>
          </div>

          <!-- Column 4: Risk, Ratios & Streaks -->
          <div class="jesse-report-col">
            <div class="col-title"><span>🛡️</span> Risk, Ratios &amp; Streaks</div>
            <div class="metric-row"><span class="metric-name">Max Drawdown</span><span class="metric-val" style="color:#ef4444;" id="jr-max-dd">$0.00 (0.0%)</span></div>
            <div class="metric-row"><span class="metric-name">Sharpe Ratio</span><span class="metric-val" id="jr-sharpe">0.00</span></div>
            <div class="metric-row"><span class="metric-name">Sortino Ratio</span><span class="metric-val" id="jr-sortino">0.00</span></div>
            <div class="metric-row"><span class="metric-name">Calmar Ratio</span><span class="metric-val" id="jr-calmar">0.00</span></div>
            <div class="metric-row"><span class="metric-name">Max Consecutive Wins</span><span class="metric-val" style="color:#4ade80;" id="jr-consec-wins">0</span></div>
            <div class="metric-row"><span class="metric-name">Max Consecutive Losses</span><span class="metric-val" style="color:#f87171;" id="jr-consec-loss">0</span></div>
            <div class="metric-row"><span class="metric-name">Avg Holding Time</span><span class="metric-val" id="jr-avg-hold">0.0 bars</span></div>
            <div class="metric-row"><span class="metric-name">Max / Min Holding</span><span class="metric-val" id="jr-max-hold">0 / 0 bars</span></div>
          </div>
        </div>
      </div>

      <!-- Jesse Walk-Forward Optimizer Results Card -->
      <div id="optimizer-results-card" class="card" style="display:none; margin-bottom:24px; border:1px solid rgba(245, 158, 11, 0.4); padding:18px 20px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom:1px solid #1e293b; padding-bottom:10px;">
          <div>
            <h4 style="color:#fbbf24; font-size:15px; display:flex; align-items:center; gap:8px;">
              <span>🧬</span> Jesse Walk-Forward Hyperparameter Optimization Results (70% In-Sample / 30% Out-of-Sample)
            </h4>
            <div id="opt-summary-sub" style="font-size:12px; color:#94a3b8; margin-top:4px;">Evaluated 25 parameter vectors against unseen test horizon</div>
          </div>
          <div id="opt-overfit-badge" style="padding:4px 10px; border-radius:6px; font-weight:700; font-size:12px; background:rgba(34,197,94,0.15); color:#4ade80;">OVERFITTING GUARD: PASSED</div>
        </div>
        
        <div class="opt-split-grid" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:14px; margin-bottom:16px;">
          <div style="background:#080c14; padding:12px; border-radius:6px; border:1px solid #1e293b;">
            <div style="font-size:11px; color:#94a3b8; text-transform:uppercase;">Optimal Parameters Found</div>
            <div id="opt-best-params" style="font-family:'JetBrains Mono', monospace; font-size:13px; color:#38bdf8; font-weight:600; margin-top:4px;">d_pct: 1.0, b1_tp: 2.8, b2_tp: 3.5</div>
          </div>
          <div style="background:#080c14; padding:12px; border-radius:6px; border:1px solid #1e293b;">
            <div style="font-size:11px; color:#94a3b8; text-transform:uppercase;">In-Sample Training (70%)</div>
            <div id="opt-train-metrics" style="font-size:13px; font-weight:600; margin-top:4px;">Sharpe: 0.00 &bull; Profit: $0.00 &bull; WR: 0%</div>
          </div>
          <div style="background:#080c14; padding:12px; border-radius:6px; border:1px solid #1e293b;">
            <div style="font-size:11px; color:#94a3b8; text-transform:uppercase;">Out-of-Sample Unseen (30%)</div>
            <div id="opt-test-metrics" style="font-size:13px; font-weight:600; margin-top:4px;">Sharpe: 0.00 &bull; Profit: $0.00 &bull; WR: 0%</div>
          </div>
          <div style="background:#080c14; padding:12px; border-radius:6px; border:1px solid #1e293b;">
            <div style="font-size:11px; color:#94a3b8; text-transform:uppercase;">Jesse Volume-Weighted Fitness</div>
            <div id="opt-fitness-score" style="font-size:14px; font-weight:700; color:#fbbf24; margin-top:4px;">0.000</div>
          </div>
        </div>

        <div style="font-size:12px; font-weight:700; color:#94a3b8; margin-bottom:8px; text-transform:uppercase;">Top Candidate Trials Leaderboard</div>
        <div class="table-container" style="max-height:220px; overflow-y:auto;">
          <table style="width:100%; border-collapse:collapse; font-size:12px;">
            <thead>
              <tr style="border-bottom:1px solid #1e293b; color:#64748b; text-align:left;">
                <th style="padding:6px 10px;">Trial</th>
                <th style="padding:6px 10px;">Parameters (D, B1, B2)</th>
                <th style="padding:6px 10px;">Train Profit</th>
                <th style="padding:6px 10px;">Train Sharpe</th>
                <th style="padding:6px 10px;">Test Profit</th>
                <th style="padding:6px 10px;">Test Sharpe</th>
                <th style="padding:6px 10px;">Test Max DD</th>
                <th style="padding:6px 10px;">Fitness</th>
                <th style="padding:6px 10px;">Status</th>
              </tr>
            </thead>
            <tbody id="opt-leaderboard-body">
              <tr><td colspan="9" style="text-align:center; padding:12px; color:#64748b;">Run optimizer to populate leaderboard</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 4 Canvas Charts Grid -->
      <div class="charts-2x2-grid">
        <!-- Chart 1: Equity Curve vs Buy & Hold -->
        <div class="card chart-card">
          <div class="chart-header">
            <div>
              <span class="chart-title">Cumulative Equity vs. Buy &amp; Hold Benchmark</span>
              <span class="chart-sub">Compounding account equity with entry/exit trade markers</span>
            </div>
            <div class="chart-legend">
              <span style="color:#38bdf8;">&mdash; Strategy Equity</span>
              <span style="color:#64748b;">&mdash; Buy &amp; Hold</span>
            </div>
          </div>
          <div class="canvas-wrap">
            <canvas id="chart-equity"></canvas>
          </div>
        </div>

        <!-- Chart 2: Underwater Drawdown Profile -->
        <div class="card chart-card">
          <div class="chart-header">
            <div>
              <span class="chart-title">Underwater Drawdown Profile (%)</span>
              <span class="chart-sub">High-water mark intra-candle dips and recovery duration</span>
            </div>
            <div class="chart-legend">
              <span style="color:#ef4444;">&mdash; Drawdown Depth</span>
            </div>
          </div>
          <div class="canvas-wrap">
            <canvas id="chart-drawdown"></canvas>
          </div>
        </div>

        <!-- Chart 3: Monte Carlo Fan Chart -->
        <div class="card chart-card">
          <div class="chart-header">
            <div>
              <span class="chart-title">Monte Carlo 5,000-Path Resampling Fan Chart</span>
              <span class="chart-sub">5th, 25th, Median (50th), 75th, and 95th percentile confidence cones</span>
            </div>
            <div class="chart-legend">
              <span style="color:#c084fc;">&mdash; Median</span>
              <span style="color:#a855f7; opacity:0.6;">&#9632; 90% Conf</span>
            </div>
          </div>
          <div class="canvas-wrap">
            <canvas id="chart-monte-carlo"></canvas>
          </div>
        </div>

        <!-- Chart 4: Scenario Distribution Breakdown -->
        <div class="card chart-card">
          <div class="chart-header">
            <div>
              <span class="chart-title">Path B Execution Scenario Distribution</span>
              <span class="chart-sub">Branch 1 Apex TP vs Scenario 5 Size-Flip vs Exhaustion Guard vs Timeout</span>
            </div>
          </div>
          <div class="canvas-wrap">
            <canvas id="chart-scenarios"></canvas>
          </div>
        </div>
      </div>

      <!-- Detailed Simulated Trades Table with Focus on Chart Action -->
      <div class="section-title" style="margin-top:24px;">
        <span>Simulated 1-Minute Replay Trade Cycles</span>
        <div style="display:flex; gap:10px; align-items:center;">
          <label style="font-size:12px; color:#94a3b8;">Filter:</label>
          <select id="rs-trade-filter" onchange="filterResearchTrades(this.value)" style="background:#0f172a; border:1px solid #1e293b; color:#e2e8f0; font-size:12px; padding:3px 8px; border-radius:4px;">
            <option value="ALL">All Cycles</option>
            <option value="BRANCH_1_TP">Branch 1 TP (Primary Runner)</option>
            <option value="SCENARIO_5_FLIP">Scenario 5 Size-Flip (+70% Runner)</option>
            <option value="EXHAUSTION_GUARD">Exhaustion Guard (Flash Crash/Pump TP)</option>
            <option value="BRANCH_3_TIMEOUT">Branch 3 Timeout (50-Bar Recycle)</option>
          </select>
        </div>
      </div>
      <div class="table-container">
        <div class="table-scroll">
          <table id="rs-trades-table">
            <thead>
              <tr>
                <th>Cycle #</th>
                <th>Direction</th>
                <th>Scenario</th>
                <th>Entry Price</th>
                <th>Primary Exit</th>
                <th>Hedge Exit</th>
                <th>Gross PnL</th>
                <th>Fees</th>
                <th>Net PnL</th>
                <th>Return %</th>
                <th>Bars Held</th>
                <th>Exhaustion Guard</th>
                <th>Chart View</th>
              </tr>
            </thead>
            <tbody id="rs-trades-body">
              <tr><td colspan="13" style="text-align:center; padding:20px; color:#64748b;">Click "Run 1-Min Replay Backtest" above to run high-resolution simulation.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Modal: Saved Research Tests Browser -->
      <div id="modal-saved-tests" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeSavedTestsModal();">
        <div class="modal-card">
          <div class="modal-header">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:18px;">📚</span>
              <h3 style="margin:0; font-size:15px; font-weight:700; color:#f8fafc;">Saved Research Tests &amp; Permlinks</h3>
            </div>
            <button class="btn-close-modal" onclick="closeSavedTestsModal()">&times;</button>
          </div>
          <div class="modal-body" id="saved-tests-list">
            <div style="text-align:center; padding:20px; color:#64748b;">Loading saved tests...</div>
          </div>
        </div>
      </div>

      <!-- Floating Toast Notification -->
      <div id="research-toast" class="research-toast"></div>
    </div>
    """


def get_research_js() -> str:
    return """
    // Main Tab Switching: Live Operations vs. Research Suite
    function switchMainView(view) {
      const liveTab = document.getElementById('btn-tab-live');
      const rsTab = document.getElementById('btn-tab-research');
      const liveView = document.getElementById('view-live');
      const rsView = document.getElementById('view-research');
      if (!liveTab || !rsTab || !liveView || !rsView) return;

      if (view === 'research') {
        liveTab.classList.remove('active');
        rsTab.classList.add('active');
        liveView.style.display = 'none';
        rsView.style.display = 'block';
        if (!window.hasLoadedResearchBacktest) {
          window.hasLoadedResearchBacktest = true;
          runResearchBacktest();
        } else {
          resizeAndRedrawCharts();
        }
      } else {
        rsTab.classList.remove('active');
        liveTab.classList.add('active');
        rsView.style.display = 'none';
        liveView.style.display = 'block';
      }
      localStorage.setItem('active_main_view', view);
    }

    const CHAMPION_DEFAULTS = {
      'BTCUSDT': { d_pct: 1.0, confirm_mult: 0.80, b1_tp_mult: 2.80, b2_tp_mult: 3.50, leverage: 10 },
      'ETHUSDT': { d_pct: 1.0, confirm_mult: 0.80, b1_tp_mult: 2.80, b2_tp_mult: 3.00, leverage: 10 },
      'SOLUSDT': { d_pct: 1.0, confirm_mult: 0.80, b1_tp_mult: 2.80, b2_tp_mult: 3.50, leverage: 5 },
      'PAXGUSDT': { d_pct: 1.0, confirm_mult: 0.80, b1_tp_mult: 2.80, b2_tp_mult: 3.50, leverage: 10 }
    };

    function onResearchSymbolChange() {
      const sym = document.getElementById('rs-symbol').value;
      const def = CHAMPION_DEFAULTS[sym] || CHAMPION_DEFAULTS['BTCUSDT'];
      document.getElementById('rs-d-pct').value = def.d_pct;
      document.getElementById('rs-confirm-mult').value = def.confirm_mult;
      document.getElementById('rs-b1-tp-mult').value = def.b1_tp_mult;
      document.getElementById('rs-b2-tp-mult').value = def.b2_tp_mult;
      document.getElementById('rs-leverage').value = def.leverage;
    }

    function setResearchStatus(status, text) {
      const badge = document.getElementById('rs-status-badge');
      if (!badge) return;
      badge.className = 'rs-status-' + status;
      badge.innerHTML = text;
    }

    window.latestResearchBacktestData = null;
    window.latestResearchMCData = null;
    window.chartViewState = {
      windowBars: 250,
      startIdx: 0,
      endIdx: 250,
      showEma: true,
      showRays: true,
      highlightedCycleId: null
    };

    async function runResearchBacktest() {
      const sym = document.getElementById('rs-symbol').value;
      const bars = parseInt(document.getElementById('rs-bars').value) || 2000;
      const capital = parseFloat(document.getElementById('rs-capital').value) || 1000;
      const lev = parseFloat(document.getElementById('rs-leverage').value) || 10;
      const d_pct = parseFloat(document.getElementById('rs-d-pct').value) || 1.0;
      const confirm_mult = parseFloat(document.getElementById('rs-confirm-mult').value) || 0.80;
      const b1_tp_mult = parseFloat(document.getElementById('rs-b1-tp-mult').value) || 2.80;
      const b2_tp_mult = parseFloat(document.getElementById('rs-b2-tp-mult').value) || 3.50;

      const payload = {
        symbol: sym,
        bars: bars,
        initial_capital: capital,
        custom_params: {
          d_pct: d_pct,
          confirm_mult: confirm_mult,
          b1_tp_mult: b1_tp_mult,
          b2_tp_mult: b2_tp_mult,
          leverage: lev
        }
      };

      const btn = document.getElementById('btn-run-backtest');
      if (btn) btn.disabled = true;
      setResearchStatus('running', '⏳ Replaying ' + bars + ' 1m candles...');

      try {
        const res = await fetch('/api/research/backtest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        window.latestResearchBacktestData = data;
        renderBacktestKPIs(data);
        renderJesseReport(data);
        renderSimulatedTrades(data.trades);
        initMasterChart(data);
        drawEquityChart(data.equity_points || data.equity_curve, data.buy_hold_curve);
        drawDrawdownChart(data.drawdown_series || data.drawdown_curve);
        drawScenarioChart(data.scenario_counts);
        setResearchStatus('done', '✓ 1-Min Replay Complete (' + data.total_trades + ' cycles)');

        if (data.test_id) {
          showActiveTestPermlink({
            test_id: data.test_id,
            test_url: data.test_url,
            test_type: 'backtest',
            title: sym + ' 1-Min Replay Backtest',
            symbol: sym,
            created_at: new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC',
            summary: {
              symbol: sym,
              net_profit: data.net_profit,
              win_rate: data.win_rate,
              profit_factor: data.profit_factor,
              max_drawdown_pct: data.max_drawdown_pct,
              total_trades: data.total_trades,
              expectancy_usd: data.expectancy_usd,
              cagr_pct: data.cagr_pct
            }
          });
          updateSavedTestsCount();
        }
      } catch (err) {
        setResearchStatus('error', '✗ Backtest Error: ' + err.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function runResearchMonteCarlo() {
      const sym = document.getElementById('rs-symbol').value;
      const bars = parseInt(document.getElementById('rs-bars').value) || 2000;
      const payload = {
        symbol: sym,
        bars: bars,
        iterations: 5000,
        permutations: 1000
      };

      const btn = document.getElementById('btn-run-mc');
      if (btn) btn.disabled = true;
      setResearchStatus('running', '🎲 Simulating 5,000 bootstrap paths & 1,000 RST runs...');

      try {
        const res = await fetch('/api/research/monte-carlo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        window.latestResearchMCData = data;
        renderMCKPIs(data);
        if (data.monte_carlo && data.monte_carlo.fan_chart) {
          drawFanChart(data.monte_carlo.fan_chart);
        }
        setResearchStatus('done', '✓ 5,000-Path MC & RST Complete');

        if (data.test_id) {
          showActiveTestPermlink({
            test_id: data.test_id,
            test_url: data.test_url,
            test_type: 'monte_carlo',
            title: sym + ' Monte Carlo (5k) & RST',
            symbol: sym,
            created_at: new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC',
            summary: {
              symbol: sym,
              median_profit: data.monte_carlo ? data.monte_carlo.median_profit : 0,
              prob_profit: data.monte_carlo ? data.monte_carlo.prob_profit : 0,
              risk_of_ruin: data.monte_carlo ? data.monte_carlo.risk_of_ruin : 0,
              p_value: data.rst ? data.rst.p_value : 0,
              is_significant: data.rst ? data.rst.is_significant : false
            }
          });
          updateSavedTestsCount();
        }
      } catch (err) {
        setResearchStatus('error', '✗ MC Error: ' + err.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function runResearchOptimize() {
      const sym = document.getElementById('rs-symbol').value;
      const bars = parseInt(document.getElementById('rs-bars').value) || 2000;
      const payload = {
        symbol: sym,
        bars: bars,
        trials: 25,
        objective: 'sharpe',
        train_ratio: 0.70
      };

      const btn = document.getElementById('btn-run-opt');
      if (btn) btn.disabled = true;
      setResearchStatus('running', '🧬 Running Jesse Walk-Forward Optimizer (70/30 split)...');

      try {
        const res = await fetch('/api/research/optimize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        renderOptimizerResults(data);
        setResearchStatus('done', '✓ Walk-Forward Optimization Complete');

        if (data.test_id) {
          showActiveTestPermlink({
            test_id: data.test_id,
            test_url: data.test_url,
            test_type: 'optimizer',
            title: sym + ' Walk-Forward Optimization',
            symbol: sym,
            created_at: new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC',
            summary: {
              symbol: sym,
              best_fitness: data.best_fitness,
              trials: data.trials_evaluated,
              objective: data.objective
            }
          });
          updateSavedTestsCount();
        }
      } catch (err) {
        setResearchStatus('error', '✗ Optimization Error: ' + err.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    }


    function renderBacktestKPIs(d) {
      const pnlEl = document.getElementById('kpi-net-profit');
      const retEl = document.getElementById('kpi-return-pct');
      const wrEl = document.getElementById('kpi-win-rate');
      const shEl = document.getElementById('kpi-shield-rate');
      const ddEl = document.getElementById('kpi-max-dd');
      const ddpEl = document.getElementById('kpi-max-dd-pct');
      const shpEl = document.getElementById('kpi-sharpe');
      const feeEl = document.getElementById('kpi-fees');

      const pnl = d.net_profit || 0;
      if (pnlEl) {
        pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
        pnlEl.style.color = pnl >= 0 ? '#38bdf8' : '#ef4444';
      }
      if (retEl) {
        const ret = ((pnl / 1000.0) * 100.0);
        const pf = d.profit_factor || 0;
        retEl.textContent = (ret >= 0 ? '+' : '') + ret.toFixed(1) + '% • PF: ' + pf.toFixed(2);
      }
      if (wrEl) wrEl.textContent = (d.win_rate || 0).toFixed(1) + '%';
      if (shEl) shEl.textContent = 'Shield: ' + (d.shield_rate || 0).toFixed(1) + '% • ' + (d.total_trades || 0) + ' Cycles';
      if (ddEl) ddEl.textContent = '-$' + (d.max_drawdown || 0).toFixed(2);
      if (ddpEl) ddpEl.textContent = (d.max_drawdown_pct || 0).toFixed(2) + '% intra-candle';
      if (shpEl) shpEl.textContent = (d.sharpe_ratio || 0).toFixed(2);
      if (feeEl) feeEl.textContent = 'Taker Fees: $' + (d.total_fees || 0).toFixed(2);
    }

    function renderJesseReport(d) {
      const pnl = d.net_profit || 0;
      const setVal = (id, val, color) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = val;
        if (color) el.style.color = color;
      };

      // Col 1: Capital
      setVal('jr-net-profit', (pnl >= 0 ? '+$':'-$') + Math.abs(pnl).toFixed(2), pnl >= 0 ? '#4ade80' : '#f87171');
      setVal('jr-return-pct', ((pnl / 1000.0) * 100.0).toFixed(2) + '%', pnl >= 0 ? '#4ade80' : '#f87171');
      setVal('jr-cagr', (d.cagr_pct || 0).toFixed(2) + '%');
      setVal('jr-gross-profit', '+$' + (d.gross_profit || 0).toFixed(2));
      setVal('jr-gross-loss', '-$' + (d.gross_loss || 0).toFixed(2));
      setVal('jr-profit-factor', (d.profit_factor || 0).toFixed(2));
      setVal('jr-expectancy', (d.expectancy_usd >= 0 ? '+$':'-$') + Math.abs(d.expectancy_usd || 0).toFixed(2), (d.expectancy_usd >= 0) ? '#4ade80':'#f87171');
      setVal('jr-fees', '-$' + (d.total_fees || 0).toFixed(2));

      // Col 2: Win/Loss
      setVal('jr-win-rate', (d.win_rate || 0).toFixed(1) + '%');
      setVal('jr-shield-rate', (d.shield_rate || 0).toFixed(1) + '%');
      setVal('jr-total-trades', d.total_trades || 0);
      setVal('jr-win-trades', d.winning_trades || 0);
      setVal('jr-loss-trades', d.losing_trades || 0);
      setVal('jr-wl-ratio', (d.win_loss_ratio || 0).toFixed(2));
      setVal('jr-avg-win', '+$' + (d.avg_win || 0).toFixed(2));
      setVal('jr-avg-loss', '-$' + (d.avg_loss || 0).toFixed(2));
      setVal('jr-large-win', '+$' + (d.largest_win || 0).toFixed(2));
      setVal('jr-large-loss', '-$' + (d.largest_loss || 0).toFixed(2));

      // Col 3: Directional
      setVal('jr-long-trades', d.long_trades || 0);
      setVal('jr-long-wr', (d.long_win_rate || 0).toFixed(1) + '%');
      setVal('jr-long-pnl', ((d.long_profit || 0) >= 0 ? '+$':'-$') + Math.abs(d.long_profit || 0).toFixed(2), (d.long_profit || 0) >= 0 ? '#4ade80':'#f87171');
      setVal('jr-short-trades', d.short_trades || 0);
      setVal('jr-short-wr', (d.short_win_rate || 0).toFixed(1) + '%');
      setVal('jr-short-pnl', ((d.short_profit || 0) >= 0 ? '+$':'-$') + Math.abs(d.short_profit || 0).toFixed(2), (d.short_profit || 0) >= 0 ? '#4ade80':'#f87171');
      const sc = d.scenario_counts || {};
      setVal('jr-eg-hits', sc.EXHAUSTION_GUARD || 0);
      setVal('jr-timeouts', sc.BRANCH_3_TIMEOUT || 0);

      // Col 4: Risk
      setVal('jr-max-dd', '-$' + (d.max_drawdown || 0).toFixed(2) + ' (' + (d.max_drawdown_pct || 0).toFixed(1) + '%)');
      setVal('jr-sharpe', (d.sharpe_ratio || 0).toFixed(2));
      setVal('jr-sortino', (d.sortino_ratio || 0).toFixed(2));
      setVal('jr-calmar', (d.calmar_ratio || 0).toFixed(2));
      setVal('jr-consec-wins', d.max_consecutive_wins || 0);
      setVal('jr-consec-loss', d.max_consecutive_losses || 0);
      setVal('jr-avg-hold', (d.avg_bars_held || 0).toFixed(1) + ' bars');
      setVal('jr-max-hold', (d.max_bars_held || 0) + ' / ' + (d.min_bars_held || 0) + ' bars');
    }

    function renderMCKPIs(d) {
      const mc = d.monte_carlo || {};
      const rst = d.rst || {};

      const medEl = document.getElementById('kpi-mc-median');
      const rngEl = document.getElementById('kpi-mc-range');
      const rstpEl = document.getElementById('kpi-rst-p');
      const rstsigEl = document.getElementById('kpi-rst-sig');

      if (medEl) medEl.textContent = '$' + (mc.percentile_50 || 0).toFixed(2);
      if (rngEl) rngEl.textContent = '5th: $' + (mc.percentile_5 || 0).toFixed(2) + ' | 95th: $' + (mc.percentile_95 || 0).toFixed(2);
      if (rstpEl) {
        const p = rst.p_value || 0;
        rstpEl.textContent = 'p = ' + p.toFixed(4);
        rstpEl.style.color = (p < 0.05) ? '#4ade80' : '#f87171';
      }
      if (rstsigEl) {
        const sig = rst.is_significant ? 'Statistically Significant (p < 0.05)' : 'Not Statistically Significant';
        rstsigEl.textContent = sig + ' • Conf: ' + (rst.confidence_level_pct || 0).toFixed(1) + '%';
      }
    }

    function renderOptimizerResults(d) {
      const card = document.getElementById('optimizer-results-card');
      if (!card) return;
      card.style.display = 'block';

      const bp = d.best_params || {};
      const train = d.best_training_metrics || {};
      const test = d.best_testing_metrics || {};

      document.getElementById('opt-best-params').textContent = 'D: ' + bp.d_pct + '%, B1: ' + bp.b1_tp_mult + 'x, B2: ' + bp.b2_tp_mult + 'x';
      document.getElementById('opt-train-metrics').textContent = 'Sharpe: ' + (train.sharpe_ratio || 0).toFixed(2) + ' • Profit: $' + (train.net_profit || 0).toFixed(2) + ' • WR: ' + (train.win_rate || 0).toFixed(1) + '%';
      document.getElementById('opt-test-metrics').textContent = 'Sharpe: ' + (test.sharpe_ratio || 0).toFixed(2) + ' • Profit: $' + (test.net_profit || 0).toFixed(2) + ' • WR: ' + (test.win_rate || 0).toFixed(1) + '%';
      document.getElementById('opt-fitness-score').textContent = (d.best_fitness || 0).toFixed(3);

      const tbody = document.getElementById('opt-leaderboard-body');
      if (tbody && d.leaderboard) {
        let html = '';
        d.leaderboard.forEach(t => {
          const p = t.params || {};
          const statusBadge = t.is_overfit 
            ? '<span style="color:#ef4444; font-weight:700;">OVERFIT</span>' 
            : '<span style="color:#4ade80; font-weight:700;">ROBUST</span>';
          html += '<tr style="border-bottom:1px solid #1e293b;">' +
            '<td style="padding:6px 10px; font-weight:600;">#' + t.trial + '</td>' +
            '<td style="padding:6px 10px; font-family:monospace;">D=' + p.d_pct + '%, B1=' + p.b1_tp_mult + ', B2=' + p.b2_tp_mult + '</td>' +
            '<td style="padding:6px 10px; color:' + (t.training_profit >= 0 ? '#4ade80':'#f87171') + ';">$' + t.training_profit.toFixed(1) + '</td>' +
            '<td style="padding:6px 10px;">' + t.training_sharpe.toFixed(2) + '</td>' +
            '<td style="padding:6px 10px; color:' + (t.testing_profit >= 0 ? '#4ade80':'#f87171') + ';">$' + t.testing_profit.toFixed(1) + '</td>' +
            '<td style="padding:6px 10px;">' + t.testing_sharpe.toFixed(2) + '</td>' +
            '<td style="padding:6px 10px; color:#ef4444;">-$' + t.testing_max_dd.toFixed(1) + '</td>' +
            '<td style="padding:6px 10px; font-weight:700; color:#fbbf24;">' + t.fitness_score.toFixed(3) + '</td>' +
            '<td style="padding:6px 10px;">' + statusBadge + '</td>' +
          '</tr>';
        });
        tbody.innerHTML = html;
      }
    }

    function renderSimulatedTrades(trades) {
      window.allSimTrades = trades || [];
      filterResearchTrades(document.getElementById('rs-trade-filter').value);
    }

    function filterResearchTrades(filterVal) {
      const tbody = document.getElementById('rs-trades-body');
      if (!tbody) return;
      const trades = window.allSimTrades || [];

      let filtered = trades;
      if (filterVal === 'BRANCH_1_TP') {
        filtered = trades.filter(t => t.scenario.includes('BRANCH_1'));
      } else if (filterVal === 'SCENARIO_5_FLIP') {
        filtered = trades.filter(t => t.scenario.includes('SCENARIO_5'));
      } else if (filterVal === 'EXHAUSTION_GUARD') {
        filtered = trades.filter(t => t.exhaustion_guard_triggered || t.scenario.includes('EXHAUSTION'));
      } else if (filterVal === 'BRANCH_3_TIMEOUT') {
        filtered = trades.filter(t => t.scenario.includes('TIMEOUT'));
      }

      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:20px; color:#64748b;">No cycles match filter "' + filterVal + '".</td></tr>';
        return;
      }

      let html = '';
      filtered.forEach(t => {
        let badgeClass = 'badge-branch1';
        if (t.scenario.includes('SCENARIO_5')) badgeClass = 'badge-scenario5';
        else if (t.exhaustion_guard_triggered || t.scenario.includes('EXHAUSTION')) badgeClass = 'badge-exhaustion';
        else if (t.scenario.includes('TIMEOUT')) badgeClass = 'badge-timeout';

        const pnlColor = (t.net_pnl >= 0) ? '#4ade80' : '#f87171';
        const egCol = t.exhaustion_guard_triggered 
          ? '<span style="color:#fbbf24; font-weight:700;">⚡ ARMED &amp; TRIGGERED</span>' 
          : '<span style="color:#64748b;">—</span>';

        html += '<tr style="border-bottom:1px solid #1e293b;" id="trade-row-' + t.cycle_id + '">' +
          '<td style="font-weight:600;">#' + t.cycle_id + '</td>' +
          '<td style="font-weight:700; color:' + (t.direction === 'LONG' || t.direction === 'bullish' ? '#4ade80':'#f87171') + ';">' + t.direction + '</td>' +
          '<td><span class="' + badgeClass + '">' + t.scenario + '</span></td>' +
          '<td style="font-family:monospace;">$' + t.entry_price.toFixed(2) + '</td>' +
          '<td style="font-family:monospace;">$' + (t.primary_exit || t.exit_price).toFixed(2) + '</td>' +
          '<td style="font-family:monospace;">$' + (t.counter_exit || t.exit_price).toFixed(2) + '</td>' +
          '<td style="font-family:monospace;">$' + t.gross_pnl.toFixed(2) + '</td>' +
          '<td style="font-family:monospace; color:#ef4444;">-$' + t.fees.toFixed(2) + '</td>' +
          '<td style="font-family:monospace; font-weight:700; color:' + pnlColor + ';">' + (t.net_pnl >= 0 ? '+$':'-$') + Math.abs(t.net_pnl).toFixed(2) + '</td>' +
          '<td style="font-family:monospace; font-weight:600; color:' + pnlColor + ';">' + (t.return_pct >= 0 ? '+': '') + t.return_pct.toFixed(2) + '%</td>' +
          '<td>' + t.bars_held + ' bars</td>' +
          '<td>' + egCol + '</td>' +
          '<td><button class="btn-focus-trade" onclick="focusTradeOnChart(' + t.cycle_id + ')">🔍 View</button></td>' +
        '</tr>';
      });
      tbody.innerHTML = html;
    }

    // ==========================================
    // Jesse-Style Price & Trades Master Chart
    // ==========================================
    function initMasterChart(data) {
      if (!data || !data.price_candles || data.price_candles.length === 0) return;
      const totalBars = data.price_candles.length;
      const winBars = Math.min(window.chartViewState.windowBars === 'all' ? totalBars : window.chartViewState.windowBars, totalBars);
      window.chartViewState.endIdx = totalBars;
      window.chartViewState.startIdx = Math.max(0, totalBars - winBars);

      setupMasterChartInteractivity();
      drawPriceTradesMasterChart();
    }

    function setChartWindow(bars) {
      window.chartViewState.windowBars = bars;
      const data = window.latestResearchBacktestData;
      if (!data || !data.price_candles) return;
      const totalBars = data.price_candles.length;
      if (bars === 'all') {
        window.chartViewState.startIdx = 0;
        window.chartViewState.endIdx = totalBars;
      } else {
        window.chartViewState.endIdx = totalBars;
        window.chartViewState.startIdx = Math.max(0, totalBars - parseInt(bars));
      }
      drawPriceTradesMasterChart();
    }

    function panChart(offset) {
      const data = window.latestResearchBacktestData;
      if (!data || !data.price_candles) return;
      const totalBars = data.price_candles.length;
      const winSize = window.chartViewState.endIdx - window.chartViewState.startIdx;

      let newStart = window.chartViewState.startIdx + offset;
      let newEnd = window.chartViewState.endIdx + offset;

      if (newStart < 0) {
        newStart = 0;
        newEnd = Math.min(totalBars, winSize);
      }
      if (newEnd > totalBars) {
        newEnd = totalBars;
        newStart = Math.max(0, totalBars - winSize);
      }

      window.chartViewState.startIdx = newStart;
      window.chartViewState.endIdx = newEnd;
      drawPriceTradesMasterChart();
    }

    function toggleOverlay(type) {
      if (type === 'ema') {
        window.chartViewState.showEma = !window.chartViewState.showEma;
        document.getElementById('btn-toggle-ema').classList.toggle('active', window.chartViewState.showEma);
      } else if (type === 'rays') {
        window.chartViewState.showRays = !window.chartViewState.showRays;
        document.getElementById('btn-toggle-rays').classList.toggle('active', window.chartViewState.showRays);
      }
      drawPriceTradesMasterChart();
    }

    function focusTradeOnChart(cycleId) {
      const data = window.latestResearchBacktestData;
      if (!data || !data.trades) return;
      const trade = data.trades.find(t => t.cycle_id === cycleId);
      if (!trade) return;

      window.chartViewState.highlightedCycleId = cycleId;

      const totalBars = data.price_candles.length;
      const entryIdx = trade.entry_bar_idx || 0;
      const exitIdx = trade.exit_bar_idx || (entryIdx + trade.bars_held);

      const span = exitIdx - entryIdx;
      const pad = Math.max(25, span * 2);

      window.chartViewState.startIdx = Math.max(0, entryIdx - pad);
      window.chartViewState.endIdx = Math.min(totalBars, exitIdx + pad);

      drawPriceTradesMasterChart();

      // Highlight row in table
      document.querySelectorAll('#rs-trades-table tr').forEach(tr => tr.style.background = '');
      const tr = document.getElementById('trade-row-' + cycleId);
      if (tr) tr.style.background = 'rgba(56, 189, 248, 0.15)';

      // Scroll chart into view smoothly
      const chartCard = document.querySelector('.price-trades-card');
      if (chartCard) chartCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function drawPriceTradesMasterChart() {
      const data = window.latestResearchBacktestData;
      if (!data || !data.price_candles || data.price_candles.length === 0) return;

      const c = setupCanvas('chart-price-trades');
      if (!c) return;
      const ctx = c.ctx, w = c.width, h = c.height;

      ctx.clearRect(0, 0, w, h);

      const allCandles = data.price_candles;
      const startIdx = Math.max(0, window.chartViewState.startIdx);
      const endIdx = Math.min(allCandles.length, window.chartViewState.endIdx);
      const visibleCandles = allCandles.slice(startIdx, endIdx);

      if (visibleCandles.length < 2) return;

      const rangeLabel = document.getElementById('chart-range-label');
      if (rangeLabel) {
        rangeLabel.textContent = 'Bars ' + startIdx + '–' + endIdx + ' of ' + allCandles.length + ' (' + visibleCandles[0].time + ' to ' + visibleCandles[visibleCandles.length - 1].time + ')';
      }

      const padding = { top: 25, right: 65, bottom: 30, left: 15 };
      const plotW = w - padding.left - padding.right;
      const plotH = h - padding.top - padding.bottom;

      let minP = Math.min(...visibleCandles.map(c => c.l));
      let maxP = Math.max(...visibleCandles.map(c => c.h));
      const pRange = (maxP - minP) || 1.0;
      minP -= pRange * 0.05;
      maxP += pRange * 0.05;

      const getX = (globalIdx) => {
        const localIdx = globalIdx - startIdx;
        return padding.left + (localIdx / (visibleCandles.length - 1)) * plotW;
      };
      const getY = (price) => {
        return padding.top + plotH - ((price - minP) / (maxP - minP)) * plotH;
      };

      // Save coordinate transformer for mouse crosshair
      window.masterChartCoords = { startIdx, endIdx, getX, getY, minP, maxP, padding, plotW, plotH, visibleCandles };

      // 1. Grid Lines & Price Ticks
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 5; i++) {
        const y = padding.top + (i / 5) * plotH;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();

        const pVal = maxP - (i / 5) * (maxP - minP);
        ctx.fillStyle = '#64748b';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText('$' + pVal.toFixed(1), w - padding.right + 6, y + 3);
      }

      // 2. Candlesticks
      const candleW = Math.max(2, (plotW / visibleCandles.length) * 0.7);
      visibleCandles.forEach((cdl, idx) => {
        const globalIdx = startIdx + idx;
        const x = getX(globalIdx);
        const yOpen = getY(cdl.o);
        const yClose = getY(cdl.c);
        const yHigh = getY(cdl.h);
        const yLow = getY(cdl.l);

        const isBull = cdl.c >= cdl.o;
        const color = isBull ? '#22c55e' : '#ef4444';

        // High-low wick
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, yHigh);
        ctx.lineTo(x, yLow);
        ctx.stroke();

        // Candle body
        ctx.fillStyle = color;
        const topY = Math.min(yOpen, yClose);
        const bodyH = Math.max(1.5, Math.abs(yOpen - yClose));
        ctx.fillRect(x - candleW / 2, topY, candleW, bodyH);
      });

      // 3. Technical Indicators: EMA(9) & EMA(21)
      if (window.chartViewState.showEma) {
        // EMA(9)
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        let firstEma9 = true;
        visibleCandles.forEach((cdl, idx) => {
          if (cdl.ema9 != null) {
            const x = getX(startIdx + idx);
            const y = getY(cdl.ema9);
            if (firstEma9) { ctx.moveTo(x, y); firstEma9 = false; }
            else ctx.lineTo(x, y);
          }
        });
        ctx.stroke();

        // EMA(21)
        ctx.strokeStyle = '#a855f7';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        let firstEma21 = true;
        visibleCandles.forEach((cdl, idx) => {
          if (cdl.ema21 != null) {
            const x = getX(startIdx + idx);
            const y = getY(cdl.ema21);
            if (firstEma21) { ctx.moveTo(x, y); firstEma21 = false; }
            else ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
      }

      // 4. Trade Rays & Markers (Jesse Mode)
      const trades = data.trades || [];
      trades.forEach(t => {
        const eIdx = t.entry_bar_idx || 0;
        const xIdx = t.exit_bar_idx || (eIdx + t.bars_held);

        // Check if trade overlaps current visible window
        if (xIdx < startIdx || eIdx > endIdx) return;

        const xEntry = getX(eIdx);
        const yEntry = getY(t.entry_price);
        const xExit = getX(xIdx);
        const yExit = getY(t.exit_price);

        const isWin = t.net_pnl > 0.05;
        const isEg = t.exhaustion_guard_triggered;
        const isHighlighted = (t.cycle_id === window.chartViewState.highlightedCycleId);

        let strokeColor = isWin ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)';
        if (isEg) strokeColor = 'rgba(245, 158, 11, 0.85)';
        if (isHighlighted) strokeColor = '#38bdf8';

        // Connecting Trade Ray
        if (window.chartViewState.showRays) {
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = isHighlighted ? 2.5 : 1.5;
          ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.moveTo(xEntry, yEntry);
          ctx.lineTo(xExit, yExit);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Highlight ring if focused
        if (isHighlighted) {
          ctx.strokeStyle = '#38bdf8';
          ctx.lineWidth = 2;
          ctx.strokeRect(Math.min(xEntry, xExit) - 8, Math.min(yEntry, yExit) - 8, Math.abs(xExit - xEntry) + 16, Math.abs(yExit - yEntry) + 16);
        }

        // Entry Marker (Triangle)
        if (eIdx >= startIdx && eIdx <= endIdx) {
          const isLong = (t.direction === 'LONG' || t.direction === 'bullish');
          ctx.fillStyle = isLong ? '#22c55e' : '#ef4444';
          ctx.beginPath();
          if (isLong) {
            ctx.moveTo(xEntry, yEntry + 4);
            ctx.lineTo(xEntry - 5, yEntry + 14);
            ctx.lineTo(xEntry + 5, yEntry + 14);
          } else {
            ctx.moveTo(xEntry, yEntry - 4);
            ctx.lineTo(xEntry - 5, yEntry - 14);
            ctx.lineTo(xEntry + 5, yEntry - 14);
          }
          ctx.closePath();
          ctx.fill();

          // Entry tag
          ctx.font = '9px JetBrains Mono, monospace';
          ctx.fillStyle = '#e2e8f0';
          ctx.textAlign = 'center';
          ctx.fillText('#' + t.cycle_id, xEntry, isLong ? yEntry + 24 : yEntry - 18);
        }

        // Exit Marker
        if (xIdx >= startIdx && xIdx <= endIdx) {
          ctx.fillStyle = isEg ? '#fbbf24' : (isWin ? '#22c55e' : '#ef4444');
          ctx.beginPath();
          ctx.arc(xExit, yExit, 4.5, 0, Math.PI * 2);
          ctx.fill();

          // PnL badge text
          ctx.font = '10px JetBrains Mono, monospace';
          ctx.fillStyle = isEg ? '#fbbf24' : (isWin ? '#4ade80' : '#f87171');
          ctx.textAlign = 'center';
          const pnlStr = (t.net_pnl >= 0 ? '+$' : '-$') + Math.abs(t.net_pnl).toFixed(1);
          ctx.fillText(pnlStr, xExit, yExit - 8);
        }
      });
    }

    function setupMasterChartInteractivity() {
      const canvas = document.getElementById('chart-price-trades');
      const tooltip = document.getElementById('chart-tooltip');
      const hud = document.getElementById('chart-hover-hud');
      if (!canvas || canvas.hasRegisteredEvents) return;
      canvas.hasRegisteredEvents = true;

      canvas.addEventListener('mousemove', (e) => {
        const coords = window.masterChartCoords;
        if (!coords || !window.latestResearchBacktestData) return;

        const rect = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        if (mouseX < coords.padding.left || mouseX > canvas.clientWidth - coords.padding.right) {
          if (tooltip) tooltip.style.display = 'none';
          return;
        }

        // Find closest candle
        const pctX = (mouseX - coords.padding.left) / coords.plotW;
        const candleLocalIdx = Math.round(pctX * (coords.visibleCandles.length - 1));
        const cdl = coords.visibleCandles[candleLocalIdx];
        if (!cdl) return;

        const globalIdx = coords.startIdx + candleLocalIdx;

        // Find if a trade entered or exited at or near this bar
        const trades = window.latestResearchBacktestData.trades || [];
        const nearTrade = trades.find(t => Math.abs((t.entry_bar_idx || 0) - globalIdx) <= 1 || Math.abs((t.exit_bar_idx || 0) - globalIdx) <= 1);

        if (hud) {
          hud.innerHTML = 'Bar #' + globalIdx + ' | ' + cdl.time + ' | O: $' + cdl.o.toFixed(1) + ' H: $' + cdl.h.toFixed(1) + ' L: $' + cdl.l.toFixed(1) + ' C: $' + cdl.c.toFixed(1);
        }

        if (nearTrade && tooltip) {
          const isWin = nearTrade.net_pnl > 0;
          const pnlColor = isWin ? '#4ade80' : '#f87171';
          tooltip.style.display = 'block';
          tooltip.style.left = Math.min(mouseX + 15, canvas.clientWidth - 220) + 'px';
          tooltip.style.top = Math.max(10, mouseY - 70) + 'px';
          tooltip.innerHTML = '<div style="font-weight:700; color:#38bdf8; margin-bottom:4px;">Trade Cycle #' + nearTrade.cycle_id + ' (' + nearTrade.direction + ')</div>' +
            '<div style="color:#94a3b8;">Scenario: <span style="color:#f8fafc;">' + nearTrade.scenario + '</span></div>' +
            '<div style="color:#94a3b8;">Entry: <span style="color:#f8fafc;">$' + nearTrade.entry_price.toFixed(2) + '</span> (' + nearTrade.entry_time + ')</div>' +
            '<div style="color:#94a3b8;">Exit: <span style="color:#f8fafc;">$' + nearTrade.exit_price.toFixed(2) + '</span> (' + nearTrade.exit_time + ')</div>' +
            '<div style="color:#94a3b8;">Net P&amp;L: <span style="color:' + pnlColor + '; font-weight:700;">' + (nearTrade.net_pnl >= 0 ? '+$':'-$') + Math.abs(nearTrade.net_pnl).toFixed(2) + ' (' + nearTrade.return_pct.toFixed(2) + '%)</span></div>' +
            '<div style="color:#94a3b8;">Fees: <span style="color:#ef4444;">-$' + nearTrade.fees.toFixed(2) + '</span> • Held: ' + nearTrade.bars_held + ' bars</div>' +
            (nearTrade.exhaustion_guard_triggered ? '<div style="color:#fbbf24; font-weight:700; margin-top:2px;">⚡ Flash-Crash Exhaustion Guard Activated</div>' : '');
        } else if (tooltip) {
          tooltip.style.display = 'none';
        }
      });

      canvas.addEventListener('mouseleave', () => {
        if (tooltip) tooltip.style.display = 'none';
        if (hud) hud.textContent = 'Hover over chart for details';
      });
    }

    // Helper to normalize array of floats vs array of objects
    function normalizeSeries(series, key) {
      if (!series || !Array.isArray(series)) return [];
      return series.map(item => {
        if (typeof item === 'number') return item;
        if (item && typeof item === 'object' && item[key] !== undefined) return Number(item[key]);
        return 0;
      });
    }

    // High-Resolution Native HTML5 Canvas Chart Renderers
    function setupCanvas(id) {
      const canvas = document.getElementById(id);
      if (!canvas) return null;
      const rect = canvas.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      const ctx = canvas.getContext('2d');
      ctx.resetTransform();
      ctx.scale(dpr, dpr);
      return { ctx: ctx, width: rect.width, height: rect.height };
    }

    function drawEquityChart(equityData, buyHoldData) {
      const equityCurve = normalizeSeries(equityData, 'equity');
      const buyHoldCurve = normalizeSeries(buyHoldData, 'price');
      const c = setupCanvas('chart-equity');
      if (!c || !equityCurve || equityCurve.length < 2) return;
      const ctx = c.ctx, w = c.width, h = c.height;

      ctx.clearRect(0, 0, w, h);
      const padding = { top: 20, right: 55, bottom: 25, left: 15 };
      const plotW = w - padding.left - padding.right;
      const plotH = h - padding.top - padding.bottom;

      let minVal = Math.min(...equityCurve, ...(buyHoldCurve.length ? buyHoldCurve : [equityCurve[0]]));
      let maxVal = Math.max(...equityCurve, ...(buyHoldCurve.length ? buyHoldCurve : [equityCurve[0]]));
      const range = (maxVal - minVal) || 1.0;
      minVal -= range * 0.05;
      maxVal += range * 0.05;

      const getX = (idx, total) => padding.left + (idx / (total - 1)) * plotW;
      const getY = (val) => padding.top + plotH - ((val - minVal) / (maxVal - minVal)) * plotH;

      // Draw Grid Lines
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = padding.top + (i / 4) * plotH;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();

        const val = maxVal - (i / 4) * (maxVal - minVal);
        ctx.fillStyle = '#64748b';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText('$' + val.toFixed(0), w - padding.right + 6, y + 3);
      }

      // Buy & Hold
      if (buyHoldCurve && buyHoldCurve.length === equityCurve.length) {
        ctx.strokeStyle = '#475569';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        buyHoldCurve.forEach((val, i) => {
          const x = getX(i, buyHoldCurve.length);
          const y = getY(val);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Equity Gradient Fill
      const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
      grad.addColorStop(0, 'rgba(56, 189, 248, 0.25)');
      grad.addColorStop(1, 'rgba(56, 189, 248, 0.0)');

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(getX(0, equityCurve.length), padding.top + plotH);
      equityCurve.forEach((val, i) => {
        ctx.lineTo(getX(i, equityCurve.length), getY(val));
      });
      ctx.lineTo(getX(equityCurve.length - 1, equityCurve.length), padding.top + plotH);
      ctx.closePath();
      ctx.fill();

      // Equity Line
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2;
      ctx.beginPath();
      equityCurve.forEach((val, i) => {
        const x = getX(i, equityCurve.length);
        const y = getY(val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function drawDrawdownChart(drawdownData) {
      const drawdownSeries = normalizeSeries(drawdownData, 'drawdown_pct');
      const c = setupCanvas('chart-drawdown');
      if (!c || !drawdownSeries || drawdownSeries.length < 2) return;
      const ctx = c.ctx, w = c.width, h = c.height;

      ctx.clearRect(0, 0, w, h);
      const padding = { top: 20, right: 55, bottom: 25, left: 15 };
      const plotW = w - padding.left - padding.right;
      const plotH = h - padding.top - padding.bottom;

      const maxDd = Math.max(...drawdownSeries, 1.0);
      const getX = (idx, total) => padding.left + (idx / (total - 1)) * plotW;
      const getY = (val) => padding.top + (val / maxDd) * plotH;

      // Grid Lines
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = padding.top + (i / 4) * plotH;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();

        const val = -(i / 4) * maxDd;
        ctx.fillStyle = '#64748b';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(val.toFixed(1) + '%', w - padding.right + 6, y + 3);
      }

      // Drawdown Area
      const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
      grad.addColorStop(0, 'rgba(239, 68, 68, 0.05)');
      grad.addColorStop(1, 'rgba(239, 68, 68, 0.35)');

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(getX(0, drawdownSeries.length), padding.top);
      drawdownSeries.forEach((val, i) => {
        ctx.lineTo(getX(i, drawdownSeries.length), getY(val));
      });
      ctx.lineTo(getX(drawdownSeries.length - 1, drawdownSeries.length), padding.top);
      ctx.closePath();
      ctx.fill();

      // Drawdown Line
      ctx.strokeStyle = '#ef4444';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      drawdownSeries.forEach((val, i) => {
        const x = getX(i, drawdownSeries.length);
        const y = getY(val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function drawFanChart(fanData) {
      const c = setupCanvas('chart-monte-carlo');
      if (!c || !fanData || !fanData.steps || fanData.steps.length < 2) return;
      const ctx = c.ctx, w = c.width, h = c.height;

      ctx.clearRect(0, 0, w, h);
      const padding = { top: 20, right: 55, bottom: 25, left: 15 };
      const plotW = w - padding.left - padding.right;
      const plotH = h - padding.top - padding.bottom;

      const p5 = fanData.p5 || [];
      const p25 = fanData.p25 || [];
      const p50 = fanData.p50 || [];
      const p75 = fanData.p75 || [];
      const p95 = fanData.p95 || [];
      const steps = fanData.steps || [];

      const minVal = Math.min(...p5) * 0.95;
      const maxVal = Math.max(...p95) * 1.05;
      const range = (maxVal - minVal) || 1.0;

      const getX = (idx) => padding.left + (idx / (steps.length - 1)) * plotW;
      const getY = (val) => padding.top + plotH - ((val - minVal) / range) * plotH;

      // Grid Lines
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = padding.top + (i / 4) * plotH;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();

        const val = maxVal - (i / 4) * range;
        ctx.fillStyle = '#64748b';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText('$' + val.toFixed(0), w - padding.right + 6, y + 3);
      }

      // Outer Cone (5th to 95th)
      ctx.fillStyle = 'rgba(168, 85, 247, 0.15)';
      ctx.beginPath();
      p95.forEach((val, i) => {
        const x = getX(i), y = getY(val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      for (let i = p5.length - 1; i >= 0; i--) {
        ctx.lineTo(getX(i), getY(p5[i]));
      }
      ctx.closePath();
      ctx.fill();

      // Inner Cone (25th to 75th)
      ctx.fillStyle = 'rgba(168, 85, 247, 0.25)';
      ctx.beginPath();
      p75.forEach((val, i) => {
        const x = getX(i), y = getY(val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      for (let i = p25.length - 1; i >= 0; i--) {
        ctx.lineTo(getX(i), getY(p25[i]));
      }
      ctx.closePath();
      ctx.fill();

      // Median Line (50th)
      ctx.strokeStyle = '#c084fc';
      ctx.lineWidth = 2;
      ctx.beginPath();
      p50.forEach((val, i) => {
        const x = getX(i), y = getY(val);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function drawScenarioChart(scenarioCounts) {
      const c = setupCanvas('chart-scenarios');
      if (!c) return;
      const ctx = c.ctx, w = c.width, h = c.height;
      ctx.clearRect(0, 0, w, h);

      const sc = scenarioCounts || { 'BRANCH_1_TP': 0, 'SCENARIO_5_FLIP': 0, 'EXHAUSTION_GUARD': 0, 'BRANCH_3_TIMEOUT': 0 };
      const items = [
        { label: 'Branch 1 Apex TP', count: sc.BRANCH_1_TP || 0, color: '#22c55e' },
        { label: 'Scenario 5 Size-Flip', count: sc.SCENARIO_5_FLIP || 0, color: '#38bdf8' },
        { label: 'Exhaustion Guard Hit', count: sc.EXHAUSTION_GUARD || 0, color: '#f59e0b' },
        { label: 'Branch 3 Timeout', count: sc.BRANCH_3_TIMEOUT || 0, color: '#64748b' }
      ];

      const total = items.reduce((sum, item) => sum + item.count, 0) || 1;
      const barH = 28;
      const gap = 18;
      const startY = 25;
      const maxW = w - 160;

      items.forEach((item, idx) => {
        const y = startY + idx * (barH + gap);
        const pct = (item.count / total) * 100;
        const barWidth = Math.max((pct / 100) * maxW, 4);

        // Label
        ctx.fillStyle = '#e2e8f0';
        ctx.font = '11px Inter, sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(item.label, 15, y - 6);

        // Track
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(15, y, maxW, barH);

        // Bar Fill
        ctx.fillStyle = item.color;
        ctx.fillRect(15, y, barWidth, barH);

        // Text Value
        ctx.fillStyle = '#f8fafc';
        ctx.font = '11px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(item.count + ' (' + pct.toFixed(1) + '%)', maxW + 25, y + 18);
      });
    }

    function resizeAndRedrawCharts() {
      drawPriceTradesMasterChart();
      if (window.latestResearchBacktestData) {
        drawEquityChart(window.latestResearchBacktestData.equity_points || window.latestResearchBacktestData.equity_curve, window.latestResearchBacktestData.buy_hold_curve);
        drawDrawdownChart(window.latestResearchBacktestData.drawdown_series || window.latestResearchBacktestData.drawdown_curve);
        drawScenarioChart(window.latestResearchBacktestData.scenario_counts);
      }
      if (window.latestResearchMCData && window.latestResearchMCData.monte_carlo && window.latestResearchMCData.monte_carlo.fan_chart) {
        drawFanChart(window.latestResearchMCData.monte_carlo.fan_chart);
      }
    }

    window.addEventListener('resize', () => {
      if (document.getElementById('view-research') && document.getElementById('view-research').style.display !== 'none') {
        resizeAndRedrawCharts();
      }
    });

    // =========================================================================
    // PERMLINK & UNIQUE TEST RUN MANAGEMENT
    // =========================================================================

    function showToast(msg) {
      let toast = document.getElementById('research-toast');
      if (!toast) return;
      toast.innerText = msg;
      toast.style.display = 'block';
      if (window.toastTimer) clearTimeout(window.toastTimer);
      window.toastTimer = setTimeout(() => {
        toast.style.display = 'none';
      }, 2500);
    }

    function showActiveTestPermlink(testObj) {
      if (!testObj || !testObj.test_id) return;
      const bar = document.getElementById('test-permlink-bar');
      const badge = document.getElementById('permlink-badge');
      const titleEl = document.getElementById('permlink-title');
      const tsEl = document.getElementById('permlink-ts');
      const sumEl = document.getElementById('permlink-summary-line');
      const inputEl = document.getElementById('permlink-input');
      if (!bar || !inputEl) return;

      const fullUrl = window.location.origin + (testObj.test_url || ('/dashboard?test_id=' + testObj.test_id));
      inputEl.value = fullUrl;

      const ttype = (testObj.test_type || 'test').toLowerCase();
      if (badge) {
        badge.innerText = ttype.toUpperCase().replace('_', ' ');
        if (ttype === 'backtest') badge.style.color = '#38bdf8';
        else if (ttype === 'monte_carlo') badge.style.color = '#c084fc';
        else if (ttype === 'optimizer') badge.style.color = '#fbbf24';
      }

      if (titleEl) titleEl.innerText = testObj.title || (testObj.symbol + ' Research Test');
      if (tsEl) tsEl.innerText = testObj.created_at || '';

      if (sumEl && testObj.summary) {
        const s = testObj.summary;
        if (ttype === 'backtest') {
          sumEl.innerHTML = 'Net Profit: <b style="color:' + ((s.net_profit||0)>=0?'#4ade80':'#f87171') + '">' + ((s.net_profit||0)>=0?'+':'') + '$' + (s.net_profit||0).toFixed(2) + '</b> &bull; Win Rate: <b>' + (s.win_rate||0).toFixed(1) + '%</b> &bull; PF: <b>' + (s.profit_factor||0).toFixed(2) + '</b> &bull; Max DD: <b style="color:#f87171">' + (s.max_drawdown_pct||0).toFixed(2) + '%</b> &bull; Cycles: <b>' + (s.total_trades||0) + '</b>';
        } else if (ttype === 'monte_carlo') {
          sumEl.innerHTML = 'MC Median: <b style="color:#c084fc">$' + (s.median_profit||0).toFixed(2) + '</b> &bull; Prob Profit: <b>' + (s.prob_profit||0).toFixed(1) + '%</b> &bull; RST p-val: <b>p = ' + (s.p_value||0).toFixed(4) + '</b> (' + (s.is_significant ? '<span style="color:#4ade80">Significant</span>' : '<span style="color:#94a3b8">Null</span>') + ')';
        } else if (ttype === 'optimizer') {
          sumEl.innerHTML = 'Best Fitness: <b style="color:#fbbf24">' + (s.best_fitness||0).toFixed(3) + '</b> &bull; Evaluated: <b>' + (s.trials||0) + ' Trials</b> &bull; Objective: <b>' + (s.objective||'sharpe') + '</b>';
        }
      }

      bar.style.display = 'flex';
      try {
        const newUrl = window.location.pathname + '?test_id=' + encodeURIComponent(testObj.test_id);
        window.history.replaceState({ test_id: testObj.test_id }, '', newUrl);
      } catch (e) {}
    }

    function copyActivePermlink() {
      const input = document.getElementById('permlink-input');
      if (!input || !input.value) return;
      input.select();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(() => {
          showToast('✓ Test permlink copied to clipboard!');
        }).catch(() => {
          document.execCommand('copy');
          showToast('✓ Link copied!');
        });
      } else {
        document.execCommand('copy');
        showToast('✓ Link copied!');
      }
    }

    function openActivePermlinkInNewTab() {
      const input = document.getElementById('permlink-input');
      if (!input || !input.value) return;
      window.open(input.value, '_blank');
    }

    async function loadResearchTestById(testId) {
      if (!testId) return;
      setResearchStatus('running', '🔗 Fetching saved test ' + testId + '...');

      try {
        const res = await fetch('/api/research/test?id=' + encodeURIComponent(testId));
        const json = await res.json();
        if (json.error) throw new Error(json.error);
        const test = json.test;
        if (!test || !test.data) throw new Error('Malformed test run payload');

        const d = test.data;
        const ttype = (test.test_type || 'backtest').toLowerCase();

        if (ttype === 'backtest') {
          window.latestResearchBacktestData = d;
          renderBacktestKPIs(d);
          renderJesseReport(d);
          renderSimulatedTrades(d.trades || []);
          initMasterChart(d);
          drawEquityChart(d.equity_points || d.equity_curve, d.buy_hold_curve);
          drawDrawdownChart(d.drawdown_series || d.drawdown_curve);
          drawScenarioChart(d.scenario_counts);
          setResearchStatus('done', '✓ Loaded Backtest: ' + test.title);
        } else if (ttype === 'monte_carlo') {
          window.latestResearchMCData = d;
          renderMCKPIs(d);
          if (d.monte_carlo && d.monte_carlo.fan_chart) {
            drawFanChart(d.monte_carlo.fan_chart);
          }
          setResearchStatus('done', '✓ Loaded Monte Carlo: ' + test.title);
        } else if (ttype === 'optimizer') {
          renderOptimizerResults(d);
          setResearchStatus('done', '✓ Loaded Optimizer: ' + test.title);
        }

        showActiveTestPermlink(test);
        showToast('✓ Loaded ' + test.title);

        const bar = document.getElementById('test-permlink-bar');
        if (bar) bar.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } catch (err) {
        setResearchStatus('error', '✗ Could not load test: ' + err.message);
        showToast('✗ Error: ' + err.message);
      }
    }

    async function updateSavedTestsCount() {
      try {
        const res = await fetch('/api/research/tests?limit=100');
        const json = await res.json();
        if (json.tests) {
          const el = document.getElementById('saved-tests-count');
          if (el) el.innerText = json.tests.length;
        }
      } catch (e) {}
    }

    async function openSavedTestsModal() {
      const modal = document.getElementById('modal-saved-tests');
      const listEl = document.getElementById('saved-tests-list');
      if (!modal || !listEl) return;

      modal.style.display = 'flex';
      listEl.innerHTML = '<div style="text-align:center; padding:30px; color:#64748b;">Loading saved tests...</div>';

      try {
        const res = await fetch('/api/research/tests?limit=50');
        const json = await res.json();
        const tests = json.tests || [];

        if (tests.length === 0) {
          listEl.innerHTML = '<div style="text-align:center; padding:40px; color:#64748b;">No saved research tests yet.<br><span style="font-size:11px;">Run a backtest, Monte Carlo, or optimizer to automatically create permalinks.</span></div>';
          return;
        }

        let html = '';
        tests.forEach(t => {
          const ttype = (t.test_type || 'test').toLowerCase();
          const badgeColor = ttype === 'backtest' ? '#38bdf8' : (ttype === 'monte_carlo' ? '#c084fc' : '#fbbf24');
          const fullUrl = window.location.origin + (t.test_url || ('/dashboard?test_id=' + t.test_id));

          let metricsSnippet = '';
          if (t.summary) {
            const s = t.summary;
            if (ttype === 'backtest') {
              metricsSnippet = 'Profit: ' + ((s.net_profit||0)>=0?'+':'') + '$' + (s.net_profit||0).toFixed(2) + ' &bull; WR: ' + (s.win_rate||0).toFixed(1) + '% &bull; Max DD: ' + (s.max_drawdown_pct||0).toFixed(1) + '% &bull; Trades: ' + (s.total_trades||0);
            } else if (ttype === 'monte_carlo') {
              metricsSnippet = 'Median: $' + (s.median_profit||0).toFixed(2) + ' &bull; Prob Profit: ' + (s.prob_profit||0).toFixed(1) + '% &bull; p-val: ' + (s.p_value||0).toFixed(4);
            } else if (ttype === 'optimizer') {
              metricsSnippet = 'Fitness: ' + (s.best_fitness||0).toFixed(3) + ' &bull; Trials: ' + (s.trials||0);
            }
          }

          html += `
            <div class="saved-test-item">
              <div class="saved-test-info">
                <div class="saved-test-header">
                  <span class="permlink-badge" style="color:${badgeColor}; border-color:${badgeColor}66;">${ttype.toUpperCase().replace('_', ' ')}</span>
                  <span class="saved-test-title">${t.title || t.symbol}</span>
                  <span class="saved-test-meta">${t.created_at || ''}</span>
                </div>
                <div class="saved-test-metrics">${metricsSnippet}</div>
              </div>
              <div class="saved-test-btns">
                <button class="btn-load-test" onclick="closeSavedTestsModal(); loadResearchTestById('${t.test_id}');">⚡ Load</button>
                <button class="btn-permlink-share" onclick="navigator.clipboard.writeText('${fullUrl}'); showToast('✓ Copied: ${t.test_id}');">📋 Copy</button>
                <button class="btn-delete-test" onclick="deleteSavedTest('${t.test_id}');" title="Delete Test">&times;</button>
              </div>
            </div>
          `;
        });
        listEl.innerHTML = html;
      } catch (err) {
        listEl.innerHTML = '<div style="text-align:center; padding:20px; color:#f87171;">Failed to load tests: ' + err.message + '</div>';
      }
    }

    function closeSavedTestsModal() {
      const modal = document.getElementById('modal-saved-tests');
      if (modal) modal.style.display = 'none';
    }

    async function deleteSavedTest(testId) {
      if (!confirm('Are you sure you want to delete test run ' + testId + '?')) return;
      try {
        const res = await fetch('/api/research/test/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: testId })
        });
        const json = await res.json();
        if (json.deleted) {
          showToast('✓ Deleted test ' + testId);
          openSavedTestsModal();
          updateSavedTestsCount();
        } else {
          showToast('Could not delete test');
        }
      } catch (e) {
        showToast('Delete error: ' + e.message);
      }
    }

    // Auto-restore view preference & check for ?test_id=... deep linking
    window.addEventListener('DOMContentLoaded', () => {
      updateSavedTestsCount();
      const params = new URLSearchParams(window.location.search);
      const testId = params.get('test_id') || params.get('test');
      if (testId) {
        switchMainView('research');
        loadResearchTestById(testId);
      } else {
        const savedMainView = localStorage.getItem('active_main_view');
        if (savedMainView === 'research') {
          switchMainView('research');
        }
      }
    });
    """

