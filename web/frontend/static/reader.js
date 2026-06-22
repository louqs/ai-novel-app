// ===== reader.js — 阅读模式 =====

var projectId = '';
var chapters = [];
var currentVol = 1, currentCh = 1;

(async function init() {
    var params = new URLSearchParams(window.location.search);
    projectId = params.get('project_id');
    if (!projectId) { document.getElementById('reader-content').innerHTML = '<p style="text-align:center;padding-top:200px;color:#999">请从工作台访问此页面</p>'; return; }
    try {
        var resp = await fetch('/api/v1/projects/' + projectId);
        var proj = await resp.json();
        document.querySelector('.reader-header h1').textContent = proj.title || '阅读模式';
        document.title = (proj.title || '阅读') + ' - 阅读模式';
        // 更新返回链接
        var backLink = document.querySelector('.reader-header .nav a[href*="workbench"]');
        if (backLink) backLink.href = '/workbench?project_id=' + projectId;
    } catch(e) {}
    try {
        var resp = await fetch('/api/v1/projects/' + projectId + '/outline');
        var outline = await resp.json();
        if (outline.volumes) {
            var toc = '';
            var sel = document.getElementById('chapter-select');
            outline.volumes.forEach(function(vol) {
                toc += '<div class="toc-vol">📘 第' + vol.volume_number + '卷: ' + (vol.title || '') + '</div>';
                (vol.chapters || []).forEach(function(ch) {
                    chapters.push({vol: vol.volume_number, ch: ch.chapter_number, title: ch.title || ''});
                    toc += '<div class="toc-item" onclick="jumpToChapter(\'' + vol.volume_number + '_' + ch.chapter_number + '\')">Ch' + ch.chapter_number + ' ' + (ch.title || '') + '</div>';
                    sel.innerHTML += '<option value="' + vol.volume_number + '_' + ch.chapter_number + '">第' + vol.volume_number + '卷 Ch' + ch.chapter_number + ' ' + (ch.title || '') + '</option>';
                });
            });
            document.getElementById('toc-panel').innerHTML = toc;
        }
    } catch(e) {}
    if (chapters.length > 0) await loadChapter(chapters[0].vol, chapters[0].ch);
})();

async function loadChapter(vol, ch) {
    currentVol = vol; currentCh = ch;
    var content = document.getElementById('reader-content');
    content.innerHTML = '<p style="text-align:center;color:#999">加载中...</p>';
    try {
        var resp = await fetch('/api/v1/projects/' + projectId + '/chapters/' + ch + '?volume=' + vol);
        var data = await resp.json();
        var text = data.content || '';
        text = text.replace(/^---[\s\S]*?---\n*/, '');
        text = text.replace(/^#.*\n*/m, '');
        var paragraphs = text.split(/\n\n+/).filter(function(p) { return p.trim(); });
        var html = '<h2>第' + vol + '卷 第' + ch + '章</h2>';
        html += '<div class="chapter-meta">' + (data.word_count || text.length) + '字</div>';
        paragraphs.forEach(function(p) {
            p = p.trim();
            if (p === '---') html += '<div style="text-align:center;margin:24px 0;color:#999">* * *</div>';
            else if (p.startsWith('#')) html += '<h3 style="margin:16px 0 8px">' + p.replace(/^#+\s*/, '') + '</h3>';
            else if (p.match(/^[""「『]/)) html += '<p style="text-indent:0">' + p + '</p>';
            else html += '<p>' + p + '</p>';
        });
        html += '<div class="chapter-end">— 本章完 —</div>';
        content.innerHTML = html;
        document.getElementById('chapter-select').value = vol + '_' + ch;
        var idx = chapters.findIndex(function(c) { return c.vol === vol && c.ch === ch; });
        document.getElementById('nav-prev').className = idx <= 0 ? 'disabled' : '';
        document.getElementById('nav-next').className = idx >= chapters.length - 1 ? 'disabled' : '';
        // 更新目录高亮
        document.querySelectorAll('.toc-item').forEach(function(el) { el.classList.remove('active'); });
        var tocItems = document.querySelectorAll('.toc-item');
        if (idx >= 0 && tocItems[idx]) tocItems[idx].classList.add('active');
        window.scrollTo(0, 0);
    } catch(e) { content.innerHTML = '<p style="color:red">加载失败</p>'; }
}

function navigateChapter(delta) {
    var idx = chapters.findIndex(function(c) { return c.vol === currentVol && c.ch === currentCh; });
    var newIdx = idx + delta;
    if (newIdx >= 0 && newIdx < chapters.length) loadChapter(chapters[newIdx].vol, chapters[newIdx].ch);
}

function jumpToChapter(val) {
    var parts = val.split('_');
    if (parts.length === 2) loadChapter(parseInt(parts[0]), parseInt(parts[1]));
}

function toggleTOC() {
    var panel = document.getElementById('toc-panel');
    var overlay = document.getElementById('toc-overlay');
    panel.classList.toggle('open');
    overlay.classList.toggle('open');
}
