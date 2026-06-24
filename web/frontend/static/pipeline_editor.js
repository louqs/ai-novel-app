// ===== pipeline_editor.js — 简化版编辑优化 =====

var peState = {
    projectId: '',
    chapterNum: 0,
    volumeNum: 1,
    stepsResult: null,
    currentContent: '',
    batchChapters: [],
    batchRunning: false,
    batchCancelled: false,
    abortCtrl: null,
};

// ===== 初始化 =====
window.addEventListener('DOMContentLoaded', function() { peLoadProjects(); });

// ===== 项目加载 =====
async function peLoadProjects() {
    try {
        var resp = await fetch('/api/v1/projects');
        var data = await resp.json();
        var html = '<option value="">选择项目...</option>';
        (data || []).forEach(function(p) {
            html += '<option value="' + p.project_id + '">' + esc(p.title || p.project_id) + '</option>';
        });
        document.getElementById('pe-project').innerHTML = html;
    } catch(e) {}
}

async function peLoadProject(pid) {
    peState.projectId = pid;
    if (!pid) return;
    var outlineData = null, chList = null;
    try {
        var results = await Promise.allSettled([
            fetch('/api/v1/projects/' + pid + '/outline').then(function(r) { return r.json(); }),
            fetch('/api/v1/projects/' + pid + '/chapters/list').then(function(r) { return r.json(); })
        ]);
        if (results[0].status === 'fulfilled') outlineData = results[0].value;
        if (results[1].status === 'fulfilled') chList = results[1].value;
    } catch(e) {}

    var completedKeys = new Set();
    if (chList) {
        // 只要章节出现在列表中，就说明已生成（list_chapters API 不含正文，但会返回文件系统中的章节）
        (chList || []).forEach(function(c) {
            completedKeys.add((c.volume_number || 1) + '_' + c.chapter_number);
        });
    }

    var chapters = [];
    if (outlineData && outlineData.volumes) {
        (outlineData.volumes || []).forEach(function(vol) {
            (vol.chapters || []).forEach(function(ch) {
                var key = (vol.volume_number || 1) + '_' + ch.chapter_number;
                chapters.push({
                    chapter_number: ch.chapter_number,
                    title: ch.title || '',
                    volume_number: vol.volume_number || 1,
                    status: completedKeys.has(key) ? 'completed' : 'planned'
                });
            });
        });
    } else if (chList) {
        chapters = (chList || []).map(function(ch) {
            return { chapter_number: ch.chapter_number, title: ch.title || '', volume_number: ch.volume_number || 1, status: ch.content ? 'completed' : 'pending' };
        });
    }
    peState.batchChapters = chapters;
    // 更新单章下拉
    var chSel = document.getElementById('pe-chapter');
    chSel.innerHTML = '<option value="">选择章节...</option>';
    chapters.forEach(function(ch) {
        if (ch.status === 'completed') {
            chSel.innerHTML += '<option value="' + ch.chapter_number + '" data-vol="' + ch.volume_number + '">' + ch.chapter_number + '. ' + esc(ch.title) + '</option>';
        }
    });
    // 更新批量列表
    peRenderBatchList();
    peUpdateRange();
    // 异步加载优化状态
    peLoadAllOptStatus();
}

// ===== 范围切换 =====
function peUpdateRange() {
    var range = document.getElementById('pe-range').value;
    document.getElementById('pe-single-row').style.display = range === 'single' ? 'flex' : 'none';
    document.getElementById('pe-batch-panel').style.display = range !== 'single' ? 'block' : 'none';
    if (range !== 'single') peRenderBatchList();
}

// ===== 批量章节列表 =====
function peRenderBatchList() {
    var container = document.getElementById('pe-batch-list');
    var html = '';
    var count = 0;
    peState.batchChapters.forEach(function(ch, i) {
        if (ch.status !== 'completed') return;
        count++;
        var optLabel = ch._optTime ? '<span style="font-size:10px;color:var(--ok);margin-left:auto;cursor:pointer" onclick="event.stopPropagation();peViewChOptResult(' + ch.chapter_number + ',' + (ch.volume_number || 1) + ')">✅ ' + ch._optTime + '</span>' : '';
        html += '<label class="pe-ch-item"><input type="checkbox" class="pe-ch-cb" data-idx="' + i + '" checked onchange="peUpdateBatchCount()"><span class="ch-name" style="cursor:pointer" onclick="event.stopPropagation();peViewChOptResult(' + ch.chapter_number + ',' + (ch.volume_number || 1) + ')">' + ch.chapter_number + '. ' + esc(ch.title) + '</span>' + optLabel + '</label>';
    });
    container.innerHTML = count === 0 ? '<p class="muted" style="padding:12px;text-align:center">暂无已生成的章节</p>' : html;
    peUpdateBatchCount();
}

