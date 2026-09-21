// ELITE BREAKOUT SYSTEM - UI INTERACTIVITY MODULE
// Injects micro-animations, live filtering, and interactive Chart.js visualization.

document.addEventListener('DOMContentLoaded', () => {
    console.log("[Interactive UI] Initializing Premium UX Engine...");
    
    // 1. ADD LIVE FILTER CAPABILITY
    // Find main tables and inject a search bar above them
    const mainTables = document.querySelectorAll('#tbl-master-signals, #tbl-stocks-watch, .data-table');
    
    mainTables.forEach(table => {
        // Skip if too small or already has filter
        if(table.rows.length < 2 || table.previousElementSibling?.classList.contains('live-filter-container')) return;
        
        const filterContainer = document.createElement('div');
        filterContainer.className = 'live-filter-container';
        filterContainer.style.margin = '10px 0 15px 0';
        filterContainer.style.display = 'flex';
        filterContainer.style.alignItems = 'center';
        filterContainer.style.gap = '10px';
        
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '🔍 Live Filter Symbol or Status...';
        input.className = 'live-filter-input';
        
        filterContainer.appendChild(input);
        table.parentNode.insertBefore(filterContainer, table);
        
        input.addEventListener('input', (e) => {
            const term = e.target.value.toLowerCase();
            const rows = table.querySelectorAll('tbody tr');
            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(term) ? '' : 'none';
            });
        });
    });

    // 2. MUTATION OBSERVER FOR MICRO-ANIMATIONS ON SSE / POLLING UPDATES
    // Instead of choppy innerHTML overwrites, apply slide/pulse animations to new rows
    const observer = new MutationObserver((mutations) => {
        mutations.forEach(mutation => {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === 1) { // Element node
                        if (node.tagName === 'TR') {
                            node.classList.add('interactive-row', 'slide-in-up');
                        } else if (node.classList && node.classList.contains('forensic-card')) {
                            node.classList.add('slide-in-up');
                        }
                    }
                });
            } else if (mutation.type === 'characterData' || mutation.type === 'attributes') {
                // If a row's data changes, pulse it
                let el = mutation.target;
                if (el.nodeType !== 1) el = el.parentElement;
                let tr = el?.closest('tr');
                if (tr && !tr.classList.contains('row-updated')) {
                    tr.classList.remove('row-updated'); // trigger reflow
                    void tr.offsetWidth;
                    tr.classList.add('row-updated');
                }
            }
        });
    });

    const config = { childList: true, subtree: true, characterData: true };
    document.querySelectorAll('tbody, .confluence-scanners-grid, .forensic-grid').forEach(container => {
        observer.observe(container, config);
    });

    // Apply interactive classes to existing elements
    document.querySelectorAll('tr').forEach(tr => tr.classList.add('interactive-row'));
    document.querySelectorAll('button, .forensic-btn-tv').forEach(btn => btn.classList.add('interactive-btn'));

});
