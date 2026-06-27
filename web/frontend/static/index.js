// ===== index.js — 项目列表 =====

var platformNames = {fanqie:'番茄小说', qidian:'起点中文网', jinjiang:'晋江文学城', qimao:'七猫小说', douban:'豆瓣阅读'};
var lengthNames = {short:'短篇', medium:'中篇', long:'长篇', extra_long:'超长篇'};
var lengthWordRanges = {short:{min:10000,max:50000}, medium:{min:50000,max:150000}, long:{min:150000,max:500000}, extra_long:{min:500000,max:7000000}};

(async function init() { await loadProjects(); })();

function updateWordRange() {
    var range = lengthWordRanges[document.getElementById('length').value];
    if (range) { document.getElementById('target_words_min').value = range.min; document.getElementById('target_words_max').value = range.max; }
}

async function loadProjects() {
    try {
        var resp = await fetch('/api/v1/projects');
        var projects = await resp.json();
        var el = document.getElementById('project-list');
        if (projects.length === 0) { el.innerHTML = '<p class="muted">暂无项目，创建第一个吧 ✨</p>'; return; }
        el.innerHTML = projects.map(function(p) {
            return '<div class="project-item"><div class="project-info"><strong>' + esc(p.title) + '</strong><span class="badge">' + (platformNames[p.platform] || p.platform) + '</span><span class="muted">' + (lengthNames[p.length] || '中篇') + ' | ' + (p.current_chapter > 0 ? '第' + p.current_chapter + '章' : '未开始') + '</span></div><div class="project-actions"><button class="btn btn-sm" onclick="editProject(\'' + p.project_id + '\')">✏️ 编辑</button><a href="/workbench?project_id=' + p.project_id + '" class="btn btn-sm">✏️ 创作</a><a href="/coach?project_id=' + p.project_id + '" class="btn btn-sm">📊 教练</a><a href="/stats?project_id=' + p.project_id + '" class="btn btn-sm">📈 看板</a><button class="btn btn-sm" onclick="exportProject(\'' + p.project_id + '\')">📥 导出</button><button class="btn btn-sm" data-del-pid="' + p.project_id + '" onclick="deleteProject(\'' + p.project_id + '\',\'' + esc(p.title || '').replace(/'/g, "\\'") + '\')" style="color:var(--bad);border-color:var(--bad)">🗑</button></div></div>';
        }).join('');
    } catch(e) { document.getElementById('project-list').innerHTML = '<p class="error">加载失败</p>'; }
}

async function generateTitle(oneLiner, genreTags) {
    var tags = genreTags.split(',').map(function(t) { return t.trim(); }).filter(Boolean);
    var tagStr = tags.length > 0 ? tags[0] : '';
    var keywords = oneLiner.split(/[，,。、]/).slice(0, 2).join('');
    if (tagStr && keywords) return tagStr + '：' + keywords;
    else if (keywords) return keywords;
    else return '新项目';
}
async function regenerateTitle() {
    var oneLiner = document.getElementById('one_liner').value;
    var genreTags = document.getElementById('genre_tags').value;
    if (!oneLiner) { alert('请先填写梗概'); return; }
    document.getElementById('title-preview').textContent = await generateTitle(oneLiner, genreTags);
    document.getElementById('generated-title').style.display = 'block';
}

var _creating = false;
async function createProject(e) {
    if (e) e.preventDefault();
    if (_creating) return;
    var title = document.getElementById('title-preview').textContent || '新项目';
    var platform = document.getElementById('platform').value;
    var length = document.getElementById('length').value;
    var oneLiner = document.getElementById('one_liner').value;
    var genreTags = document.getElementById('genre_tags').value;
    var minWords = document.getElementById('target_words_min').value;
    var maxWords = document.getElementById('target_words_max').value;
    if (!oneLiner) { alert('请填写梗概'); return; }
    _creating = true;
    var btn = document.querySelector('#create-form button[type="submit"], #create-form .btn-primary');
    if (btn) { btn.disabled = true; btn.dataset.origText = btn.textContent; btn.textContent = '创建中...'; }
    try {
        var resp = await fetch('/api/v1/projects', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: title, platform: platform, length: length, one_liner: oneLiner, genre_tags: genreTags.split(',').map(function(t) { return t.trim(); }).filter(Boolean), min_words: parseInt(minWords) || null, max_words: parseInt(maxWords) || null})});
        var d = await resp.json();
        if (d.project_id) { toast('项目已创建', 'success'); loadProjects(); }
    } catch(err) { toast('创建失败', 'error'); }
    finally { _creating = false; if (btn) { btn.disabled = false; btn.textContent = btn.dataset.origText || '创建项目'; } }
}