// ===== 加载所有章节的优化状态（异步，不阻塞渲染） =====
async function peLoadAllOptStatus() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) return;
    var tasks = peState.batchChapters.map(function(ch, i) {
        if (ch.status !== 'completed') return Promise.resolve(null);
        return fetch('/api/v1/projects/' + pid + '/pipeline/result/' + ch.chapter_number + '?volume=' + (ch.volume_number || 1))
            .then(function(r) { return r.json(); })
            .then(function(d) { return d.found ? {idx: i, time: d.created_at} : null; })
            .catch(function() { return null; });
    });
    var results = await Promise.all(tasks);
    results.forEach(function(r) {
        if (r && peState.batchChapters[r.idx]) {
            peState.batchChapters[r.idx]._optTime = r.time ? r.time.replace(/^\d{4}-/, '').substring(0, 14) : '';
        }
    });
    peRenderBatchList(); // 更新显示
}
function peUpdateBatchCount() {
    document.getElementById('pe-batch-sel-count').textContent = document.querySelectorAll('.pe-ch-cb:checked').length;
}
function peBatchSelectAll() { document.querySelectorAll('.pe-ch-cb').forEach(function(c) { c.checked = true; }); peUpdateBatchCount(); }
function peBatchSelectNone() { document.querySelectorAll('.pe-ch-cb').forEach(function(c) { c.checked = false; }); peUpdateBatchCount(); }

// ===== 选择章节时自动加载历史优化结果 =====
async function peLoadOptResult() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    var chSel = document.getElementById('pe-chapter');
    var chNum = parseInt(chSel.value) || 0;
    var opt = chSel.options[chSel.selectedIndex];
    var volNum = opt ? parseInt(opt.getAttribute('data-vol')) || 1 : 1;
    if (!pid || !chNum) return;
    await _loadAndShowOptResult(pid, chNum, volNum);
}

// ===== 批量模式点击查看某章优化结果 =====
async function peViewChOptResult(chNum, volNum) {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) return;
    await _loadAndShowOptResult(pid, chNum, volNum);
}

// ===== 通用：加载并展示优化结果 =====
async function _loadAndShowOptResult(pid, chNum, volNum) {
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/result/' + chNum + '?volume=' + volNum);
        var data = await resp.json();
        if (data.found && data.optimized) {
            peState.chapterNum = chNum;
            peState.volumeNum = volNum;
            peState.currentContent = data.optimized;
            peState.stepsResult = { original: data.original, current_content: data.optimized, explanation: data.explanation };
            peShowResult(peState.stepsResult);
            document.getElementById('pe-status').innerHTML = '<span style="color:var(--ok)">✅ 已加载第' + chNum + '章优化结果 (' + (data.created_at || '') + ')</span>';
        } else {
            document.getElementById('pe-status').innerHTML = '<span style="color:var(--text-muted)">第' + chNum + '章暂无优化结果</span>';
        }
    } catch(e) {}
}

// ===== 启动优化 =====
async function peStart() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) { toast('请选择项目', 'error'); return; }
    var range = document.getElementById('pe-range').value;
    if (range === 'single') {
        await peStartSingle(pid);
    } else {
        await peStartBatch(pid, range);
    }
}

// ===== 单章优化 =====
async function peStartSingle(pid) {
    var chSel = document.getElementById('pe-chapter');
    var chNum = parseInt(chSel.value) || 0;
    var opt = chSel.options[chSel.selectedIndex];
    var volNum = opt ? parseInt(opt.getAttribute('data-vol')) || 1 : 1;
    if (!chNum) { toast('请选择章节', 'error'); return; }

    peState.chapterNum = chNum;
    peState.volumeNum = volNum;
    peSetRunning(true, '正在优化第' + chNum + '章（可能需要1-3分钟）...');

    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/optimize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ chapter_num: chNum, volume_number: volNum }),
            signal: peAbortSignal(),
        });
        if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '请求失败'); }
        var result = await resp.json();
        peShowResult(result);
        toast('优化完成', 'success');
    } catch(e) {
        if (e.name === 'AbortError') { toast('已取消', 'info'); }
        else { toast('优化失败: ' + e.message, 'error'); }
    } finally { peSetRunning(false); }
}

