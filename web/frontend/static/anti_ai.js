// ===== anti_ai.js — 反AI检测与改写 =====

var originalText = '', optimizedText = '';
var currentProjectId = '', currentChapterNum = 0;

// ===== Init =====
(async function init() {
    try {
        var resp = await fetch('/api/v1/projects');
        var projects = await resp.json();
        var sel = document.getElementById('project-select');
        projects.forEach(function(p) { sel.innerHTML += '<option value="' + p.project_id + '">' + p.title + '</option>'; });
    } catch(e) {}
    document.getElementById('text-input').addEventListener('input', function() {
        document.getElementById('char-count').textContent = this.value.length + ' 字';
    });
})();

// ===== 项目/章节选择 =====
async function loadChapters() {
    var pid = document.getElementById('project-select').value;
    currentProjectId = pid;
    var chapterSel = document.getElementById('chapter-select');
    var wrap = document.getElementById('chapter-select-wrap');
    if (!pid) { wrap.style.display = 'none'; chapterSel.innerHTML = '<option value="">选择章节...</option>'; return; }
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/chapters/list');
        var chapters = await resp.json();
        chapterSel.innerHTML = '<option value="">选择章节...</option>';
        (chapters || []).forEach(function(ch) { chapterSel.innerHTML += '<option value="' + ch.chapter_number + '">第' + ch.chapter_number + '章 ' + (ch.title || '') + '</option>'; });
        wrap.style.display = 'block';
    } catch(e) { chapterSel.innerHTML = '<option value="">加载失败</option>'; }
}
async function loadChapterContent() {
    var chNum = document.getElementById('chapter-select').value;
    if (!chNum || !currentProjectId) return;
    currentChapterNum = parseInt(chNum);
    var ta = document.getElementById('text-input');
    ta.value = '加载中...';
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + chNum);
        if (resp.ok) { var data = await resp.json(); ta.value = data.content || ''; document.getElementById('char-count').textContent = ta.value.length + ' 字'; }
        else { ta.value = ''; }
    } catch(e) { ta.value = '加载失败'; }
}

