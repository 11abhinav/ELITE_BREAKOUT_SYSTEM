/**
 * =====================================================================================
 * app/static/shared_ui.js
 * SHARED FRONTEND EVENT BADGES RENDERER & ACCESSIBLE TOOLTIPS
 * =====================================================================================
 */

function renderEventBadges(eventBadges, maxDisplay = 2) {
  if (!eventBadges || !Array.isArray(eventBadges) || eventBadges.length === 0) {
    return '';
  }

  // 1. Sort badges by priority DESC
  const sorted = [...eventBadges].sort((a, b) => (b.priority || 0) - (a.priority || 0));

  const visible = sorted.slice(0, maxDisplay);
  const overflow = sorted.slice(maxDisplay);

  let html = '<span class="event-badge-container">';

  visible.forEach(badge => {
    const type = (badge.type || '').toLowerCase();
    const status = (badge.status || '').toUpperCase();
    const label = badge.label || 'E';
    const meta = badge.metadata || {};

    let themeClass = 'badge-action-default';
    if (type === 'earnings') {
      themeClass = status === 'UPCOMING' ? 'badge-earnings-upcoming' : 'badge-earnings-recent';
    }

    // Build rich multi-line tooltip string from semantic metadata
    let tooltip = '';
    if (type === 'earnings') {
      if (status === 'UPCOMING') {
        const dStr = meta.days === 0 ? 'Today' : `In ${meta.days} trading session${meta.days === 1 ? '' : 's'}`;
        tooltip = `Upcoming Earnings Result\nDate: ${meta.date || 'TBA'}\nTimeline: ${dStr}\nStatus: ${meta.date_status || 'ESTIMATED'}`;
      } else {
        const absDays = Math.abs(meta.days || 0);
        tooltip = `Recent Earnings Result\nDate: ${meta.date || 'TBA'}\nDeclared: ${absDays} trading session${absDays === 1 ? '' : 's'} ago\nStatus: ${meta.date_status || 'CONFIRMED'}`;
      }
    } else {
      tooltip = `${type.toUpperCase()} Event\nDate: ${meta.date || 'TBA'}`;
    }

    html += `<span class="event-badge-pill ${themeClass}" title="${escapeHtmlAttr(tooltip)}" tabindex="0" role="note" aria-label="${escapeHtmlAttr(tooltip)}">${escapeHtml(label)}</span>`;
  });

  if (overflow.length > 0) {
    const overflowText = overflow.map(b => `${(b.type || '').toUpperCase()}: ${b.label || ''}`).join('\n');
    html += `<span class="event-badge-pill badge-overflow" title="${escapeHtmlAttr('Additional Events:\n' + overflowText)}" tabindex="0" role="note">+${overflow.length}</span>`;
  }

  html += '</span>';
  return html;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeHtmlAttr(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
