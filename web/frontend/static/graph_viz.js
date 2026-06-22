// ===== graph_viz.js — 人物关系图 =====

var graphData = null;

(async function init() {
    var params = new URLSearchParams(window.location.search);
    var pid = params.get('project_id');
    if (!pid) { document.getElementById('full-graph').innerHTML = '<p style="text-align:center;padding-top:200px;color:var(--text-muted)">请从工作台访问此页面</p>'; return; }
    await loadFullGraph(pid);
})();

async function loadFullGraph(pid) {
    if (!pid) { var params = new URLSearchParams(window.location.search); pid = params.get('project_id'); }
    if (!pid) return;
    var container = document.getElementById('full-graph');
    try {
        var resp = await fetch('/api/v1/graph/export?project_id=' + pid);
        graphData = await resp.json();
        if (!graphData.nodes || graphData.nodes.length === 0) { container.innerHTML = '<p style="text-align:center;padding-top:200px;color:var(--text-muted)">暂无图谱数据</p>'; return; }
        drawGraph(container, graphData);
        renderLegend(graphData);
    } catch(e) { container.innerHTML = '<p class="error">加载失败</p>'; }
}

function drawGraph(container, data) {
    container.innerHTML = '';
    var W = container.clientWidth, H = container.clientHeight;
    var svg = d3.select(container).append('svg').attr('width', W).attr('height', H);
    var color = d3.scaleOrdinal(d3.schemeCategory10);
    var relTypeMap = {'ALLY':'盟友','ENEMY':'敌人','FAMILY':'家人','MASTER_DISCIPLE':'师徒','RIVAL':'对手','SUBORDINATE':'从属','ROMANTIC':'恋人','ACQUAINTANCE':'相识','BELONGS_TO':'属于','LOCATED_AT':'位于','POSSESSES':'拥有','PARTICIPATES_IN':'参与'};
    var nodes = data.nodes.map(function(n) { return Object.assign({}, n); });
    var edges = data.edges.filter(function(e) { return nodes.some(function(n) { return n.id === e.source; }) && nodes.some(function(n) { return n.id === e.target; }); }).map(function(e) { return {source: e.source, target: e.target, type: e.type}; });
    var sim = d3.forceSimulation(nodes).force('link', d3.forceLink(edges).id(function(d) { return d.id; }).distance(80)).force('charge', d3.forceManyBody().strength(-300)).force('center', d3.forceCenter(W / 2, H / 2));
    var link = svg.append('g').selectAll('line').data(edges).join('line').attr('stroke', '#555').attr('stroke-width', 2);
    var node = svg.append('g').selectAll('circle').data(nodes).join('circle').attr('r', 12).attr('fill', function(d) { return color(d.group); }).call(d3.drag().on('start', function(e, d) { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }).on('drag', function(e, d) { d.fx = e.x; d.fy = e.y; }).on('end', function(e, d) { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    var label = svg.append('g').selectAll('text').data(nodes).join('text').text(function(d) { return d.label || d.id; }).attr('font-size', 12).attr('fill', '#ddd').attr('dx', 14).attr('dy', 4);
    svg.append('g').selectAll('text').data(edges).join('text').text(function(d) { return relTypeMap[d.type] || d.type; }).attr('font-size', 9).attr('fill', '#888').attr('x', function(d) { return (d.source.x + d.target.x) / 2; }).attr('y', function(d) { return (d.source.y + d.target.y) / 2; });
    sim.on('tick', function() { link.attr('x1', function(d) { return d.source.x; }).attr('y1', function(d) { return d.source.y; }).attr('x2', function(d) { return d.target.x; }).attr('y2', function(d) { return d.target.y; }); node.attr('cx', function(d) { return d.x; }).attr('cy', function(d) { return d.y; }); label.attr('x', function(d) { return d.x; }).attr('y', function(d) { return d.y; }); });
}

function renderLegend(data) {
    var groups = new Set();
    (data.nodes || []).forEach(function(n) { groups.add(n.group); });
    var colors = d3.scaleOrdinal(d3.schemeCategory10);
    var legendEl = document.getElementById('legend');
    if (legendEl) {
        legendEl.innerHTML = Array.from(groups).map(function(g) {
            return '<span style="display:inline-flex;align-items:center;gap:4px;font-size:12px"><span style="width:12px;height:12px;border-radius:50%;background:' + colors(g) + ';display:inline-block"></span>' + g + '</span>';
        }).join('');
    }
}

function exportGraphPNG() {
    var svg = document.querySelector('#full-graph svg');
    if (!svg) { toast('无图谱可导出', 'error'); return; }
    var canvas = document.createElement('canvas');
    var W = svg.clientWidth * 2, H = svg.clientHeight * 2;
    canvas.width = W; canvas.height = H;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#1a1a2e'; ctx.fillRect(0, 0, W, H);
    var svgData = new XMLSerializer().serializeToString(svg);
    var img = new Image();
    img.onload = function() { ctx.drawImage(img, 0, 0, W, H); var link = document.createElement('a'); link.download = '人物关系图.png'; link.href = canvas.toDataURL('image/png'); link.click(); };
    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
}
