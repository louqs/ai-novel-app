// ===== coach.js — 写作教练 =====

var currentPid = sessionStorage.getItem('coach_pid') || '';
var currentChNum = 0;
var _coachTimer = null, _optTimer = null;

(async function init() {
    try {
        var resp = await fetch('/api/v1/projects'); var projects = await resp.json();
        var sel = document.getElementById('project-select');
        projects.forEach(function(p) { sel.innerHTML += '<option value="' + p.project_id + '">' + p.title + '</option>'; });
        var params = new URLSearchParams(window.location.search);
        if (params.get('project_id')) { sel.value = params.get('project_id'); loadCoach(params.get('project_id')); }
    } catch(e) {}
})();

async function loadCoach(pid) {
    if (!pid) return;
    currentPid = pid; sessionStorage.setItem('coach_pid', pid);
    var div = document.getElementById('coach-result'); div.innerHTML = '<p class="muted">分析中...</p>';
    var bar = document.getElementById('coach-progress'); bar.style.display = 'block';
    var fill = document.getElementById('coach-progress-fill'); fill.style.width = '0';
    if (_coachTimer) clearInterval(_coachTimer);
    var pct = 0;
    _coachTimer = setInterval(function() { pct = Math.min(pct + Math.random() * 5, 85); fill.style.width = pct + '%'; }, 400);
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/coach', {method: 'POST'});
        var d = await resp.json();
        clearInterval(_coachTimer); fill.style.width = '100%';
        setTimeout(function() { bar.style.display = 'none'; }, 500);
        if (d.error) { div.innerHTML = '<p class="error">' + d.error + '</p>'; return; }

        var h = '<h3>评分: ' + ((d.avg_score || 0).toFixed(2)) + ' | ' + (d.total_chapters || 0) + '章 | ' + ((d.total_words || 0).toLocaleString()) + '字</h3><p>' + (d.summary || '') + '</p>';

        // 整本小说分析
        var wn = d.whole_novel || {};
        if (wn.word_stats || wn.rhythm_curve || wn.ai_overview) {
            h += '<hr><h3 style="margin-bottom:12px">📖 整本小说分析</h3>';
            var ai = wn.ai_overview || {};
            if (ai.structure_score != null) {
                h += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">';
                h += '<div style="background:var(--surface2);padding:8px 12px;border-radius:6px;font-size:12px">结构评分: <strong>' + ((ai.structure_score || 0).toFixed(2)) + '</strong></div>';
                h += '<div style="background:var(--surface2);padding:8px 12px;border-radius:6px;font-size:12px">平台适配: <strong>' + ((ai.platform_fit_score || 0).toFixed(2)) + '</strong></div></div>';
                if (ai.pacing_comment) h += '<div style="font-size:12px;margin:4px 0"><strong>节奏:</strong> ' + esc(ai.pacing_comment) + '</div>';
                if (ai.character_comment) h += '<div style="font-size:12px;margin:4px 0"><strong>人物:</strong> ' + esc(ai.character_comment) + '</div>';
                if (ai.world_building_comment) h += '<div style="font-size:12px;margin:4px 0"><strong>世界观:</strong> ' + esc(ai.world_building_comment) + '</div>';
                if (ai.strengths && ai.strengths.length) h += '<div style="font-size:12px;margin:4px 0"><strong>亮点:</strong> ' + ai.strengths.map(function(s) { return esc(s); }).join('、') + '</div>';
                if (ai.top_issues && ai.top_issues.length) h += '<div style="font-size:12px;margin:4px 0;color:var(--bad)"><strong>待改进:</strong> ' + ai.top_issues.map(function(s) { return esc(s); }).join('、') + '</div>';
            }
            var ws = wn.word_stats || {};
            if (ws.avg) {
                var cvColor = ws.cv < 0.3 ? 'var(--ok)' : ws.cv < 0.5 ? 'var(--warn)' : 'var(--bad)';
                h += '<div style="margin-top:12px;font-size:12px"><strong>📊 字数统计</strong><span style="margin-left:8px">均值' + ws.avg + '字 | 最短' + ws.min + '字 | 最长' + ws.max + '字 | 波动系数 <span style="color:' + cvColor + '">' + ws.cv + '</span></span></div>';
            }
            var rc = wn.rhythm_curve || [];
            if (rc.length > 0) {
                h += '<div style="margin-top:12px"><strong style="font-size:12px">📈 节奏曲线</strong>';
                h += '<div style="font-size:10px;color:var(--text-muted);margin:4px 0">对话占比（每章）</div>';
                h += '<div style="display:flex;align-items:flex-end;gap:1px;height:60px;background:var(--bg);padding:4px;border-radius:4px">';
                rc.forEach(function(r) { var pct = Math.min(r.dialogue_ratio, 100); var color = pct > 40 ? 'var(--ok)' : pct > 20 ? 'var(--accent)' : 'var(--warn)'; h += '<div style="flex:1;background:' + color + ';height:' + Math.max(pct, 2) + '%;border-radius:2px 2px 0 0" title="Ch' + r.ch + ': ' + r.dialogue_ratio + '%对话"></div>'; });
                h += '</div>';
                h += '<div style="font-size:10px;color:var(--text-muted);margin:4px 0">情绪密度（每章）</div>';
                h += '<div style="display:flex;align-items:flex-end;gap:1px;height:40px;background:var(--bg);padding:4px;border-radius:4px">';
                var maxEmo = Math.max.apply(null, rc.map(function(r) { return r.emotion_density; }).concat([1]));
                rc.forEach(function(r) { var pct = r.emotion_density / maxEmo * 100; h += '<div style="flex:1;background:var(--warn);height:' + Math.max(pct, 2) + '%;border-radius:2px 2px 0 0" title="Ch' + r.ch + ': ' + r.emotion_density + '‰"></div>'; });
                h += '</div></div>';
            }
            var fs = wn.foreshadow_stats || {};
            if (fs.total > 0) {
                h += '<div style="margin-top:12px;font-size:12px"><strong>🧵 伏笔状态</strong><span style="margin-left:8px">共' + fs.total + '条 | 已埋' + fs.planted + ' | 推进中' + fs.building + ' | 已回收' + fs.paid + '</span>';
                if (fs.planted + fs.building > 0) h += '<span style="color:var(--warn);margin-left:8px">⚠ ' + (fs.planted + fs.building) + '条待回收</span>';
                h += '</div>';
            }
            var sc = wn.style_consistency || {};
            if (sc.dialogue_drift != null) {
                var stableColor = sc.is_stable ? 'var(--ok)' : 'var(--warn)';
                var stableText = sc.is_stable ? '稳定' : '有漂移';
                h += '<div style="margin-top:12px;font-size:12px"><strong>🎨 风格一致性</strong><span style="margin-left:8px">对话占比漂移' + sc.dialogue_drift + '% | 段落长度漂移' + sc.paragraph_drift + '字 | <span style="color:' + stableColor + '">' + stableText + '</span></span></div>';
            }
            var ct = wn.character_tracker || {};
            var charKeys = Object.keys(ct);
            if (charKeys.length > 0) {
                h += '<div style="margin-top:12px;font-size:12px"><strong>👥 人物追踪</strong></div>';
                h += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">';
                charKeys.forEach(function(name) { var c = ct[name]; h += '<span style="font-size:10px;padding:2px 6px;background:var(--surface2);border-radius:3px">' + esc(name) + ': ' + esc(c.status || '') + ' @ ' + esc(c.location || '') + '</span>'; });
                h += '</div>';
            }
        }

        h += '<hr><h3 style="margin:12px 0 8px">📝 章节分析</h3>';
        (d.chapter_analyses || []).forEach(function(ch) {
            h += '<div class="model-card"><div class="model-info"><strong>第' + ch.chapter + '章</strong> ' + ch.score.toFixed(2) + ' (' + ch.words + '字)';
            (ch.suggestions || []).slice(0, 2).forEach(function(s) { h += '<div class="muted">[' + s.area + '] ' + s.issue + '</div>'; });
            h += '</div><button class="btn btn-sm" onclick="optimizeChapter(' + ch.chapter + ')">✨ 优化对比</button></div>';
        });
        div.innerHTML = h;
    } catch(e) {
        clearInterval(_coachTimer); bar.style.display = 'none';
        div.innerHTML = '<p class="error">分析失败</p>';
    }
}