var _editProjectId = null;
var _lengthWordBounds = {short:{min:10000,max:50000}, medium:{min:50000,max:150000}, long:{min:150000,max:500000}, extra_long:{min:500000,max:7000000}};
// 编辑弹窗中切换「篇幅」时，同步刷新字数区间为该篇幅的默认值
function updateEditWordRange() {
    var bounds = _lengthWordBounds[document.getElementById('edit-length').value] || _lengthWordBounds.medium;
    document.getElementById('edit-min-words').value = bounds.min;
    document.getElementById('edit-max-words').value = bounds.max;
}
async function editProject(pid) {
    _editProjectId = pid;
    try {
        var resp = await fetch('/api/v1/projects/' + pid);
        var proj = await resp.json();
        document.getElementById('edit-title').value = proj.title || '';
        document.getElementById('edit-one-liner').value = proj.one_liner || '';
        document.getElementById('edit-platform').value = proj.platform || 'fanqie';
        document.getElementById('edit-length').value = proj.length || 'medium';
        document.getElementById('edit-genre-tags').value = (proj.genre_tags || []).join(', ');
        document.getElementById('edit-target-words').value = proj.target_words_per_chapter || '';
        var bounds = _lengthWordBounds[proj.length] || _lengthWordBounds.medium;
        document.getElementById('edit-min-words').value = proj.min_words || bounds.min;
        document.getElementById('edit-max-words').value = proj.max_words || bounds.max;
        document.getElementById('edit-modal').style.display = 'flex';
    } catch(e) { toast('加载失败', 'error'); }
}
function closeEditModal() { document.getElementById('edit-modal').style.display = 'none'; _editProjectId = null; }
async function saveProjectEdit() {
    if (!_editProjectId) return;
    try {
        await fetch('/api/v1/projects/' + _editProjectId, {method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: document.getElementById('edit-title').value, one_liner: document.getElementById('edit-one-liner').value, platform: document.getElementById('edit-platform').value, length: document.getElementById('edit-length').value, genre_tags: document.getElementById('edit-genre-tags').value.split(',').map(function(t) { return t.trim(); }).filter(Boolean), target_words_per_chapter: parseInt(document.getElementById('edit-target-words').value) || null, min_words: parseInt(document.getElementById('edit-min-words').value) || null, max_words: parseInt(document.getElementById('edit-max-words').value) || null})});
        toast('项目已更新', 'success'); closeEditModal(); loadProjects();
    } catch(e) { toast('保存失败', 'error'); }
}

var _deleting = {};
async function deleteProject(pid, title) {
    if (_deleting[pid]) return;
    if (!confirm('确定删除项目「' + title + '」？此操作不可恢复。')) return;
    _deleting[pid] = true;
    // 禁用该项目的删除按钮并显示 loading
    var btns = document.querySelectorAll('[data-del-pid="' + pid + '"]');
    btns.forEach(function(b) { b.disabled = true; b.dataset.origText = b.textContent; b.textContent = '删除中...'; });
    try { await fetch('/api/v1/projects/' + pid, {method: 'DELETE'}); toast('已删除', 'success'); loadProjects(); } catch(e) { toast('删除失败', 'error'); }
    finally { delete _deleting[pid]; btns.forEach(function(b) { b.disabled = false; b.textContent = b.dataset.origText || '删除'; }); }
}
async function exportProject(pid) {
    try { window.open('/api/v1/projects/' + pid + '/export'); } catch(e) { toast('导出失败', 'error'); }
}

function esc(s) { return (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
