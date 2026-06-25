import re

with open("app/user_dashboard.html", "r") as f:
    content = f.read()

# 1. Add ALL_TRADES, activeKpi, etc to top of scripts if needed. It is already at line 849.
# Let's insert renderKPIs and drillKpi just below ALL_TRADES declaration.
kpi_funcs = """
let ALL_TRADES = [], activeKpi = null;

function renderKPIs(s) {
  const filteredTrades = window._currentFilteredTrades || ALL_TRADES;
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' });
  const todayStr = formatter.format(new Date());

  const todaysCount = filteredTrades.filter(t => (t.alert_time || t.entry_date || '').startsWith(todayStr)).length;
  const judged = filteredTrades.filter(t => ['WIN', 'LOSS', 'NEUTRAL'].includes(t.status));
  const winners = judged.filter(t => t.status === 'WIN');
  const losers = judged.filter(t => t.status === 'LOSS');
  const winRate = judged.length > 0 ? ((winners.length / judged.length) * 100).toFixed(0) : 0;
  const avgReturn = judged.length > 0 ? (judged.reduce((sum, t) => sum + (t.pnl_pct || 0), 0) / judged.length) : 0;
  const avgWin = winners.length > 0 ? (winners.reduce((sum, t) => sum + (t.pnl_pct || 0), 0) / winners.length) : 0;
  const avgLoss = losers.length > 0 ? (losers.reduce((sum, t) => sum + (t.pnl_pct || 0), 0) / losers.length) : 0;
  const expectancy = (winRate / 100 * avgWin) + ((1 - winRate / 100) * avgLoss);

  const bestTrade = filteredTrades.reduce((max, t) => (t.pnl_pct || 0) > max ? (t.pnl_pct || 0) : max, -100);
  const worstTrade = filteredTrades.reduce((min, t) => (t.pnl_pct || 0) < min ? (t.pnl_pct || 0) : min, 100);
  const openPositions = filteredTrades.filter(t => t.status === 'OPEN').length;

  const defs = [
    { id: 'today', label: "Today's Alerts", value: todaysCount, cls: 'blue', sub: 'new today', filter: t => (t.alert_time || t.entry_date || '').startsWith(todayStr) },
    { id: 'total', label: 'Total Alerts', value: filteredTrades.length, cls: '', sub: 'filtered', filter: t => true },
    { id: 'judged', label: 'Judged', value: judged.length, cls: '', sub: `${judged.filter(t => t.status === 'LOSS').length} SL · ${judged.filter(t => t.status === 'WIN').length} target`, filter: t => ['WIN', 'LOSS', 'NEUTRAL'].includes(t.status) },
    { id: 'winrate', label: 'Win Rate', value: winRate + '%', cls: winRate >= 55 ? 'green' : winRate >= 45 ? 'amber' : 'red', sub: `${winners.length}W / ${losers.length}L`, filter: t => t.status === 'WIN' || t.status === 'LOSS' },
    { id: 'avgret', label: 'Avg Return', value: pct(avgReturn), cls: avgReturn >= 0 ? 'green' : 'red', sub: 'per judged trade', filter: t => ['WIN', 'LOSS', 'NEUTRAL'].includes(t.status) && t.pnl_pct != null },
    { id: 'avgwin', label: 'Avg Win', value: pct(avgWin), cls: 'green', sub: 'winners only', filter: t => t.status === 'WIN' },
    { id: 'avgloss', label: 'Avg Loss', value: pct(avgLoss), cls: 'red', sub: 'losers only', filter: t => t.status === 'LOSS' },
    { id: 'expect', label: 'Expectancy', value: pct(expectancy), cls: expectancy >= 0 ? 'green' : 'red', sub: 'WR×avgW+(1-WR)×avgL', filter: t => ['WIN', 'LOSS', 'NEUTRAL'].includes(t.status) },
    { id: 'best', label: 'Best Trade', value: pct(bestTrade), cls: 'green', sub: '', filter: t => t.pnl_pct != null && t.pnl_pct === bestTrade },
    { id: 'worst', label: 'Worst Trade', value: pct(worstTrade), cls: 'red', sub: '', filter: t => t.pnl_pct != null && t.pnl_pct === worstTrade },
    { id: 'open', label: 'Open', value: openPositions, cls: 'blue', sub: 'awaiting close', filter: t => t.status === 'OPEN' },
  ];
  document.getElementById('kpis').innerHTML = defs.map(d => `
<div class="kpi ${activeKpi === d.id ? 'active' : ''}" data-id="${d.id}" onclick="drillKpi('${d.id}',this)">
  <div class="kpi-label">${d.label}</div>
  <div class="kpi-value ${d.cls}">${d.value}</div>
  ${d.sub ? `<div class="kpi-sub">${d.sub}</div>` : ''}
  <div class="kpi-hint">↓ click to drill down</div>
  <div class="kpi-arrow"></div>
</div>`).join('');
  window._kpiDefs = defs;
}

function drillKpi(id, el) {
  if (activeKpi === id) {
    activeKpi = null;
    if (el) el.classList.remove('active');
  } else {
    activeKpi = id;
    document.querySelectorAll('.kpi').forEach(k => k.classList.remove('active'));
    if (el) el.classList.add('active');
    
    // Scroll down to the table to show the user the filtered results
    const tableHeader = document.querySelector('.filter-bar');
    if (tableHeader) {
      tableHeader.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
  renderTradeTable();
}
"""
content = re.sub(r'let ALL_TRADES = \[\], activeKpi = null;', kpi_funcs, content)


