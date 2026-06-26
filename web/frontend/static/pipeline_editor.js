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

// ===== 已生成章节列表（只读，点击查看各章优化结果）=====
function peRenderBatchList() {
    var container = document.getElementById('pe-batch-list');
    var html = '';
    var count = 0;
    peState.batchChapters.forEach(function(ch) {
        if (ch.status !== 'completed') return;
        count++;
        var optLabel = ch._optTime ? '<span style="font-size:10px;color:var(--ok);margin-left:auto">✅ ' + ch._optTime + '</span>' : '';
        var active = (peState.chapterNum === ch.chapter_number && peState.volumeNum === (ch.volume_number || 1)) ? ' pe-ch-current' : '';
        html += '<div class="pe-ch-item' + active + '" style="cursor:pointer" onclick="peViewChOptResult(' + ch.chapter_number + ',' + (ch.volume_number || 1) + ')"><span class="ch-name">' + ch.chapter_number + '. ' + esc(ch.title) + '</span>' + optLabel + '</div>';
    });
    container.innerHTML = count === 0 ? '<p class="muted" style="padding:12px;text-align:center">暂无已生成的章节</p>' : html;
    document.getElementById('pe-batch-sel-count').textContent = count;
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
    peSetRunning(true, '优化中，请耐心等待（约1-3分钟）...');

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

// ===== 整个项目检测（全部已生成章节）=====
async function peStartBatch(pid, range) {
    var selected = peState.batchChapters.filter(function(ch) { return ch.status === 'completed'; });
    if (selected.length === 0) { toast('没有已生成的章节可检测', 'error'); return; }

    peState.batchRunning = true;
    peState.batchCancelled = false;
    peSetRunning(true, '正在检测整个项目，共' + selected.length + '章（约' + selected.length * 2 + '-' + selected.length * 3 + '分钟）...');
    document.getElementById('pe-batch-prog').style.display = 'block';

    var total = selected.length, done = 0, failed = 0;
    var itemsHtml = '';
    var lastResult = null;

    for (var i = 0; i < selected.length; i++) {
        if (peState.batchCancelled) break;
        var ch = selected[i];
        document.getElementById('pe-batch-prog-text').textContent = done + '/' + total;
        document.getElementById('pe-batch-bar').style.width = Math.round(done / total * 100) + '%';

        itemsHtml += '<div class="pe-batch-item" id="pe-bi-' + i + '" style="cursor:pointer" onclick="peViewChOptResult(' + ch.chapter_number + ',' + (ch.volume_number || 1) + ')"><span>⏳</span><span style="flex:1">' + ch.chapter_number + '. ' + esc(ch.title) + '</span><span style="color:var(--text-muted)">执行中...</span></div>';
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
            result._chapterNum = ch.chapter_number;
            result._volumeNum = ch.volume_number || 1;
            result._chapterTitle = ch.title || '';
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
    toast(peState.batchCancelled ? '已取消' : '整个项目检测完成: ' + done + '章, 失败' + failed + '章', peState.batchCancelled ? 'info' : 'success');
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

// ===== 对比区表头：显示当前展示的是哪一章 =====
function peUpdateCompareHeader(title) {
    // 未传标题时，从已生成章节列表里查
    if (!title && peState.chapterNum && peState.batchChapters) {
        var hit = peState.batchChapters.find(function(c) {
            return c.chapter_number === peState.chapterNum && (c.volume_number || 1) === (peState.volumeNum || 1);
        });
        if (hit) title = hit.title;
    }
    var label = peState.chapterNum ? ('· 第' + peState.chapterNum + '章' + (title ? ' ' + esc(title) : '')) : '';
    var oh = document.getElementById('pe-orig-title');
    var nh = document.getElementById('pe-opt-title');
    if (oh) oh.textContent = label;
    if (nh) nh.textContent = label;
    // 同步左侧章节列表高亮
    if (typeof peRenderBatchList === 'function' && document.getElementById('pe-batch-panel').style.display !== 'none') {
        peRenderBatchList();
    }
}

// ===== 结果展示 =====
function peShowResult(result) {
    peState.stepsResult = result;
    peState.currentContent = result.current_content || '';
    var original = result.original || '';
    var optimized = result.current_content || '';

    // 记录当前展示的章节（批量结果带 _chapterNum；单章走 peState 已有值）
    if (result._chapterNum) {
        peState.chapterNum = result._chapterNum;
        peState.volumeNum = result._volumeNum || 1;
    }
    peUpdateCompareHeader(result._chapterTitle);

    document.getElementById('pe-compare-area').style.display = 'block';
    // 显示朱雀面板（重置回填状态）
    var zqPanel = document.getElementById('pe-zhuque');
    if (zqPanel) {
        zqPanel.style.display = 'block';
        document.getElementById('pe-zq-rate').value = '';
        document.getElementById('pe-zq-result').style.display = 'none';
    }
    document.getElementById('pe-orig-count').textContent = original.length + ' 字';
    document.getElementById('pe-opt-count').textContent = optimized.length + ' 字';

    // 段落级批注映射：paragraph_index -> change（来自实测报告，已带 paragraph_index）
    var annotMap = {};
    if (result.explanation && result.explanation.changes) {
        result.explanation.changes.forEach(function(ch) {
            if (ch.paragraph_index !== null && ch.paragraph_index !== undefined) {
                annotMap[ch.paragraph_index] = ch;
            }
        });
    }

    // 双栏按段落索引对齐渲染
    peRenderAlignedColumns(original, optimized, annotMap);

    // 解释面板
    if (result.explanation) {
        peRenderExplanation(result.explanation);
    } else {
        document.getElementById('pe-explain').classList.remove('show');
    }
}

// ===== 双栏对齐渲染（原文 / 优化版，同索引 data-pi，点击互相导航）=====
function peRenderAlignedColumns(original, optimized, annotMap) {
    var origParas = original.split(/\n\n+/);
    var optParas = optimized.split(/\n\n+/);
    var maxLen = Math.max(origParas.length, optParas.length);
    var origHtml = '', optHtml = '';

    for (var i = 0; i < maxLen; i++) {
        var op = (origParas[i] || '').trim();
        var np = (optParas[i] || '').trim();
        var changed = op !== np;
        var cls = 'pe-para' + (changed ? ' changed' : '');

        // 原文栏
        origHtml += '<div class="' + cls + '" data-pi="' + i + '" onclick="peNavTo(' + i + ')">' + (esc(op) || '&nbsp;') + '</div>';

        // 优化版栏：正文（字符 diff）+ 内联批注（独立节点，不进正文）
        var bodyHtml;
        if (!changed) {
            bodyHtml = esc(np) || '&nbsp;';
        } else if (!op && np) {
            bodyHtml = '<span class="diff-label">新增段落</span><span class="diff-line-add">' + esc(np) + '</span>';
        } else if (op && !np) {
            bodyHtml = '<span class="diff-label">删除段落</span><span class="diff-line-del">' + esc(op) + '</span>';
        } else {
            bodyHtml = peCharDiff(op, np);
        }
        optHtml += '<div class="' + cls + '" data-pi="' + i + '" onclick="peNavTo(' + i + ')">';
        optHtml += '<div class="pe-para-body">' + bodyHtml + '</div>';
        if (changed && annotMap[i]) {
            optHtml += peAnnotHtml(annotMap[i]);
        }
        optHtml += '</div>';
    }
    document.getElementById('pe-col-orig').innerHTML = origHtml;
    document.getElementById('pe-col-opt').innerHTML = optHtml;
}

// 内联批注节点（独立 DOM，复制/保存读 state 不受影响）
function peAnnotHtml(ch) {
    var typeLabel = {ai_taste:'AI味',writing:'文笔',consistency:'一致性',style:'风格',logic:'逻辑'}[ch.type] || ch.type || '修改';
    var html = '<div class="pe-annot" contenteditable="false">';
    html += '<span class="pe-annot-tag">✏️ ' + esc(typeLabel) + '</span>';
    if (ch.evidence_source) html += '<span class="pe-annot-src">📌 ' + esc(ch.evidence_source) + '</span>';
    if (ch.reason) html += '<div class="pe-annot-reason">' + esc(ch.reason) + '</div>';
    html += '</div>';
    return html;
}

// 点击段落 → 两栏同时高亮 + 另一栏滚动到对应段
function peNavTo(pi) {
    var both = document.querySelectorAll('#pe-col-orig .pe-para, #pe-col-opt .pe-para');
    both.forEach(function(el) { el.classList.remove('pe-para-active'); });
    var targets = document.querySelectorAll('.pe-para[data-pi="' + pi + '"]');
    targets.forEach(function(el) {
        el.classList.add('pe-para-active');
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
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
    panel.classList.remove('collapsed');  // 新结果默认展开
    var tbtn = panel.querySelector('.pe-explain-header .btn');
    if (tbtn) tbtn.textContent = '收起';

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
            if (ch.evidence_source) html += '<span class="change-type" style="background:rgba(124,92,252,0.12);color:var(--accent);margin-left:4px">📌 ' + esc(ch.evidence_source) + '</span>';
            html += '<div style="margin-top:6px"><span style="color:var(--bad);text-decoration:line-through">' + esc(ch.original_snippet || '') + '</span></div>';
            html += '<div style="margin-top:2px"><span style="color:var(--ok)">' + esc(ch.optimized_snippet || '') + '</span></div>';
            if (ch.reason) html += '<div style="margin-top:6px;color:var(--text-muted);font-size:11px">💡 依据: ' + esc(ch.reason) + '</div>';
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

// 收起/展开：只折叠 body，保留 header（修复点击后整个面板消失）
function peToggleExplain(btn) {
    var panel = document.getElementById('pe-explain');
    panel.classList.toggle('collapsed');
    if (btn) btn.textContent = panel.classList.contains('collapsed') ? '展开' : '收起';
}

// ===== 保存/复制/应用 =====
async function peSaveResult() {
    var pid = peState.projectId;
    var chNum = peState.chapterNum;
    var content = peState.currentContent;  // 纯优化正文，不含批注
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

// 应用：单章模式应用当前章；批量/整个项目模式应用全部已优化章节。均为纯正文，不含批注。
async function peApplyResult() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) { toast('请先选择项目', 'error'); return; }
    var range = document.getElementById('pe-range').value;

    if (range === 'single') {
        // 单章：直接把当前优化正文写回章节
        if (!peState.chapterNum || !peState.currentContent) { toast('没有可应用的内容', 'error'); return; }
        if (!confirm('确定把优化版应用到第' + peState.chapterNum + '章？原内容会自动快照备份。')) return;
        try {
            var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/save', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chapter_num: peState.chapterNum, volume_number: peState.volumeNum || 1, content: peState.currentContent })
            });
            if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '应用失败'); }
            toast('已应用到第' + peState.chapterNum + '章', 'success');
        } catch(e) { toast('应用失败: ' + e.message, 'error'); }
        return;
    }

    // 批量 / 整个项目：应用所有已保存的优化结果到对应章节
    if (!confirm('确定把所有已优化章节应用到项目？每章原内容会自动快照备份。')) return;
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/apply-all', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})
        });
        if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '应用失败'); }
        var data = await resp.json();
        if (data.status === 'noop') { toast(data.message || '没有可应用的优化结果', 'info'); }
        else { toast('已应用 ' + data.applied + ' 章' + (data.skipped ? '，跳过 ' + data.skipped + ' 章' : ''), 'success'); }
    } catch(e) { toast('应用失败: ' + e.message, 'error'); }
}