// ===== 检测 =====
async function step1_detect() {
    var text = document.getElementById('text-input').value;
    if (!text || text.length < 50) { alert('请输入至少50字的文本'); return; }
    document.getElementById('detect-card').style.display = 'block';
    document.getElementById('detect-result').innerHTML = '<p class="muted">检测中...</p>';
    try {
        var r = await fetch('/api/v1/anti-ai/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: text})});
        var d = await r.json();
        var aiRate = Math.round((1 - d.ai_score) * 100);
        var cls = aiRate < 30 ? 'ok' : (aiRate < 60 ? 'warn' : 'bad');
        var label = aiRate < 30 ? '✅ 人类写作' : (aiRate < 60 ? '⚠️ 有AI痕迹' : '❌ AI痕迹明显');
        var h = '<div style="display:flex;gap:16px;align-items:flex-start">';
        h += '<div style="text-align:center;flex-shrink:0">';
        h += '<div class="score-circle ' + cls + '" style="width:80px;height:80px"><span class="score-number" style="font-size:28px">' + aiRate + '%</span></div>';
        h += '<p style="margin-top:4px;font-size:12px;font-weight:600">' + label + '</p></div>';
        h += '<div style="flex:1;display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px">';
        if (d.sentence_uniformity) { var su = d.sentence_uniformity; h += '<div class="detect-dim"><strong>📊 句长</strong>: <span class="' + (su.is_uniform ? 'bad' : 'ok') + '">SD=' + su.sd + '</span> ' + (su.is_uniform ? '⚠️均匀' : '✅正常') + '</div>'; }
        if (d.vocabulary_diversity !== undefined) { var vdd = d.vocabulary_diversity; h += '<div class="detect-dim"><strong>📚 词汇</strong>: <span class="' + (vdd > 0.1 ? 'bad' : 'ok') + '">' + (vdd * 100).toFixed(0) + '%</span> ' + (vdd > 0.1 ? '⚠️重复' : '✅多样') + '</div>'; }
        if (d.template_words) { var tw = d.template_words; h += '<div class="detect-dim"><strong>🤖 模板词</strong>: <span class="' + (tw.is_excessive ? 'bad' : 'ok') + '">' + tw.total + '次</span> ' + (tw.is_excessive ? '⚠️超限' : '✅正常') + '</div>'; }
        if (d.not_x_but_y) { var nxy = d.not_x_but_y; h += '<div class="detect-dim"><strong>⚠️ 不是X是Y</strong>: <span class="' + (nxy.is_excessive ? 'bad' : 'ok') + '">' + nxy.count + '处</span> ' + (nxy.is_excessive ? '❌超限' : '✅正常') + '</div>'; }
        if (d.dialogue_quotes) { var dq = d.dialogue_quotes; h += '<div class="detect-dim"><strong>📝 引号</strong>: <span class="' + (dq.is_adequate ? 'ok' : 'bad') + '">' + dq.quote_pairs + '对</span> ' + (dq.is_adequate ? '✅充足' : '❌不足') + '</div>'; }
        h += '</div></div>';
        document.getElementById('detect-result').innerHTML = h;
    } catch(e) { document.getElementById('detect-result').innerHTML = '<p class="error">检测失败: ' + e.message + '</p>'; }
}

// ===== 智能降重 =====
async function autoReduce() {
    var text = document.getElementById('text-input').value;
    if (!text || text.length < 50) { alert('请输入至少50字的文本'); return; }
    var btn = document.getElementById('btn-reduce');
    btn.disabled = true;
    var bar = document.getElementById('progress-bar');
    var fill = document.getElementById('progress-fill');
    bar.style.display = 'block';
    var currentText = text;
    var currentAiRate = 100;
    var round = 0;
    var maxRounds = 5;
    var targetAiRate = parseInt(document.getElementById('target-ai-rate').value) || 30;
    var history = [{text: text, aiRate: 100, round: 0, issues: [], improvement: 0}];
    try {
        var initResp = await fetch('/api/v1/anti-ai/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: currentText})});
        var initData = await initResp.json();
        currentAiRate = Math.round((1 - initData.ai_score) * 100);
        history[0].aiRate = currentAiRate;
        history[0].issues = initData.pattern_matches || [];
        updateDetectStatus('初始AI率: ' + currentAiRate + '%', currentAiRate);
        if (currentAiRate <= targetAiRate) { btn.disabled = false; btn.textContent = '✅ 已达标'; setTimeout(function() { btn.textContent = '🔄 智能降重'; }, 1500); bar.style.display = 'none'; return; }
    } catch(e) {}
    while (currentAiRate > targetAiRate && round < maxRounds) {
        round++;
        btn.textContent = '🔄 第' + round + '轮改写中...';
        fill.style.width = (round / maxRounds * 100) + '%';
        try {
            var reduceMode = document.getElementById('reduce-mode').value;
            var humanizeResp = await fetch('/api/v1/anti-ai/humanize', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: currentText, mode: reduceMode})});
            var humanizeData = await humanizeResp.json();
            if (humanizeData.content) currentText = humanizeData.content;
        } catch(e) { console.error('改写失败:', e); break; }
        try {
            var newCheckResp = await fetch('/api/v1/anti-ai/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: currentText})});
            var newCheckData = await newCheckResp.json();
            var newAiRate = Math.round((1 - newCheckData.ai_score) * 100);
            var improvement = currentAiRate - newAiRate;
            history.push({text: currentText, aiRate: newAiRate, round: round, issues: newCheckData.pattern_matches || [], improvement: improvement});
            updateDetectStatus('第' + round + '轮: ' + currentAiRate + '% → ' + newAiRate + '% (↓' + improvement + '%)', newAiRate);
            currentAiRate = newAiRate;
        } catch(e) { console.error('检测失败:', e); }
    }
    document.getElementById('text-input').value = currentText;
    document.getElementById('char-count').textContent = currentText.length + ' 字';
    fill.style.width = '100%';
    btn.textContent = '✅ 降重完成';
    setTimeout(function() { btn.disabled = false; btn.textContent = '🔄 智能降重'; bar.style.display = 'none'; }, 2000);
    showDiffHistory(history, currentAiRate);
    toast('降重完成: ' + round + '轮，AI率' + history[0].aiRate + '%→' + currentAiRate + '%', currentAiRate <= targetAiRate ? 'success' : 'warn');
}
function updateDetectStatus(message, aiRate) {
    var el = document.getElementById('detect-result');
    document.getElementById('detect-card').style.display = 'block';
    var cls = aiRate < 30 ? 'ok' : (aiRate < 60 ? 'warn' : 'bad');
    el.innerHTML = '<div style="text-align:center;padding:16px"><div class="score-circle ' + cls + '" style="width:100px;height:100px;margin:0 auto 12px"><span class="score-number" style="font-size:32px">' + aiRate + '%</span></div><p style="font-size:14px;font-weight:600">AI率</p><p style="color:var(--accent);margin-top:4px;font-size:13px">' + message + '</p></div>';
}

