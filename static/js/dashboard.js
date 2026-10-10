/* global cytoscape */
(() => {
  const root = document.querySelector('.dashboard');
  if (!root) return;
  const connectionId = root.dataset.connectionId;
  const isWindchill = root.dataset.windchill === 'true';
  const endpoint = (path) => `/api/connections/${connectionId}${path}`;
  const graphStatus = document.getElementById('graph-status');
  const detailPanel = document.getElementById('details-panel');
  let cy;
  let selectedNode;
  let graphLoaded = false;
  let latestRunStatus = '';
  let graphQuery = '';
  let graphTableId;
  let graphOffset = 0;
  const graphLimit = 1000;
  let pendingEdges = [];
  let pollTimer;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' })[char]);
  const setGraphStatus = (message, done = false) => { graphStatus.textContent = message; graphStatus.classList.toggle('done', done); };
  const getJson = async (url, options) => {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.status === 204 ? null : response.json();
  };

  function updatePageControls(page, stats) {
    const controls = document.getElementById('graph-pagination');
    if (!page || graphTableId) {
      controls.classList.add('hidden');
      return;
    }
    graphOffset = page.offset;
    controls.classList.remove('hidden');
    document.getElementById('page-label').textContent = `Page ${Math.floor(page.offset / page.limit) + 1} of ${Math.max(1, Math.ceil(stats.tables / page.limit))}`;
    document.getElementById('previous-page').disabled = page.offset === 0;
    document.getElementById('next-page').disabled = page.offset + page.returned >= stats.tables;
  }

  function createGraph(elements, page, stats) {
    if (!window.cytoscape) {
      setGraphStatus('The graph library did not load. Check your network connection and reload.');
      return;
    }
    if (cy) cy.destroy();
    pendingEdges = [];
    cy = cytoscape({
      container: document.getElementById('graph'), elements: { nodes: [], edges: [] },
      layout: { name: 'preset', fit: false, padding: 60 },
      wheelSensitivity: 0.18, minZoom: 0.08, maxZoom: 5,
      style: [
        { selector: 'node', style: { 'shape':'round-rectangle', 'width': 205, 'height':64, 'padding':'12px', 'background-color':'#152842', 'border-width':1.5, 'border-color':'#5480bc', 'label':'data(card)', 'color':'#e7effa', 'font-size':12, 'font-family':'Consolas, Menlo, monospace', 'text-wrap':'wrap', 'text-max-width':182, 'text-valign':'center', 'text-halign':'center', 'overlay-opacity':0 } },
        { selector: 'node:selected, node.highlighted', style: { 'border-width':3, 'border-color':'#55d8d1', 'background-color':'#1b3758' } },
        { selector: 'edge', style: { 'width':1.7, 'line-color':'#65a1ff', 'target-arrow-color':'#65a1ff', 'target-arrow-shape':'triangle', 'curve-style':'bezier', 'arrow-scale':.8, 'opacity':.78, 'label':'data(label)', 'source-label':'data(source_label)', 'target-label':'data(target_label)', 'font-size':9, 'font-family':'Consolas, Menlo, monospace', 'color':'#dce9fa', 'text-background-color':'#0d1728', 'text-background-opacity':.9, 'text-background-padding':'2px', 'source-text-margin-y':-12, 'target-text-margin-y':12 } },
        { selector: 'edge.windchill_classname', style: { 'line-color':'#b395ff', 'target-arrow-color':'#b395ff', 'line-style':'dashed', 'width':2.6, 'opacity':.9 } },
        { selector: 'edge.windchill_role_a, edge.windchill_role_b', style: { 'line-color':'#55d8d1', 'target-arrow-color':'#55d8d1', 'line-style':'dotted', 'width':2.6, 'opacity':.9 } },
        { selector: 'edge.windchill_master_reference', style: { 'line-color':'#ffb86b', 'target-arrow-color':'#ffb86b', 'width':2.8, 'opacity':.92 } },
        { selector: '.filtered-out', style: { 'display':'none' } },
        { selector: '.unfocused', style: { 'opacity':.15 } },
      ],
    });
    cy.on('tap', 'node', event => selectTable(event.target));
    cy.on('tap', 'edge', event => selectRelationship(event.target.data('relationship_id')));
    cy.on('dragfree', 'node', event => {
      const node = event.target; const p = node.position();
      fetch(endpoint(`/tables/${node.id()}/position`), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(p) }).catch(() => {});
    });
    cy.on('tap', event => { if (event.target === cy) clearSelection(); });
    appendGraph(elements);
    cy.fit(cy.elements(), 60);
    graphLoaded = true;
    const suffix = page && page.returned < page.limit ? '' : ' Use search to focus on a table.';
    setGraphStatus(`Loaded ${cy.nodes().length} tables and ${cy.edges().length} relationships.${suffix}`, true);
    updatePageControls(page, stats);
  }

  function appendGraph(elements) {
    if (!cy) return;
    const nodes = elements.nodes.filter(node => !cy.$id(node.data.id).length);
    cy.add(nodes);
    pendingEdges.push(...elements.edges);
    const nodeIds = new Set(cy.nodes().map(node => node.id()));
    const edgeIds = new Set(cy.edges().map(edge => edge.id()));
    const readyEdges = [];
    const waitingEdges = [];
    for (const edge of pendingEdges) {
      const data = edge.data;
      if (edgeIds.has(data.id)) continue;
      if (nodeIds.has(data.source) && nodeIds.has(data.target)) {
        readyEdges.push(edge);
        edgeIds.add(data.id);
      } else {
        waitingEdges.push(edge);
      }
    }
    pendingEdges = waitingEdges;
    if (readyEdges.length) cy.add(readyEdges);
  }

  async function loadGraph(query = '', tableId, offset = 0, append = false) {
    try {
      setGraphStatus(append ? `Loading tables ${offset.toLocaleString()} onward…` : 'Loading relationship graph…');
      graphQuery = query;
      graphTableId = tableId;
      const params = new URLSearchParams({ limit: String(graphLimit), offset: String(offset) });
      if (query) params.set('q', query);
      if (tableId) params.set('table_id', tableId);
      params.set('include_external', '1');
      const data = await getJson(`${endpoint('/graph')}?${params}`);
      document.getElementById('table-count').textContent = data.stats.tables.toLocaleString();
      document.getElementById('relationship-count').textContent = data.stats.relationships.toLocaleString();
      const unresolved = document.getElementById('unresolved-stat');
      unresolved.querySelector('b').textContent = data.stats.unresolved;
      unresolved.classList.toggle('hidden', !data.stats.unresolved);
      if (append) appendGraph(data.elements);
      else createGraph(data.elements, data.page, data.stats);
      applyFilters();
      const moreTables = !tableId && data.page.returned === data.page.limit && data.page.offset + data.page.returned < data.stats.tables;
      if (moreTables) {
        document.getElementById('graph-pagination').classList.add('hidden');
        setTimeout(() => loadGraph(query, undefined, offset + data.page.returned, true), 80);
      } else if (append) {
        setGraphStatus(`Loaded ${cy.nodes().length.toLocaleString()} tables and ${cy.edges().length.toLocaleString()} relationships.`, true);
      }
    } catch (error) { setGraphStatus(`Unable to load the graph: ${error.message}`); }
  }

  function edgeAllowed(edge) {
    const selectedTypes = [...document.querySelectorAll('.edge-filter:checked')].map(input => input.value);
    if (!selectedTypes.includes(edge.data('type'))) return false;
    if (!selectedNode) return true;
    const direction = document.querySelector('input[name="direction"]:checked').value;
    if (direction === 'outgoing') return edge.source().id() === selectedNode.id();
    if (direction === 'incoming') return edge.target().id() === selectedNode.id();
    return edge.source().id() === selectedNode.id() || edge.target().id() === selectedNode.id();
  }

  function applyFilters() {
    if (!cy) return;
    cy.edges().forEach(edge => edge.toggleClass('filtered-out', !edgeAllowed(edge)));
    cy.elements().removeClass('unfocused highlighted');
    if (selectedNode) {
      const visibleEdges = selectedNode.connectedEdges().filter(edge => !edge.hasClass('filtered-out'));
      const focus = selectedNode.union(visibleEdges).union(visibleEdges.connectedNodes());
      cy.elements().difference(focus).addClass('unfocused');
      focus.nodes().addClass('highlighted');
    }
  }

  function clearSelection() {
    selectedNode = undefined;
    if (cy) { cy.elements().removeClass('unfocused highlighted'); cy.nodes().unselect(); }
    detailPanel.innerHTML = '<div class="detail-empty"><span>◎</span><h2>Select a table or relationship</h2><p>Click a card to inspect columns and incoming/outgoing links.</p></div>';
    applyFilters();
  }

  async function selectTable(node) {
    selectedNode = node; cy.nodes().unselect(); node.select(); applyFilters();
    try {
      const table = await getJson(endpoint(`/tables/${node.id()}`));
      renderTableDetail(table);
    } catch (error) { detailPanel.innerHTML = `<p class="loading">Could not load table details: ${escapeHtml(error.message)}</p>`; }
  }

  function renderTableDetail(table) {
    const columns = table.columns.map(column => `<tr><td class="${column.primary_key ? 'pk-column' : ''}">${column.primary_key ? '🔑 ' : ''}${escapeHtml(column.name)}</td><td>${escapeHtml(column.data_type)}</td><td>${column.nullable ? 'yes' : 'no'}</td></tr>`).join('');
    const relationships = table.relationships.length ? table.relationships.map(relation => `<li><button data-relationship-id="${relation.id}">${relation.direction === 'outgoing' ? '→' : '←'} ${escapeHtml(relation.other_table)}</button><span class="relationship-type">${escapeHtml(relation.type)} · ${escapeHtml(relation.source_column || '')} → ${escapeHtml(relation.target_column || '')}</span></li>`).join('') : '<li class="muted">No cached relationships.</li>';
    detailPanel.innerHTML = `<h2 class="detail-title">${escapeHtml(table.name)}</h2><p class="detail-schema">${escapeHtml(table.schema)} · ${table.columns.length} columns</p><section class="detail-section"><h3>Columns</h3><table class="columns-table"><thead><tr><th>Column</th><th>Type</th><th>Null</th></tr></thead><tbody>${columns}</tbody></table></section><section class="detail-section"><h3>Incoming & outgoing (${table.relationships.length})</h3><ul class="relationship-list">${relationships}</ul></section>`;
    detailPanel.querySelectorAll('[data-relationship-id]').forEach(button => button.addEventListener('click', () => selectRelationship(button.dataset.relationshipId)));
  }

  async function selectRelationship(relationshipId) {
    try {
      const relation = await getJson(endpoint(`/relationships/${relationshipId}`));
      const fields = [
        ['Type', relation.type], ['Source', `${relation.source.table}.${relation.source.column || ''}`], ['Target', `${relation.target.table}.${relation.target.column || ''}`],
        ['Constraint', relation.constraint_name], ['Windchill class', relation.windchill_class], ['Classname column', relation.classname_column], ['IDA column', relation.ida_column], ['Discovery method', relation.discovery_method], ['Confidence', relation.confidence != null ? `${relation.confidence}%` : null],
      ].filter(([, value]) => value);
      detailPanel.innerHTML = `<h2 class="detail-title">Relationship details</h2><p class="detail-schema">${escapeHtml(relation.type)}</p><section class="detail-section">${fields.map(([label,value]) => `<p class="detail-value"><b>${escapeHtml(label)}</b>${escapeHtml(value)}</p>`).join('')}</section>`;
      if (cy) {
        const edge = cy.$(`#r-${relationshipId}`); const source = edge.source();
        if (source.length) { selectedNode = source; source.select(); applyFilters(); }
      }
    } catch (error) { detailPanel.innerHTML = `<p class="loading">Could not load relationship details: ${escapeHtml(error.message)}</p>`; }
  }

  function focusSearchResult(result) {
    if (!cy) return;
    const node = cy.$id(String(result.table_id));
    if (!node.length) {
      loadGraph('', result.table_id).then(() => {
        const focusedNode = cy && cy.$id(String(result.table_id));
        if (focusedNode && focusedNode.length) focusSearchResult(result);
      });
      return;
    }
    cy.animate({ center:{ eles:node }, zoom:1.15 }, { duration:280 });
    selectTable(node);
    document.getElementById('search-results').innerHTML = '';
  }

  let searchTimer;
  document.getElementById('search-input').addEventListener('input', event => {
    clearTimeout(searchTimer); const query = event.target.value.trim();
    const container = document.getElementById('search-results');
    if (!query) { container.innerHTML = ''; return; }
    searchTimer = setTimeout(async () => {
      try {
        const data = await getJson(`${endpoint('/search')}?q=${encodeURIComponent(query)}`);
        container.innerHTML = data.results.map(result => `<div class="search-result" data-table-id="${result.table_id}">${escapeHtml(result.label)}<small>${escapeHtml(result.kind)}</small></div>`).join('') || '<div class="search-result">No matching metadata</div>';
        container.querySelectorAll('[data-table-id]').forEach((element, index) => element.addEventListener('click', () => focusSearchResult(data.results[index])));
      } catch (_) { container.innerHTML = ''; }
    }, 220);
  });

  document.querySelectorAll('.edge-filter, input[name="direction"]').forEach(input => input.addEventListener('change', applyFilters));
  document.getElementById('previous-page').addEventListener('click', () => loadGraph(graphQuery, undefined, Math.max(0, graphOffset - graphLimit)));
  document.getElementById('next-page').addEventListener('click', () => loadGraph(graphQuery, undefined, graphOffset + graphLimit));
  document.getElementById('fit-button').addEventListener('click', () => cy && cy.fit(cy.elements().not('.filtered-out'), 55));
  document.getElementById('zoom-in').addEventListener('click', () => cy && cy.zoom({ level: cy.zoom() * 1.2, renderedPosition:{x:cy.width()/2,y:cy.height()/2} }));
  document.getElementById('zoom-out').addEventListener('click', () => cy && cy.zoom({ level: cy.zoom() / 1.2, renderedPosition:{x:cy.width()/2,y:cy.height()/2} }));
  document.getElementById('refresh-button').addEventListener('click', async () => {
    try { const run = await getJson(endpoint('/refresh'), {method:'POST'}); setGraphStatus(`${run.phase}. Background extraction is running.`); pollRun(true); } catch (error) { setGraphStatus(`Could not start refresh: ${error.message}`); }
  });
  document.getElementById('rebuild-button').addEventListener('click', async () => {
    try { const run = await getJson(endpoint('/refresh'), {method:'POST'}); setGraphStatus(`${run.phase}. Rebuilding cached relationships.`); pollRun(true); } catch (error) { setGraphStatus(`Could not rebuild relationships: ${error.message}`); }
  });
  document.getElementById('reset-layout').addEventListener('click', async () => {
    if (!confirm('Reset all saved table positions for this connection?')) return;
    try { await getJson(endpoint('/positions'), {method:'DELETE'}); selectedNode = undefined; await loadGraph(); } catch (error) { setGraphStatus(`Could not reset layout: ${error.message}`); }
  });

  async function showUnresolved() {
    if (!document.getElementById('unresolved-filter').checked) return;
    try {
      const data = await getJson(endpoint('/windchill/unresolved'));
      const rows = data.relationships.length ? data.relationships.map(row => `<li><b>${escapeHtml(row.source_table || 'Unknown source')}</b><span class="relationship-type">${escapeHtml(row.classname_column || '')} → ${escapeHtml(row.windchill_class || 'unknown class')}</span><span class="relationship-type">${escapeHtml(row.reason)}</span></li>`).join('') : '<li class="muted">No unresolved Windchill mappings.</li>';
      detailPanel.innerHTML = `<h2 class="detail-title">Unresolved mappings</h2><section class="detail-section"><ul class="relationship-list">${rows}</ul></section>`;
    } catch (_) { /* non-Windchill connections simply have no unresolved endpoint results */ }
  }
  document.getElementById('unresolved-filter').addEventListener('change', showUnresolved);

  async function loadWindchillAnalysis() {
    if (!isWindchill) return;
    const element = document.getElementById('windchill-analysis');
    try {
      const data = await getJson(endpoint('/windchill/analysis'));
      element.innerHTML = `<h2>Windchill analysis</h2><div class="analysis-grid"><div><b>${data.total_tables}</b>tables</div><div><b>${data.windchill_classes}</b>classes</div><div><b>${data.windchill_relationships}</b>logical links</div><div><b>${data.unresolved}</b>unresolved</div></div><p><a href="/relationships/${connectionId}">Open full analysis →</a></p>`;
    } catch (_) { element.innerHTML = '<h2>Windchill analysis</h2><p class="loading">Available after extraction completes.</p>'; }
  }

  async function pollRun(force = false) {
    let continuePolling = false;
    try {
      const run = await getJson(endpoint('/runs/latest'));
      const active = run.status === 'queued' || run.status === 'running';
      continuePolling = active;
      if (active) {
        const percentage = Math.min(100, Math.round((run.completed_units / Math.max(run.total_units, 1)) * 100));
        setGraphStatus(`${run.phase} — ${percentage}%${run.message ? ` · ${run.message}` : ''}`);
      }
      if (run.status === 'completed' && (force || latestRunStatus !== 'completed')) { await loadGraph(); await loadWindchillAnalysis(); }
      if (run.status === 'failed') setGraphStatus(run.message || 'Extraction failed.');
      latestRunStatus = run.status;
    } catch (_) {
      // Retry transient startup/network failures, but do not poll completed runs.
      continuePolling = true;
    }
    if (continuePolling) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => pollRun(false), 2200);
    }
  }

  loadGraph(); loadWindchillAnalysis(); pollRun(false);
})();