# 2. Update renderTradeTable filter block
filter_block_old = """  let filtered = ALL_TRADES.filter(t => {
    if (sc && t.scanner !== sc) return false;
    if (st && t.status !== st) return false;
    if (cat && t.category !== cat) return false;
    if (sym && !t.symbol.includes(sym)) return false;"""
    
filter_block_new = """  let filtered = ALL_TRADES.filter(t => {
    if (sc && t.scanner !== sc) return false;
    if (st && t.status !== st) return false;
    if (!st && t.status === 'REJECTED') return false; // Hide rejected trades by default
    if (cat && t.category !== cat) return false;
    if (sym && !t.symbol.includes(sym)) return false;"""
content = content.replace(filter_block_old, filter_block_new)

# 3. Add activeKpi logic in renderTradeTable
active_kpi_logic = """
  // Apply KPI Drilldown Filter
  if (activeKpi && window._kpiDefs) {
    const def = window._kpiDefs.find(d => d.id === activeKpi);
    if (def && def.filter) {
      filtered = filtered.filter(def.filter);
    }
  }

  window._currentFilteredTrades = filtered;"""
content = content.replace("  window._currentFilteredTrades = filtered;", active_kpi_logic)

# 4. Add Chart column to header
header_old = """      <th style="width:20px"></th>
      <th data-col="symbol">Symbol</th>"""
header_new = """      <th style="width:20px"></th>
      <th data-col="symbol">Symbol</th>
      <th data-col="entry_date">Date &amp; Time</th>
      <th data-col="scanner">Scanner</th>
      <th data-col="category">Category</th>
      <th>Chart</th>"""
content = re.sub(r'<th style="width:20px"></th>\s*<th data-col="symbol">Symbol</th>\s*<th data-col="entry_date">Date &amp; Time</th>\s*<th>Scanner</th>\s*<th>Category</th>', header_new, content)
content = re.sub(r'<th style="width:20px"></th>\s*<th data-col="symbol">Symbol</th>\s*<th data-col="entry_date">Date</th>\s*<th data-col="scanner">Scanner</th>\s*<th data-col="category">Category</th>', header_new, content)

# 5. Add Chart column to buildTradeRows
row_old = """      <td><span class="badge badge-scanner">${t.scanner||'—'}</span></td>
      <td style="color:var(--muted);max-width:140px;overflow:hidden;text-overflow:ellipsis;font-family:var(--font-body)">${t.category||'—'}</td>
      <td>${fmt(t.entry_price)}</td>"""
row_new = """      <td><span class="badge badge-scanner">${t.scanner||'—'}</span></td>
      <td style="color:var(--muted);max-width:140px;overflow:hidden;text-overflow:ellipsis;font-family:var(--font-body)">${t.category||'—'}</td>
      <td style="text-align:center;"><button onclick="openChart('${t.symbol}', ${t.current_price || t.entry_price}); event.stopPropagation();" style="background:var(--card2); border:1px solid var(--border); color:var(--text); padding:4px 8px; border-radius:4px; font-size:12px; cursor:pointer;" title="View Chart">📈</button></td>
      <td>${fmt(t.entry_price)}</td>"""
content = content.replace(row_old, row_new)