async function optimizeChapter(chNum) {
    currentChNum = chNum;
    document.getElementById('merge-ch-num').textContent = chNum;
    document.getElementById('merge-ch-num2').textContent = chNum;
    var mc = document.getElementById('merge-card'); mc.style.display = 'block';
    document.getElementById('col-diffs').innerHTML = '<p class="muted">加载原文并AI改写中...</p>';
    var bar = document.getElementById('coach-progress'); bar.style.display = 'block';
    var fill = document.getElementById('coach-progress-fill'); fill.style.width = '0';
    if (_optTimer) clearInterval(_optTimer);
    var pct = 0;
    _optTimer = setInterval(function() { pct = Math.min(pct + Math.random() * 4, 80); fill.style.width = pct + '%'; }, 500);
    try {
        var chResp = await fetch('/api/v1/projects/' + currentPid + '/chapters/' + chNum);
        var chData = await chResp.json();
        var origText = chData.content || '';
        var hResp = await fetch('/api/v1/anti-ai/humanize', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: origText, mode: 'standard'})});
        var hData = await hResp.json();
        var optText = hData.content || origText;
        clearInterval(_optTimer); fill.style.width = '100%';
        setTimeout(function() { bar.style.display = 'none'; }, 500);

        // 使用 common.js diff 组件
        diffBuildItems(origText, optText);
        var changed = diffState.items.filter(function(d) { return d.isChanged; }).length;
        document.getElementById('merge-stats').textContent = '变更:' + changed + ' | 已选: ' + changed + '/' + changed;
        diffRenderOriginal();
        diffRenderCards('');
        diffUpdateMergeResult();
    } catch(e) {
        clearInterval(_optTimer); bar.style.display = 'none';
        alert('加载失败: ' + e);
    }
}

async function applyMerge() {
    var r = document.getElementById('col-final').value;
    if (!r) return;
    try {
        await fetch('/api/v1/projects/' + currentPid + '/chapters/' + currentChNum, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: r})});
        toast('已应用到第' + currentChNum + '章', 'success');
        document.getElementById('merge-card').style.display = 'none';
    } catch(e) { toast('保存失败', 'error'); }
}
