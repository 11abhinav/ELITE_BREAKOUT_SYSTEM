// table_sorter.js - Generic Table Column Sorting
// This script automatically makes all tables sortable across the dashboards
// and preserves the active sort even when JS frameworks overwrite the tbody innerHTML.

const TableSorter = {
  currentSorts: new Map(), // tableId -> { index: number, asc: boolean }
  observers: new Map(), // tableId -> MutationObserver
  
  init() {
    document.querySelectorAll('table').forEach(table => {
      // Must have an ID to track sort state across re-renders
      if (!table.id) return;
      
      // Prevent double initialization
      if (table.dataset.sortableInit) return;
      table.dataset.sortableInit = "true";

      const thead = table.querySelector('thead');
      if (!thead) return;

      const headers = Array.from(thead.querySelectorAll('th'));
      
      headers.forEach((th, index) => {
        // Skip columns that explicitly shouldn't be sorted (e.g. action buttons)
        if (th.classList.contains('no-sort') || th.innerText.trim().toLowerCase() === 'act') return;

        th.style.cursor = 'pointer';
        th.title = 'Click to sort';
        th.style.userSelect = 'none';

        // Add the generic sort icon if not already present
        if (!th.innerHTML.includes('↕') && !th.innerHTML.includes('↑') && !th.innerHTML.includes('↓')) {
           th.innerHTML = th.innerHTML + ' <span class="sort-icon" style="opacity:0.3;font-size:10px">↕</span>';
        }
        
        th.addEventListener('click', () => {
          let asc = true;
          const currentSort = this.currentSorts.get(table.id);
          
          if (currentSort && currentSort.index === index) {
            asc = !currentSort.asc;
          }
          
          this.currentSorts.set(table.id, { index, asc });
          this.updateHeaders(table);
          this.sortRows(table);
        });
      });
      
      // Hook into DOM updates to reapply sort when data refreshes via setInterval
      const tbody = table.querySelector('tbody');
      if (tbody) {
        const observer = new MutationObserver(() => {
          // If we have an active sort for this table, we need to re-apply it after the innerHTML changes
          if (this.currentSorts.has(table.id)) {
            // Disconnect temporarily so we don't trigger an infinite loop when we rearrange rows
            observer.disconnect();
            this.sortRows(table);
            observer.observe(tbody, { childList: true });
          }
        });
        observer.observe(tbody, { childList: true });
        this.observers.set(table.id, observer);
      }
    });
  },
  
  updateHeaders(table) {
    const currentSort = this.currentSorts.get(table.id);
    const headers = Array.from(table.querySelectorAll('thead th'));
    
    headers.forEach((th, i) => {
      // Find the existing sort icon span
      const iconSpan = th.querySelector('.sort-icon') || Array.from(th.querySelectorAll('span')).find(s => s.innerText === '↕' || s.innerText === '↑' || s.innerText === '↓');
      
      if (!iconSpan) return;

      if (currentSort && currentSort.index === i) {
        iconSpan.innerText = currentSort.asc ? '↑' : '↓';
        iconSpan.style.opacity = '1';
        iconSpan.style.color = 'var(--accent, #10b981)';
      } else {
        iconSpan.innerText = '↕';
        iconSpan.style.opacity = '0.3';
        iconSpan.style.color = 'inherit';
      }
    });
  },
  
  sortRows(table) {
    const currentSort = this.currentSorts.get(table.id);
    if (!currentSort) return;
    
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    
    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length === 0) return;
    
    rows.sort((a, b) => {
      const aCol = a.children[currentSort.index];
      const bCol = b.children[currentSort.index];
      if (!aCol || !bCol) return 0;
      
      let aText = aCol.innerText.trim();
      let bText = bCol.innerText.trim();
      
      // Special Date Parsing (Handle "YYYY-MM-DD" and "DD-MM-YYYY")
      const dateRegex = /^(\d{2,4})[-/](\d{2})[-/](\d{2,4})/;
      if (dateRegex.test(aText) && dateRegex.test(bText)) {
          let da = new Date(aText);
          let db = new Date(bText);
          if (!isNaN(da) && !isNaN(db)) {
             return currentSort.asc ? da - db : db - da;
          }
      }

      // Cleanup numbers (remove currency symbols, commas, percent signs, up/down arrows)
      aText = aText.replace(/₹|,|%|↑|↓|\+/g, '').trim();
      bText = bText.replace(/₹|,|%|↑|↓|\+/g, '').trim();
      
      let aVal = parseFloat(aText);
      let bVal = parseFloat(bText);
      
      // If either is not a number, fallback to string comparison
      if (isNaN(aVal) || isNaN(bVal)) {
        return currentSort.asc ? aCol.innerText.trim().localeCompare(bCol.innerText.trim()) : bCol.innerText.trim().localeCompare(aCol.innerText.trim());
      } else {
        return currentSort.asc ? (aVal - bVal) : (bVal - aVal);
      }
    });
    
    // Re-append sorted rows
    rows.forEach(row => tbody.appendChild(row));
  }
};

// Initialize after DOM load
document.addEventListener('DOMContentLoaded', () => {
    // Initial wait to allow frameworks to inject the first render
    setTimeout(() => TableSorter.init(), 1000);
    // Periodically search for new tables (in case of dynamic tab loading)
    setInterval(() => TableSorter.init(), 3000);
});