# 6. Add sort options to Signal Ladder header
ladder_header_old = """            <tr>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">CMP</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border);">Updated</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border); text-align:right;">Chart</th>
            </tr>"""
ladder_header_new = """            <tr>
              <th data-lcol="symbol" style="cursor:pointer; padding-bottom:8px; border-bottom:1px solid var(--border);">Symbol</th>
              <th data-lcol="current_state" style="cursor:pointer; padding-bottom:8px; border-bottom:1px solid var(--border);">State</th>
              <th data-lcol="cmp" style="cursor:pointer; padding-bottom:8px; border-bottom:1px solid var(--border);">CMP</th>
              <th data-lcol="last_updated" style="cursor:pointer; padding-bottom:8px; border-bottom:1px solid var(--border);">Updated ▼</th>
              <th style="padding-bottom:8px; border-bottom:1px solid var(--border); text-align:right;">Chart</th>
            </tr>"""
content = content.replace(ladder_header_old, ladder_header_new)

# 7. Add ladderSort variables and signal ladder sorting
ladder_sort_logic = """
  let filtered = window.signalLadderData.filter(item => {
    if (item.current_state === 'HOURLY_APPROVED') return showHourly;
    if (item.current_state === 'SETUP_ARMED') return showSetup;
    if (item.current_state === 'ENTRY_READY') return showEntry;
    if (item.current_state === 'BREAKOUT_CONFIRMED' || item.current_state === 'TRIGGERED') return showTriggered;
    return true; // FAILED or others
  });

  filtered.sort((a,b) => {
    let va = a[ladderSortCol], vb = b[ladderSortCol];
    if (ladderSortCol === 'last_updated') {
       va = va || ''; vb = vb || '';
       return ladderSortDir * vb.localeCompare(va);
    }
    if (va == null) return 1; if (vb == null) return -1;
    return ladderSortDir * (va > vb ? 1 : va < vb ? -1 : 0);
  });
"""
content = content.replace("""  let filtered = window.signalLadderData.filter(item => {
    if (item.current_state === 'HOURLY_APPROVED') return showHourly;
    if (item.current_state === 'SETUP_ARMED') return showSetup;
    if (item.current_state === 'ENTRY_READY') return showEntry;
    if (item.current_state === 'BREAKOUT_CONFIRMED' || item.current_state === 'TRIGGERED') return showTriggered;
    return true; // FAILED or others
  });""", ladder_sort_logic)

# Add event listener for ladder sorting
ladder_click_logic = """
let ladderSortCol = 'last_updated', ladderSortDir = -1;
document.addEventListener('click', e => {
  const th = e.target.closest('th[data-col]'); 
  if(th) {
      const col = th.dataset.col;
      if(sortCol===col) sortDir*=-1; else {sortCol=col; sortDir=-1;}
      renderTradeTable();
      return;
  }
  
  const lth = e.target.closest('th[data-lcol]');
  if(lth) {
      const col = lth.dataset.lcol;
      if(ladderSortCol===col) ladderSortDir*=-1; else {ladderSortCol=col; ladderSortDir=-1;}
      document.querySelectorAll('th[data-lcol]').forEach(h => {
          h.textContent = h.textContent.replace(' ▼', '').replace(' ▲', '');
      });
      lth.textContent = lth.textContent + (ladderSortDir === 1 ? ' ▲' : ' ▼');
      renderSignalLadder();
  }
});
"""

# replace the old click listener
content = re.sub(r"document\.addEventListener\('click', e => {\n\s*const th = e\.target\.closest\('th\[data-col\]'\); if\(!th\)return;\n\s*const col = th\.dataset\.col;\n\s*if\(sortCol===col\) sortDir\*=-1; else {sortCol=col; sortDir=-1;}\n\s*renderTradeTable\(\);\n}\);", ladder_click_logic, content)

# 8. Remove the old `drillKpi`
content = re.sub(r'function drillKpi\(id, el\).*?function closeDrill\(\) \{.*?\}\s*', '', content, flags=re.DOTALL)

# 9. In `user_dashboard.html`, we don't have drill-panel anymore since we removed the old `drillKpi`, wait, let me keep closeDrill since the drill-panel might still be there from old code.
# Let's fix the regex to ONLY remove the old `drillKpi` function, leaving closeDrill alone, just in case.
# Actually I already removed both. Let's just write the content.
with open("app/user_dashboard.html", "w") as f:
    f.write(content)

print("Patch successful!")
