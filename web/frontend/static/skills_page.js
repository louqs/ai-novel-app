// ===== skills_page.js — Skill Builder =====

(async function init() { await loadBuiltPlugins(); })();

async function loadBuiltPlugins() {
    try {
        var resp = await fetch('/api/v1/skills/built');
        var data = await resp.json();
        var el = document.getElementById('built-list');
        var plugins = data.plugins || [];
        if (plugins.length === 0) { el.innerHTML = '<p class="muted">暂无已生成插件</p>'; return; }
        el.innerHTML = plugins.map(function(p) {
            return '<div class="model-card"><div class="model-info"><strong>' + esc(p.name || '未命名') + '</strong><span class="muted">' + esc(p.description || '') + '</span><span class="muted" style="font-size:10px">' + (p.type || 'transformer') + '</span></div><div class="model-actions"><button class="btn btn-sm" style="color:var(--bad)" onclick="deletePlugin(\'' + esc(p.name) + '\')">🗑</button></div></div>';
        }).join('');
    } catch(e) { document.getElementById('built-list').innerHTML = '<p class="error">加载失败</p>'; }
}

async function buildSkill() {
    var requirement = document.getElementById('requirement').value.trim();
    if (!requirement) { alert('请描述插件功能'); return; }
    var progressBox = document.getElementById('progress-box');
    var buildResult = document.getElementById('build-result');
    progressBox.style.display = 'block';
    buildResult.innerHTML = '';
    document.getElementById('progress-step').textContent = 'AI 分析需求中...';
    document.getElementById('progress-pct').textContent = '0%';
    document.getElementById('progress-fill').style.width = '0';
    try {
        var resp = await fetch('/api/v1/skills/build', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({requirement: requirement})});
        var d = await resp.json();
        if (d.status === 'built') {
            document.getElementById('progress-step').textContent = '✅ 构建完成';
            document.getElementById('progress-pct').textContent = '100%';
            document.getElementById('progress-fill').style.width = '100%';
            buildResult.innerHTML = '<div style="padding:8px;background:rgba(76,175,80,0.1);border-radius:4px;font-size:12px"><strong>✅ ' + esc(d.name || '插件') + '</strong> 已生成并保存到 <code>plugins/_built/</code></div>';
            loadBuiltPlugins();
        } else {
            document.getElementById('progress-step').textContent = '❌ 构建失败';
            buildResult.innerHTML = '<p class="error">' + esc(d.error || '构建失败') + '</p>';
        }
    } catch(e) {
        document.getElementById('progress-step').textContent = '❌ 请求失败';
        buildResult.innerHTML = '<p class="error">请求失败</p>';
    }
}

async function deletePlugin(name) {
    if (!confirm('确定删除插件: ' + name + '?')) return;
    try {
        await fetch('/api/v1/skills/built/' + encodeURIComponent(name), {method: 'DELETE'});
        toast('已删除', 'success'); loadBuiltPlugins();
    } catch(e) { toast('删除失败', 'error'); }
}

function esc(s) { return (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
