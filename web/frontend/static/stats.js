// ===== stats.js — 数据看板 =====

(async function init() {
    try {
        var resp = await fetch('/api/v1/projects');
        var projects = await resp.json();
        var sel = document.getElementById('project-select');
        projects.forEach(function(p) { sel.innerHTML += '<option value="' + p.project_id + '">' + p.title + '</option>'; });
        var params = new URLSearchParams(window.location.search);
        if (params.get('project_id')) { sel.value = params.get('project_id'); loadStats(params.get('project_id')); }
    } catch(e) {}
})();

async function loadStats(pid) {
    if (!pid) return;
    var div = document.getElementById('stats-result');
    div.innerHTML = '<p class="muted">加载中...</p>';
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/stats');
        var d = await resp.json();
        var h = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">';
        h += '<div class="stat-card"><div class="stat-value">' + (d.total_chapters || 0) + '</div><div class="stat-label">总章数</div></div>';
        h += '<div class="stat-card"><div class="stat-value">' + ((d.total_words || 0).toLocaleString()) + '</div><div class="stat-label">总字数</div></div>';
        h += '<div class="stat-card"><div class="stat-value">' + (d.avg_words_per_chapter || 0) + '</div><div class="stat-label">均字数/章</div></div>';
        h += '</div>';
        div.innerHTML = h;
    } catch(e) { div.innerHTML = '<p class="error">加载失败</p>'; }
}
