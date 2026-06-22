// ===== cover.js — 封面生成 =====

var _coverProjectId = '';

(async function init() {
    try {
        var resp = await fetch('/api/v1/projects');
        var projects = await resp.json();
        var sel = document.getElementById('project-select');
        projects.forEach(function(p) { sel.innerHTML += '<option value="' + p.project_id + '">' + p.title + '</option>'; });
    } catch(e) {}
})();

async function loadProject(pid) {
    if (!pid) return;
    _coverProjectId = pid;
    try {
        var resp = await fetch('/api/v1/projects/' + pid);
        var proj = await resp.json();
        document.getElementById('title-input').value = proj.title || '';
        document.getElementById('oneliner-input').value = proj.one_liner || '';
        document.getElementById('author-input').value = proj.author || 'AI-Assisted';
    } catch(e) {}
}

async function genPrompt() {
    var title = document.getElementById('title-input').value.trim();
    var style = document.getElementById('style-select').value;
    var oneliner = document.getElementById('oneliner-input').value.trim();
    if (!title) { alert('请输入书名'); return; }
    var resultCard = document.getElementById('result-card');
    var promptDisplay = document.getElementById('prompt-display');
    resultCard.style.display = 'block';
    promptDisplay.textContent = '生成中...';
    try {
        var resp = await fetch('/api/v1/cover/prompt', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: title, style: style, one_liner: oneliner})});
        var d = await resp.json();
        if (d.prompt) promptDisplay.textContent = d.prompt;
        else promptDisplay.textContent = d.error || '生成失败';
    } catch(e) { promptDisplay.textContent = '请求失败'; }
}

function copyPrompt() {
    var text = document.getElementById('prompt-display').textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(function() { toast('已复制', 'success'); }).catch(function() {
        var range = document.createRange(); range.selectNode(document.getElementById('prompt-display'));
        window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
        document.execCommand('copy'); toast('已复制', 'success');
    });
}