function peCopyResult() {
    if (!peState.currentContent) { toast('没有可复制的内容', 'error'); return; }
    navigator.clipboard.writeText(peState.currentContent).then(function() {  // 纯正文，无批注
        toast('已复制到剪贴板', 'success');
    }).catch(function() { toast('复制失败', 'error'); });
}

// ===== 工具函数 =====
function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

// ===== 朱雀终检 =====

function peToggleZhuque(btn) {
    var panel = document.getElementById('pe-zhuque');
    panel.classList.toggle('collapsed');
}

// 导出文本供朱雀检测：单章用当前内容；整个项目模式从服务器拉合并文本
async function peZhuqueExport() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    var range = document.getElementById('pe-range').value;
    var text = '', filename = '';

    if (range === 'all' && pid) {
        try {
            var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/export-text');
            if (!resp.ok) throw new Error('获取失败');
            var data = await resp.json();
            text = data.text || '';
            filename = 'project_optimized_' + pid.slice(0, 8) + '.txt';
            if (!text) { toast('暂无已优化章节可导出', 'info'); return; }
        } catch(e) { toast('导出失败: ' + e.message, 'error'); return; }
    } else {
        text = peState.currentContent;
        if (!text) { toast('请先优化一章', 'error'); return; }
        filename = 'ch' + (peState.chapterNum || '0') + '_optimized.txt';
    }

    var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    toast('已导出 ' + filename, 'success');
}

