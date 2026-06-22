// ===== packs_page.js — 知识包市场 =====

(async function init() { await refreshPacks(); })();

async function refreshPacks() {
    try {
        var catalogEl = document.getElementById('catalog-list');
        var localEl = document.getElementById('local-list');

        // 获取目录包（含 installed 状态）
        var catalogResp = await fetch('/api/v1/packs/catalog');
        var catalog = await catalogResp.json();
        if (!Array.isArray(catalog)) catalog = [];

        catalogEl.innerHTML = catalog.length ? catalog.map(function(p) {
            var isInstalled = p.installed;
            var btnHtml = isInstalled
                ? '<span style="color:var(--ok);font-size:12px">✅ 已安装</span>'
                : '<button class="btn btn-sm btn-primary" onclick="installPack(\'' + esc(p.name) + '\')">安装</button>';
            return '<div class="model-card"><div class="model-info"><strong>' + esc(p.name || '') + '</strong><span class="muted">' + esc(p.description || '') + '</span><span class="muted" style="font-size:10px">v' + esc(p.version || '1.0') + '</span></div><div class="model-actions">' + btnHtml + '</div></div>';
        }).join('') : '<p class="muted">暂无可用包</p>';

        // 获取本地已安装包
        var localResp = await fetch('/api/v1/packs/local');
        var installed = await localResp.json();
        if (!Array.isArray(installed)) installed = [];

        localEl.innerHTML = installed.length ? installed.map(function(p) {
            return '<div class="model-card"><div class="model-info"><strong>' + esc(p.name || '') + '</strong><span class="muted">' + esc(p.description || '') + '</span><span class="muted" style="font-size:10px">v' + esc(p.version || '1.0') + ' | 更新: ' + esc(p.updated_at || '未知') + '</span></div><div class="model-actions"><button class="btn btn-sm" style="color:var(--bad)" onclick="uninstallPack(\'' + esc(p.name) + '\')">卸载</button></div></div>';
        }).join('') : '<p class="muted">暂无已安装包</p>';
    } catch(e) { document.getElementById('catalog-list').innerHTML = '<p class="error">加载失败: ' + e.message + '</p>'; }
}

async function installPack(name) {
    try {
        var resp = await fetch('/api/v1/packs/install', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source: name})});
        if (!resp.ok) {
            var err = await resp.json().catch(function() { return {detail: '未知错误'}; });
            toast('安装失败: ' + (err.detail || resp.status), 'error');
            return;
        }
        toast('已安装', 'success');
        await refreshPacks();
    } catch(e) { toast('安装失败: ' + e.message, 'error'); }
}
async function uninstallPack(name) {
    if (!confirm('确定卸载知识包: ' + name + '?')) return;
    try {
        await fetch('/api/v1/packs/uninstall/' + encodeURIComponent(name), {method: 'POST'});
        toast('已卸载', 'success'); refreshPacks();
    } catch(e) { toast('卸载失败', 'error'); }
}

async function updateKnowledge() {
    var btn = event.target;
    btn.disabled = true; btn.textContent = '更新中...';
    try {
        await fetch('/api/v1/packs/update', {method: 'POST'});
        toast('知识库已更新', 'success'); refreshPacks();
    } catch(e) { toast('更新失败', 'error'); }
    btn.disabled = false; btn.textContent = '🔄 更新知识库';
}

function esc(s) { return (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