// ===== 合并对比（使用 common.js diff 组件）=====
function showDiffHistory(history, finalAiRate) {
    if (history.length < 2) return;
    var mergeCard = document.getElementById('merge-card');
    mergeCard.style.display = 'block';
    originalText = history[0].text;
    optimizedText = history[history.length - 1].text;
    var summaryHTML = '<div style="padding:10px;background:var(--surface);border-radius:6px;margin-bottom:10px;font-size:12px"><strong>📊 降重进度</strong><br>';
    for (var i = 0; i < history.length; i++) {
        var h = history[i];
        if (i === 0) summaryHTML += '<span class="bad">' + h.aiRate + '%</span> → ';
        else if (i === history.length - 1) { var cls = h.aiRate < 30 ? 'ok' : (h.aiRate < 60 ? 'warn' : 'bad'); summaryHTML += '<span class="' + cls + '">' + h.aiRate + '%</span>'; }
    }
    summaryHTML += '</div>';
    diffBuildItems(originalText, optimizedText);
    diffRenderOriginal();
    diffRenderCards(summaryHTML);
    diffUpdateMergeResult();
    document.getElementById('btn-select-all').onclick = function() { diffBatchAccept(); };
    document.getElementById('btn-deselect-all').onclick = function() { diffBatchReject(); };
}

// ===== 应用合并结果 =====
function applyMerge() {
    var result = document.getElementById('col-final').value;
    if (!result) return;
    if (currentProjectId && currentChapterNum) {
        if (confirm('确定覆盖项目 ' + currentProjectId + ' 的第' + currentChapterNum + '章？')) saveToChapter(result);
    }
}
async function saveToChapter(content) {
    try {
        await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + currentChapterNum, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: content})});
        document.getElementById('merge-card').style.display = 'none';
        document.getElementById('text-input').value = content;
        document.getElementById('char-count').textContent = content.length + ' 字';
        toast('已覆盖章节内容', 'success');
    } catch(e) { toast('保存失败', 'error'); }
}

// ===== 在线检测 =====
async function onlineDetect(platform) {
    var text = document.getElementById('text-input').value;
    if (!text || text.length < 50) { alert('请输入至少50字的文本'); return; }
    var resultDiv = document.getElementById('online-detect-result');
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = '<p class="muted">获取检测指令...</p>';
    try {
        var resp = await fetch('/api/v1/anti-ai/online-detect', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: text, platform: platform})});
        var data = await resp.json();
        if (data.error) { resultDiv.innerHTML = '<p class="error">' + data.error + '</p>'; return; }
        var platformInfo = data.platform || {};
        var html = '<div style="padding:12px;background:var(--surface);border-radius:6px"><strong>' + (platformInfo.name || platform) + '</strong><span class="muted" style="margin-left:8px">' + (platformInfo.description || '') + '</span><br><span class="muted" style="font-size:12px">文本长度: ' + data.text_length + '字';
        if (data.chunks_count > 1) html += ' (' + data.chunks_count + '段)';
        html += '</span><br><a href="' + platformInfo.url + '" target="_blank" class="btn btn-sm" style="margin-top:8px;display:inline-block">打开检测平台</a></div>';
        if (data.chunks && data.chunks.length > 0) {
            html += '<div style="margin-top:12px"><strong>检测文本（点击复制）:</strong>';
            for (var ci = 0; ci < data.chunks.length; ci++) {
                var chunk = data.chunks[ci];
                var chunkId = 'chunk-' + chunk.index;
                html += '<div style="margin-top:8px;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;cursor:pointer" onclick="copyChunk(\'' + chunkId + '\')"><div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">第' + (chunk.index + 1) + '段 (' + chunk.length + '字) - 点击复制</div><pre id="' + chunkId + '" style="white-space:pre-wrap;font-size:12px;max-height:200px;overflow-y:auto">' + esc(chunk.text) + '</pre></div>';
            }
            html += '</div>';
        }
        resultDiv.innerHTML = html;
    } catch(e) { resultDiv.innerHTML = '<p class="error">获取失败: ' + e.message + '</p>'; }
}
function copyChunk(chunkId) {
    var el = document.getElementById(chunkId);
    if (el) {
        navigator.clipboard.writeText(el.textContent).then(function() { toast('已复制', 'success', 1500); }).catch(function() {
            var range = document.createRange(); range.selectNode(el);
            window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
            document.execCommand('copy'); toast('已复制', 'success', 1500);
        });
    }
}
async function recordDetectResult() {
    var aiRate = parseFloat(document.getElementById('record-ai-rate').value);
    if (isNaN(aiRate) || aiRate < 0 || aiRate > 100) { alert('请输入有效的AI率(0-100)'); return; }
    try {
        var resp = await fetch('/api/v1/anti-ai/record-result', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project_id: currentProjectId || '', chapter_num: currentChapterNum || 0, ai_rate: aiRate, platform: '手动记录', notes: ''})});
        var data = await resp.json();
        if (data.recorded) { var level = aiRate < 20 ? '✅ 优秀' : (aiRate < 40 ? '⚠️ 可接受' : (aiRate < 60 ? '❌ 需精修' : '🚨 严重')); toast('已记录: AI率 ' + aiRate + '% (' + level + ')', aiRate < 40 ? 'success' : 'warn'); }
    } catch(e) { toast('记录失败', 'error'); }
}