// 回填朱雀AI率并触发针对性降重
async function peZhuqueReduce() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    var rateStr = document.getElementById('pe-zq-rate').value.trim();
    if (!rateStr) { toast('请先填入朱雀检测到的 AI 率', 'error'); return; }
    var rate = parseFloat(rateStr);
    if (isNaN(rate) || rate < 0 || rate > 100) { toast('AI 率应为 0-100 之间的数字', 'error'); return; }
    if (!peState.currentContent) { toast('请先优化一章', 'error'); return; }
    if (!pid) { toast('请先选择项目', 'error'); return; }

    var btn = document.querySelector('#pe-zhuque .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '降重中...'; }

    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/zhuque-reduce', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: peState.currentContent,
                zhuque_ai_rate: rate,
                chapter_num: peState.chapterNum || 0,
                volume_number: peState.volumeNum || 1,
            }),
        });
        if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '请求失败'); }
        var data = await resp.json();

        var box = document.getElementById('pe-zq-result');
        box.style.display = 'block';
        var icon = data.changed ? '🔄' : '✅';
        var local_pct = Math.round((1 - data.local_human) * 100);
        var zq_pct = Math.round((1 - data.zhuque_human) * 100);
        var eff_pct = Math.round((1 - data.effective_human) * 100);
        box.innerHTML = icon + ' ' + esc(data.summary)
            + '<br><span style="color:var(--text-muted)">本地AI率 ' + local_pct + '% · 朱雀AI率 ' + zq_pct + '% · 有效判据 ' + eff_pct + '%</span>';

        if (data.changed && data.reduced_text) {
            peState.currentContent = data.reduced_text;
            // 用降重后的文本重新渲染对比区（以当前对比文本为原文，降重后为优化版）
            var annotMap = {};
            if (peState.stepsResult && peState.stepsResult.explanation && peState.stepsResult.explanation.changes) {
                peState.stepsResult.explanation.changes.forEach(function(ch) {
                    if (ch.paragraph_index !== null && ch.paragraph_index !== undefined) annotMap[ch.paragraph_index] = ch;
                });
            }
            peRenderAlignedColumns(document.getElementById('pe-col-orig').innerText, data.reduced_text, annotMap);
            document.getElementById('pe-opt-count').textContent = data.reduced_text.length + ' 字';
            toast('降重完成，可再次导出去朱雀验证', 'success');
        } else {
            toast(data.summary, 'info');
        }
    } catch(e) {
        toast('降重失败: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '应用降重'; }
    }
}

