// table_sorter.js - Generic Table Column Sorting (High Performance / Zero Reflow)
// Optimized to eliminate forced synchronous reflows (textContent instead of innerText)
// and uses DocumentFragment for batch DOM insertion.

const TableSorter = {
  currentSorts: new Map(), // tableId -> { index: number, asc: boolean }
  observers: new Map(), // tableId -> MutationObserver
  
  init() {
    document.querySelectorAll('table').forEach(table => {
      if (!table.id) return;

      // Skip tables that manage their own data-driven sorting/filtering
      if (table.id === 'tbl-master-signals' || table.id === 'tradeTable' || table.id === 'table-signals' || table.classList.contains('no-auto-sort')) return;
      
      if (table.dataset.sortableInit) return;
      table.dataset.sortableInit = "true";

      const thead = table.querySelector('thead');
      if (!thead) return;

      const headers = Array.from(thead.querySelectorAll('th'));
      
      headers.forEach((th, index) => {
        if (th.classList.contains('no-sort') || th.textContent.trim().toLowerCase() === 'act') return;

        th.style.cursor = 'pointer';
        th.title = 'Click to sort';
        th.style.userSelect = 'none';

        if (!th.textContent.includes('↕') && !th.textContent.includes('↑') && !th.textContent.includes('↓')) {
           th.insertAdjacentHTML('beforeend', ' <span class="sort-icon" style="opacity:0.3;font-size:10px">↕</span>');
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
      
      const tbody = table.querySelector('tbody');
      if (tbody) {
        let timer = null;
        const observer = new MutationObserver(() => {
          if (this.currentSorts.has(table.id)) {
            clearTimeout(timer);
            timer = setTimeout(() => {
              observer.disconnect();
              this.sortRows(table);
              observer.observe(tbody, { childList: true });
            }, 60);
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
      const iconSpan = th.querySelector('.sort-icon');
      if (!iconSpan) return;

      if (currentSort && currentSort.index === i) {
        iconSpan.textContent = currentSort.asc ? '↑' : '↓';
        iconSpan.style.opacity = '1';
        iconSpan.style.color = 'var(--accent, #10b981)';
      } else {
        iconSpan.textContent = '↕';
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
    if (rows.length <= 1) return;
    
    // Pre-extract comparison values once per row to avoid O(N log N) DOM queries
    const rowValues = rows.map(row => {
      const col = row.children[currentSort.index];
      const text = col ? col.textContent.trim() : '';
      
      let numVal = NaN;
      let dateVal = NaN;
      
      // Date check
      if (text.length >= 6) {
        const daClean = text.replace(/IST|GMT|UTC/gi, '').trim();
        const dParsed = Date.parse(daClean);
        if (!isNaN(dParsed) && dParsed > 946684800000) {
          dateVal = dParsed;
        }
      }
      
      // Numeric check
      if (isNaN(dateVal)) {
        const cleaned = text.replace(/[₹,%↑↓+\s]/g, '');
        if (cleaned.length > 0) {
          const parsed = parseFloat(cleaned);
          if (!isNaN(parsed)) numVal = parsed;
        }
      }
      
      return { row, text, numVal, dateVal };
    });
    
    rowValues.sort((a, b) => {
      if (!isNaN(a.dateVal) && !isNaN(b.dateVal)) {
        return currentSort.asc ? (a.dateVal - b.dateVal) : (b.dateVal - a.dateVal);
      }
      if (!isNaN(a.numVal) && !isNaN(b.numVal)) {
        return currentSort.asc ? (a.numVal - b.numVal) : (b.numVal - a.numVal);
      }
      return currentSort.asc ? a.text.localeCompare(b.text) : b.text.localeCompare(a.text);
    });
    
    // Batch DOM insertion using DocumentFragment (Single paint)
    const frag = document.createDocumentFragment();
    rowValues.forEach(item => frag.appendChild(item.row));
    tbody.appendChild(frag);
  }
};

// Initialize after DOM load
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => TableSorter.init(), 600);
});