// ===== 批量优化 =====
async function peStartBatch(pid, range) {
    var selected = [];
    if (range === 'all') {
        selected = peState.batchChapters.filter(function(ch) { return ch.status === 'completed'; });
    } else {
        document.querySelectorAll('.pe-ch-cb:checked').forEach(function(cb) {
            var idx = parseInt(cb.getAttribute('data-idx'));
            if (peState.batchChapters[idx] && peState.batchChapters[idx].status === 'completed') {
                selected.push(peState.batchChapters[idx]);
            }
        });
    }
    if (selected.length === 0) { toast('没有可优化的章节', 'error'); return; }

    peState.batchRunning = true;
    peState.batchCancelled = false;
    peSetRunning(true, '批量优化中...');
    document.getElementById('pe-batch-prog').style.display = 'block';

    var total = selected.length, done = 0, failed = 0;
    var itemsHtml = '';
    var lastResult = null;

    for (var i = 0; i < selected.length; i++) {
        if (peState.batchCancelled) break;
        var ch = selected[i];
        document.getElementById('pe-batch-prog-text').textContent = done + '/' + total;
        document.getElementById('pe-batch-bar').style.width = Math.round(done / total * 100) + '%';

        itemsHtml += '<div class="pe-batch-item" id="pe-bi-' + i + '"><span>⏳</span><span style="flex:1">' + ch.chapter_number + '. ' + esc(ch.title) + '</span><span style="color:var(--text-muted)">执行中...</span></div>';
        document.getElementById('pe-batch-items').innerHTML = itemsHtml;

        peState.abortCtrl = new AbortController();
        try {
            var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/optimize', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chapter_num: ch.chapter_number, volume_number: ch.volume_number || 1 }),
                signal: peState.abortCtrl.signal,
            });
            if (!resp.ok) throw new Error('请求失败');
            var result = await resp.json();
            done++; lastResult = result;
            var item = document.getElementById('pe-bi-' + i);
            if (item) {
                var score = '';
                if (result.explanation && result.explanation.quality_after) {
                    score = result.explanation.quality_after.grade + ' ' + result.explanation.quality_after.score + '分';
                }
                item.querySelector('span:first-child').textContent = '✅';
                item.querySelector('span:last-child').textContent = score || '完成';
            }
        } catch(e) {
            if (e.name === 'AbortError' || peState.batchCancelled) {
                var item = document.getElementById('pe-bi-' + i);
                if (item) { item.querySelector('span:first-child').textContent = '⏹'; item.querySelector('span:last-child').textContent = '已取消'; }
                break;
            }
            failed++; done++;
            var item = document.getElementById('pe-bi-' + i);
            if (item) { item.querySelector('span:first-child').textContent = '❌'; item.querySelector('span:last-child').textContent = '失败'; }
        }
    }

    peState.batchRunning = false;
    document.getElementById('pe-batch-prog-text').textContent = done + '/' + total;
    document.getElementById('pe-batch-bar').style.width = '100%';
    if (lastResult) peShowResult(lastResult);
    toast(peState.batchCancelled ? '已取消' : '批量完成: ' + done + '章, 失败' + failed + '章', peState.batchCancelled ? 'info' : 'success');
    peSetRunning(false);
}

// ===== 取消 =====
function peCancel() {
    peState.batchCancelled = true;
    if (peState.abortCtrl) peState.abortCtrl.abort();
}