// 导入朱雀报告PDF：纯正则解析（后端不调LLM），自动按解析的AI率降重
async function peZhuqueImport(input) {
    var file = input.files && input.files[0];
    input.value = '';  // 允许重复选同一文件
    if (!file) return;
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) { toast('请先选择项目', 'error'); return; }
    if (!peState.chapterNum) { toast('请先选择/优化一章', 'error'); return; }

    var box = document.getElementById('pe-zq-result');
    box.style.display = 'block';
    box.innerHTML = '⏳ 正在解析报告并降重...';

    var fd = new FormData();
    fd.append('file', file);
    var qs = '?chapter_num=' + peState.chapterNum + '&volume_number=' + (peState.volumeNum || 1);

    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/import-zhuque-report' + qs, {
            method: 'POST', body: fd,
        });
        if (!resp.ok) { var e = await resp.json(); throw new Error(e.detail || '解析失败'); }
        var data = await resp.json();
        var rep = data.report || {};

        var html = '📄 ' + esc(data.summary || '解析完成');
        html += '<br><span style="color:var(--text-muted)">整体AI率 ' + Math.round((rep.overall_ai_rate || 0) * 100)
              + '% · 共' + (rep.segment_count || 0) + '个片段，其中' + (rep.ai_segment_count || 0) + '个疑似AI</span>';
        // 列出高AIGC片段
        if (rep.segments && rep.segments.length) {
            html += '<div style="margin-top:6px;max-height:120px;overflow:auto">';
            rep.segments.forEach(function(s) {
                if (!s.is_ai) return;
                var snippet = (s.text || '').slice(0, 40);
                html += '<div style="font-size:11px;color:var(--text-muted)">• 片段' + s.index + ' AIGC '
                      + Math.round(s.aigc * 100) + '%：' + esc(snippet) + (s.text && s.text.length > 40 ? '…' : '') + '</div>';
            });
            html += '</div>';
        }
        box.innerHTML = html;

        // 自动降重已执行 → 刷新对比区
        if (data.reduction && data.reduction.changed && data.reduction.reduced_text) {
            peState.currentContent = data.reduction.reduced_text;
            var annotMap = {};
            if (peState.stepsResult && peState.stepsResult.explanation && peState.stepsResult.explanation.changes) {
                peState.stepsResult.explanation.changes.forEach(function(ch) {
                    if (ch.paragraph_index !== null && ch.paragraph_index !== undefined) annotMap[ch.paragraph_index] = ch;
                });
            }
            peRenderAlignedColumns(document.getElementById('pe-col-orig').innerText, data.reduction.reduced_text, annotMap);
            document.getElementById('pe-opt-count').textContent = data.reduction.reduced_text.length + ' 字';
            toast('报告已解析，降重完成', 'success');
        } else {
            toast('报告已解析', 'success');
        }
    } catch(e) {
        box.innerHTML = '❌ ' + esc(e.message);
        toast('导入失败: ' + e.message, 'error');
    }
}
