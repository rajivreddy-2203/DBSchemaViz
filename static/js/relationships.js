(() => {
  const root = document.querySelector('.analysis-page');
  if (!root) return;
  const connectionId = root.dataset.connectionId;
  const endpoint = path => `/api/connections/${connectionId}/windchill${path}`;
  const content = document.getElementById('analysis-content');
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' })[char]);
  let activeTab = 'relationships'; let data = { relationships:[], classes:[], unresolved:[] }; let sort = { key:'windchill_class', ascending:true };

  const cell = value => `<td>${escapeHtml(value || '—')}</td>`;
  function render() {
    const query = document.getElementById('analysis-search').value.trim().toLowerCase();
    let columns, records, rows;
    if (activeTab === 'relationships') {
      columns = [['source_table','Source table'],['source_column','Source ID column'],['target_table','Target table'],['target_column','Target ID column'],['windchill_class','Windchill class'],['classname_column','Classname column']];
      records = data.relationships.filter(row => JSON.stringify(row).toLowerCase().includes(query));
    } else if (activeTab === 'classes') {
      columns = [['class_name','Windchill class'],['table','Mapped table']];
      records = data.classes.filter(row => JSON.stringify(row).toLowerCase().includes(query));
    } else {
      columns = [['source_table','Source table'],['classname_column','Classname column'],['ida_column','IDA column'],['windchill_class','Class value'],['reason','Reason']];
      records = data.unresolved.filter(row => JSON.stringify(row).toLowerCase().includes(query));
    }
    if (!columns.some(([key]) => key === sort.key)) sort = { key:columns[0][0], ascending:true };
    records.sort((left, right) => String(left[sort.key] || '').localeCompare(String(right[sort.key] || ''), undefined, { sensitivity:'base' }) * (sort.ascending ? 1 : -1));
    if (activeTab === 'relationships') rows = records.map(row => `<tr>${cell(row.source_table)}${cell(row.source_column)}${cell(row.target_table)}${cell(row.target_column)}${cell(row.windchill_class)}${cell(row.classname_column)}</tr>`);
    if (activeTab === 'classes') rows = records.map(row => `<tr>${cell(row.class_name)}${cell(row.table)}</tr>`);
    if (activeTab === 'unresolved') rows = records.map(row => `<tr>${cell(row.source_table)}${cell(row.classname_column)}${cell(row.ida_column)}${cell(row.windchill_class)}<td class="muted-cell">${escapeHtml(row.reason)}</td></tr>`);
    content.innerHTML = `<table class="data-table"><thead><tr>${columns.map(([key,label]) => `<th><button class="sort-header" data-sort="${key}">${label}${sort.key === key ? (sort.ascending ? ' ↑' : ' ↓') : ''}</button></th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${columns.length}" class="muted-cell">No matching records.</td></tr>`}</tbody></table>`;
    content.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => { sort = { key:button.dataset.sort, ascending:sort.key === button.dataset.sort ? !sort.ascending : true }; render(); }));
  }
  async function load() {
    try {
      const [relationships, analysis, unresolved] = await Promise.all([
        fetch(endpoint('/relationships')).then(response => response.json()),
        fetch(endpoint('/analysis')).then(response => response.json()),
        fetch(endpoint('/unresolved')).then(response => response.json()),
      ]);
      data.relationships = relationships.relationships || [];
      data.classes = analysis.class_mappings || [];
      data.unresolved = unresolved.relationships || [];
      render();
    } catch (_) { content.innerHTML = '<p class="loading">Unable to load analysis. Run a metadata extraction first.</p>'; }
  }
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelector('.tab.active').classList.remove('active'); button.classList.add('active'); activeTab = button.dataset.tab; render(); }));
  document.getElementById('analysis-search').addEventListener('input', render);
  load();
})();