// ===== UI 状态 =====
function peSetRunning(running, msg) {
    document.getElementById('btn-pe-start').disabled = running;
    document.getElementById('btn-pe-cancel').style.display = running ? 'inline-block' : 'none';
    var overlay = document.getElementById('pe-overlay');
    if (running) {
        overlay.classList.add('show');
        if (msg) document.getElementById('pe-overlay-msg').textContent = msg;
        document.getElementById('pe-status').innerHTML = '<span style="color:var(--accent)">⏳ 执行中...</span>';
    } else {
        overlay.classList.remove('show');
        document.getElementById('pe-status').textContent = '';
    }
}
function peAbortSignal() {
    peState.abortCtrl = new AbortController();
    // 5分钟超时
    setTimeout(function() { if (peState.abortCtrl) peState.abortCtrl.abort(); }, 300000);
    return peState.abortCtrl.signal;
}

// ===== 结果展示 =====
function peShowResult(result) {
    peState.stepsResult = result;
    peState.currentContent = result.current_content || '';
    var original = result.original || '';
    var optimized = result.current_content || '';

    document.getElementById('pe-compare-area').style.display = 'block';

    // 原文栏（纯文本）
    document.getElementById('pe-col-orig').innerHTML = '<div style="white-space:pre-wrap">' + esc(original) + '</div>';
    document.getElementById('pe-orig-count').textContent = original.length + ' 字';
    document.getElementById('pe-opt-count').textContent = optimized.length + ' 字';

    // 优化版栏（inline diff）
    peRenderInlineDiff(original, optimized);

    // 解释面板
    if (result.explanation) {
        peRenderExplanation(result.explanation);
    } else {
        document.getElementById('pe-explain').classList.remove('show');
    }
}

// ===== Inline Diff 渲染 =====
function peRenderInlineDiff(original, optimized) {
    var origParas = original.split(/\n\n+/);
    var optParas = optimized.split(/\n\n+/);
    var maxLen = Math.max(origParas.length, optParas.length);
    var html = '';

    for (var i = 0; i < maxLen; i++) {
        var op = (origParas[i] || '').trim();
        var np = (optParas[i] || '').trim();

        if (op === np) {
            // 无变化
            html += '<div class="diff-para">' + esc(op) + '</div>';
        } else if (!op && np) {
            // 新增段落
            html += '<div class="diff-para changed"><span class="diff-label">新增段落</span><span class="diff-line-add">' + esc(np) + '</span></div>';
        } else if (op && !np) {
            // 删除段落
            html += '<div class="diff-para changed"><span class="diff-label">删除段落</span><span class="diff-line-del">' + esc(op) + '</span></div>';
        } else {
            // 修改段落 — 逐字符 diff
            html += '<div class="diff-para changed"><span class="diff-label">段落 ' + (i + 1) + ' 已修改</span>';
            html += peCharDiff(op, np);
            html += '</div>';
        }
    }
    document.getElementById('pe-col-opt').innerHTML = html;
}

// 逐字符 diff 算法
function peCharDiff(oldStr, newStr) {
    // 找到公共前缀
    var prefixLen = 0;
    while (prefixLen < oldStr.length && prefixLen < newStr.length && oldStr[prefixLen] === newStr[prefixLen]) prefixLen++;
    // 找到公共后缀
    var suffixLen = 0;
    while (suffixLen < oldStr.length - prefixLen && suffixLen < newStr.length - prefixLen && oldStr[oldStr.length - 1 - suffixLen] === newStr[newStr.length - 1 - suffixLen]) suffixLen++;

    var oldMid = oldStr.substring(prefixLen, oldStr.length - suffixLen);
    var newMid = newStr.substring(prefixLen, newStr.length - suffixLen);
    var prefix = oldStr.substring(0, prefixLen);
    var suffix = oldStr.substring(oldStr.length - suffixLen);

    var html = '';
    if (prefix) html += esc(prefix);
    if (oldMid) html += '<span class="diff-char-del">' + esc(oldMid) + '</span>';
    if (newMid) html += '<span class="diff-char-add">' + esc(newMid) + '</span>';
    if (suffix) html += esc(suffix);
    return html || esc(newStr);
}

// ===== 解释面板渲染 =====
function peRenderExplanation(exp) {
    var panel = document.getElementById('pe-explain');
    var body = document.getElementById('pe-explain-body');
    panel.classList.add('show');

    var html = '';

    // 总结
    if (exp.summary) {
        html += '<div class="pe-explain-section"><h4>📋 优化概述</h4><p>' + esc(exp.summary) + '</p></div>';
    }

    // 质量评分对比
    if (exp.quality_before || exp.quality_after) {
        html += '<div class="pe-explain-section"><h4>📊 质量评分</h4><div style="display:flex;gap:16px;align-items:center">';
        if (exp.quality_before) {
            html += '<div><span class="pe-score-badge pe-score-before">' + (exp.quality_before.grade || '?') + ' ' + (exp.quality_before.score || 0) + '分</span><span style="font-size:11px;color:var(--text-muted);margin-left:6px">优化前</span></div>';
        }
        html += '<span style="font-size:20px">→</span>';
        if (exp.quality_after) {
            html += '<div><span class="pe-score-badge pe-score-after">' + (exp.quality_after.grade || '?') + ' ' + (exp.quality_after.score || 0) + '分</span><span style="font-size:11px;color:var(--text-muted);margin-left:6px">优化后</span></div>';
        }
        html += '</div>';
        // 问题列表
        if (exp.quality_before && exp.quality_before.issues && exp.quality_before.issues.length > 0) {
            html += '<div style="margin-top:8px;font-size:12px"><strong>发现的问题：</strong><ul style="margin:4px 0 0 16px">';
            exp.quality_before.issues.forEach(function(iss) { html += '<li>' + esc(iss) + '</li>'; });
            html += '</ul></div>';
        }
        if (exp.quality_after && exp.quality_after.improvements && exp.quality_after.improvements.length > 0) {
            html += '<div style="margin-top:8px;font-size:12px"><strong>改进效果：</strong><ul style="margin:4px 0 0 16px">';
            exp.quality_after.improvements.forEach(function(imp) { html += '<li>' + esc(imp) + '</li>'; });
            html += '</ul></div>';
        }
        html += '</div>';
    }

    // 逐条修改解释
    if (exp.changes && exp.changes.length > 0) {
        html += '<div class="pe-explain-section"><h4>✏️ 修改详情</h4>';
        exp.changes.forEach(function(ch) {
            var typeLabel = {ai_taste:'AI味',writing:'文笔',consistency:'一致性',style:'风格',logic:'逻辑'}[ch.type] || ch.type || '';
            html += '<div class="pe-change-card">';
            if (typeLabel) html += '<span class="change-type">' + typeLabel + '</span>';
            html += '<div style="margin-top:6px"><span style="color:var(--bad);text-decoration:line-through">' + esc(ch.original_snippet || '') + '</span></div>';
            html += '<div style="margin-top:2px"><span style="color:var(--ok)">' + esc(ch.optimized_snippet || '') + '</span></div>';
            if (ch.reason) html += '<div style="margin-top:6px;color:var(--text-muted);font-size:11px">💡 ' + esc(ch.reason) + '</div>';
            html += '</div>';
        });
        html += '</div>';
    }

    // 整体对比
    if (exp.comparison) {
        html += '<div class="pe-explain-section"><h4>🔍 版本对比</h4><p>' + esc(exp.comparison) + '</p></div>';
    }

    // 建议
    if (exp.recommendation) {
        html += '<div class="pe-explain-section"><h4>💡 建议</h4><p style="padding:8px 12px;background:rgba(124,92,252,0.06);border-radius:6px;border-left:3px solid var(--accent)">' + esc(exp.recommendation) + '</p></div>';
    }

    body.innerHTML = html || '<p class="muted">暂无解释信息</p>';
}

// ===== 保存/复制 =====
async function peSaveResult() {
    var pid = peState.projectId;
    var chNum = peState.chapterNum;
    var content = peState.currentContent;
    if (!pid || !chNum) { toast('请先选择项目和章节', 'error'); return; }
    if (!content) { toast('没有可保存的内容', 'error'); return; }
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ chapter_num: chNum, volume_number: peState.volumeNum || 1, content: content })
        });
        if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '保存失败'); }
        var data = await resp.json();
        toast('已保存，' + (data.word_count || 0) + '字', 'success');
    } catch(e) { toast('保存失败: ' + e.message, 'error'); }
}

function peCopyResult() {
    if (!peState.currentContent) { toast('没有可复制的内容', 'error'); return; }
    navigator.clipboard.writeText(peState.currentContent).then(function() {
        toast('已复制到剪贴板', 'success');
    }).catch(function() { toast('复制失败', 'error'); });
}

// ===== 工具函数 =====
function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
