// ===== workbench.js — 创作工作台 =====

var PLATFORM_MAP = {fanqie:'番茄小说', qidian:'起点中文网', jinjiang:'晋江文学城', qimao:'七猫小说', douban:'豆瓣阅读'};
var LENGTH_MAP = {short:'短篇', medium:'中篇', long:'长篇'};
var currentProjectId = sessionStorage.getItem('wb_pid') || '';
var currentChapterNum = parseInt(sessionStorage.getItem('wb_ch') || '0');
var currentVolumeNum = parseInt(sessionStorage.getItem('wb_vol') || '1');
var outlineData = null;
var _chapterAbortCtrl = null;
var _isGeneratingChapter = false;
var _batchActive = false;
var _batchCancelled = false;
var _activeStreams = {};  // {genKey: {vol, ch, abortCtrl, statusDiv, bufferedText, done, error}}
var _outlineVersions = [];
var _incubatedDirections = [];
var _styleBible = '';
var _editing = false;
var _rewriteRange = null;

// ===== 生成状态持久化 =====
function _genKey(pid, vol, ch) { return pid + '|' + vol + '|' + ch; }
function _saveGenerating(pid, vol, ch) {
    var k = _genKey(pid, vol, ch);
    var s = JSON.parse(sessionStorage.getItem('wb_generating') || '{}');
    s[k] = Date.now();
    sessionStorage.setItem('wb_generating', JSON.stringify(s));
}
function _clearGenerating(pid, vol, ch) {
    var k = _genKey(pid, vol, ch);
    var s = JSON.parse(sessionStorage.getItem('wb_generating') || '{}');
    delete s[k];
    sessionStorage.setItem('wb_generating', JSON.stringify(s));
}
function _getGenerating() { return JSON.parse(sessionStorage.getItem('wb_generating') || '{}'); }

function _saveBatchState(pid, pending, idx, done, failed) {
    sessionStorage.setItem('wb_batch', JSON.stringify({pid: pid, pending: pending, idx: idx, done: done, failed: failed, ts: Date.now()}));
}
function _loadBatchState() { try { return JSON.parse(sessionStorage.getItem('wb_batch') || 'null'); } catch(e) { return null; } }
function _clearBatchState() { sessionStorage.removeItem('wb_batch'); }

window.addEventListener('beforeunload', function() {
    sessionStorage.setItem('wb_pid', currentProjectId);
    sessionStorage.setItem('wb_ch', String(currentChapterNum));
    sessionStorage.setItem('wb_vol', String(currentVolumeNum));
});

// ===== 大纲版本持久化 =====
function _saveOutlineVersions(versions, pid) { sessionStorage.setItem('ol_versions_' + pid, JSON.stringify(versions)); }
function _loadOutlineVersions(pid) { try { return JSON.parse(sessionStorage.getItem('ol_versions_' + pid) || '[]'); } catch(e) { return []; } }
function _clearOutlineVersions(pid) { sessionStorage.removeItem('ol_versions_' + pid); }

// ===== 生成状态恢复 =====
async function _recoverGeneratingTasks() {
    var batch = _loadBatchState();
    if (batch && batch.pid && batch.pending && Date.now() - batch.ts < 30 * 60 * 1000) {
        if (batch.pid === currentProjectId) {
            var cur = batch.pending[batch.idx];
            var total = batch.pending.length;
            var remaining = total - batch.idx;
            var msg = '检测到中断的批量生成：已完成' + batch.done + '/' + total + '章，剩余' + remaining + '章待生成';
            if (cur) msg += '，将从第' + cur.vol + '卷第' + cur.ch + '章「' + (cur.title || '') + '」继续';
            toast(msg, 'info', 8000);
            if (batch.done > 0) {
                for (var i = 0; i < batch.idx; i++) { var p = batch.pending[i]; _markChapterDone(p.vol, p.ch); }
            }
            if (cur) { currentChapterNum = cur.ch; currentVolumeNum = cur.vol; _highlightBatchChapter(cur.vol, cur.ch); }
            await generateBatch(batch.idx, batch.pending);
            return;
        }
    } else if (batch) {
        _clearBatchState();
    }
    var gen = _getGenerating();
    var keys = Object.keys(gen);
    if (keys.length === 0) return;
    var now = Date.now();
    // 清理旧格式（_分隔）和超时条目
    keys.forEach(function(k) {
        if (now - gen[k] > 30 * 60 * 1000 || !k.includes('|')) delete gen[k];
    });
    sessionStorage.setItem('wb_generating', JSON.stringify(gen));
    keys = Object.keys(gen);
    if (keys.length === 0) return;
    // 先检查后端状态，过滤掉已完成的任务
    var trulyGenerating = [];
    for (var i = 0; i < keys.length; i++) {
        var parts = keys[i].split('|');
        if (parts.length < 3) continue;
        var pid = parts[0], vol = parseInt(parts[1]), ch = parseInt(parts[2]);
        try {
            var resp = await fetch('/api/v1/stream/chapter/status/' + pid + '/' + vol + '/' + ch);
            var st = await resp.json();
            if (st.status === 'saved') {
                _clearGenerating(pid, vol, ch);
                if (pid === currentProjectId) {
                    toast('第' + vol + '卷第' + ch + '章已生成完成 (' + (st.word_count || '') + '字)', 'success');
                    if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === vol) { v.chapters.forEach(function(c) { if (c.chapter_number === ch) c.status = 'completed'; }); } });
                    _renderOutlineTree();
                    if (vol === currentVolumeNum && ch === currentChapterNum) loadChapterContent(ch, vol);
                }
                continue;
            }
            if (st.status === 'idle') { _clearGenerating(pid, vol, ch); continue; }
        } catch(e) {}
        trulyGenerating.push(keys[i]);
    }
    if (trulyGenerating.length === 0) return;
    var details = [];
    trulyGenerating.forEach(function(k) {
        var parts = k.split('|');
        if (parts.length >= 3) details.push('第' + parts[1] + '卷第' + parts[2] + '章');
    });
    toast('检测到' + trulyGenerating.length + '个进行中的生成任务: ' + details.join('、') + '，正在恢复...', 'info', 8000);
    for (var i = 0; i < trulyGenerating.length; i++) {
        var parts = trulyGenerating[i].split('|');
        if (parts.length < 3) continue;
        await _reconnectChapterStream(parts[0], parseInt(parts[1]), parseInt(parts[2]));
    }
}

async function _reconnectChapterStream(pid, vol, ch) {
    try {
        var resp = await fetch('/api/v1/stream/chapter/status/' + pid + '/' + vol + '/' + ch);
        var st = await resp.json();
        if (st.status === 'saved') {
            _clearGenerating(pid, vol, ch);
            if (pid === currentProjectId) {
                toast('第' + vol + '卷第' + ch + '章已生成完成 (' + (st.word_count || '') + '字)', 'success');
                // 更新大纲数据并刷新
                if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === vol) { v.chapters.forEach(function(c) { if (c.chapter_number === ch) c.status = 'completed'; }); } });
                _renderOutlineTree();
                if (vol === currentVolumeNum && ch === currentChapterNum) loadChapterContent(ch, vol);
            }
            return;
        }
        if (st.status === 'idle') { _clearGenerating(pid, vol, ch); return; }
    } catch(e) {}
    if (pid !== currentProjectId) return;
    var ta = document.getElementById('chapter-content');
    if (vol === currentVolumeNum && ch === currentChapterNum) { ta.value = ''; ta.disabled = false; }
    try {
        var resp = await fetch('/api/v1/stream/chapter', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project_id: pid, chapter_number: ch, volume_number: vol})});
        var reader = resp.body.getReader(); var decoder = new TextDecoder(); var buffer = '';
        while (true) {
            var r = await reader.read(); if (r.done) break;
            buffer += decoder.decode(r.value, {stream: true});
            var lines = buffer.split('\n'); buffer = lines.pop() || '';
            for (var li = 0; li < lines.length; li++) {
                if (lines[li].startsWith('data: ')) {
                    try {
                        var d = JSON.parse(lines[li].slice(6));
                        if (d.token && vol === currentVolumeNum && ch === currentChapterNum) { ta.value += d.token; ta.scrollTop = ta.scrollHeight; updateWordCount(); }
                        if (d.status === 'saved') {
                            _clearGenerating(pid, vol, ch);
                            if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === vol) { v.chapters.forEach(function(c) { if (c.chapter_number === ch) c.status = 'completed'; }); } });
                            toast('第' + vol + '卷第' + ch + '章已生成完成', 'success');
                            _renderOutlineTree();
                        }
                        if (d.error) { _clearGenerating(pid, vol, ch); }
                    } catch(e) {}
                }
            }
        }
    } catch(e) { _clearGenerating(pid, vol, ch); }
}

// ===== Init =====
(async function init() {
    try {
        var resp = await fetch('/api/v1/projects');
        var projects = await resp.json();
        var sel = document.getElementById('project-select');
        projects.forEach(function(p) { sel.innerHTML += '<option value="' + p.project_id + '">' + p.title + '</option>'; });
    } catch(e) {}
    var params = new URLSearchParams(window.location.search);
    if (params.get('project_id')) { document.getElementById('project-select').value = params.get('project_id'); await loadProject(params.get('project_id')); }
    else if (currentProjectId) { document.getElementById('project-select').value = currentProjectId; await loadProject(currentProjectId); }
    await _recoverGeneratingTasks();
})();

// ===== Project =====
async function loadProject(pid) {
    if (!pid) return;
    currentProjectId = pid;
    // 先清空旧数据，避免显示残留
    document.getElementById('characters-mini').innerHTML = '<p class="muted">加载中...</p>';
    document.getElementById('outline-tree').innerHTML = '<p class="muted">加载中...</p>';
    document.getElementById('project-info').innerHTML = '<p class="muted">加载中...</p>';
    document.getElementById('chapter-content').value = '';
    document.getElementById('current-chapter-label').textContent = '选择章节';
    document.getElementById('word-count').textContent = '0 字';
    document.getElementById('btn-outline').disabled = false;
    document.getElementById('btn-batch').disabled = false;
    document.getElementById('btn-ol-versions').disabled = false;
    outlineData = null;
    currentChapterNum = 0;
    currentVolumeNum = 1;
    try {
        var resp = await fetch('/api/v1/projects/' + pid);
        var proj = await resp.json();
        var platName = PLATFORM_MAP[proj.platform] || proj.platform || '未知';
        var lenName = LENGTH_MAP[proj.length] || (proj.length || '中篇');
        var chNum = proj.current_chapter || 0;
        var chText = chNum > 0 ? '第' + chNum + '章' : '未开始';
        document.getElementById('project-info').innerHTML = '<div><strong>' + proj.title + '</strong></div><div class="muted">' + platName + ' | ' + lenName + ' | ' + chText + '</div><div style="margin-top:4px"><a href="/reader?project_id=' + pid + '" target="_blank" style="font-size:11px;color:var(--accent);text-decoration:none">📖 阅读模式</a></div>';
        window.history.replaceState({}, '', '?project_id=' + pid);
        await loadOutline(); await loadCharactersMini(); await loadStyleBible(pid); await loadMiniGraph();
        // 恢复 sessionStorage 中保存的大纲方案
        var savedVers = _loadOutlineVersions(pid);
        if (savedVers.length > 0) {
            _outlineVersions = savedVers;
            // 如果大纲树为空或显示"暂无"，显示恢复提示
            var tree = document.getElementById('outline-tree');
            var treeIsEmpty = !outlineData || !outlineData.volumes || outlineData.volumes.length === 0;
            if (treeIsEmpty) {
                tree.innerHTML = '<p class="muted">已有 ' + savedVers.length + ' 个待选方案（来自上次生成）</p>' +
                    '<button class="btn btn-sm btn-primary" style="margin-top:8px;width:100%" onclick="renderVersionCards(_outlineVersions)">📋 查看方案</button>';
            }
        }
        // 检查是否有进行中的大纲生成任务
        await _checkOutlineGenerationStatus(pid);
    } catch(e) { document.getElementById('project-info').innerHTML = '<p class="error">加载失败</p>'; }
}

// ===== Outline =====
async function loadOutline() {
    var tree = document.getElementById('outline-tree');
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline');
        outlineData = await resp.json();
    } catch(e) { outlineData = null; }
    if (!outlineData || !outlineData.volumes) { tree.innerHTML = '<p class="muted">暂无大纲</p>'; return; }
    try {
        var chResp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/list');
        var chList = await chResp.json();
        var completedKeys = new Set((chList || []).map(function(c) { return (c.volume_number || 1) + '_' + c.chapter_number; }));
        outlineData.volumes.forEach(function(v) { v.chapters.forEach(function(ch) { ch.status = completedKeys.has((v.volume_number || 1) + '_' + ch.chapter_number) ? 'completed' : 'planned'; }); });
    } catch(e) {}
    _renderOutlineTree();
}
// 纯渲染：从 outlineData 内存数据重建大纲树 DOM，不请求 API
function _renderOutlineTree() {
    var tree = document.getElementById('outline-tree');
    if (!outlineData || !outlineData.volumes) { tree.innerHTML = '<p class="muted">暂无大纲</p>'; return; }
    var html = '';
    outlineData.volumes.forEach(function(vol) {
        html += '<div class="volume-title" data-vol-title="' + vol.volume_number + '">📘 第' + vol.volume_number + '卷: ' + (vol.title || '') + '</div>';
        (vol.chapters || []).forEach(function(ch) {
            var star = ch.is_hook_point ? '⭐' : (ch.is_climax ? '⚡' : '');
            var active = ch.chapter_number === currentChapterNum ? 'active' : '';
            var statusIcon = ch.status === 'completed' ? '✅' : '⬜';
            html += '<div class="outline-chapter ' + active + '" draggable="true" onclick="selectChapter(' + ch.chapter_number + ',this,' + vol.volume_number + ')" data-title="' + (ch.title || '').replace(/"/g, '&quot;') + '" data-ch="' + ch.chapter_number + '" data-vol="' + vol.volume_number + '"><span class="ch-status-icon">' + statusIcon + '</span> ' + star + ' Ch' + ch.chapter_number + ' ' + (ch.title || '') + (ch.status === 'completed' ? ' <span class="ch-delete" onclick="event.stopPropagation();deleteChapter(' + vol.volume_number + ',' + ch.chapter_number + ')" title="删除此章">❌</span>' : '') + '</div>';
        });
    });
    tree.innerHTML = html;
    var totalChs = 0, doneChs = 0;
    outlineData.volumes.forEach(function(v) { totalChs += v.chapters.length; v.chapters.forEach(function(ch) { if (ch.status === 'completed') doneChs++; }); });
    document.getElementById('outline-info').textContent = '共 ' + totalChs + ' 章 | 已完成 ' + doneChs + ' 章 | 点击章节查看或生成';
    document.getElementById('btn-add-ch').disabled = false;
    document.getElementById('btn-edit-ol').disabled = false;
    document.getElementById('btn-batch').disabled = false;
}

// ===== 删除章节 =====
function deleteChapter(volNum, chNum) {
    if (!currentProjectId) return;
    modalConfirm('删除章节正文', '确定删除第' + volNum + '卷第' + chNum + '章的生成内容？<br><small style="color:var(--text-muted)">大纲节点会保留，状态变回⬜，可重新生成。</small>', async function() {
        try {
            var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + chNum + '?volume=' + volNum, {method: 'DELETE'});
            var d = await resp.json();
            if (d.status === 'ok') {
                if (outlineData) {
                    outlineData.volumes.forEach(function(v) {
                        if (v.volume_number === volNum) {
                            v.chapters.forEach(function(ch) {
                                if (ch.chapter_number === chNum) ch.status = 'planned';
                            });
                        }
                    });
                }
                _renderOutlineTree();
                if (currentVolumeNum === volNum && currentChapterNum === chNum) {
                    document.getElementById('chapter-content').value = '';
                    document.getElementById('current-chapter-label').textContent = '选择章节';
                    document.getElementById('word-count').textContent = '0 字';
                    currentChapterNum = 0;
                }
                toast(d.message, 'success');
            } else {
                toast(d.message || '删除失败', 'error');
            }
        } catch(e) { toast('删除失败: ' + e, 'error'); }
    }, {danger: true, okText: '删除'});
}

// ===== 大纲树实时更新 =====
function _highlightBatchChapter(vol, ch) {
    document.querySelectorAll('.outline-chapter.generating').forEach(function(el) {
        el.classList.remove('generating');
        var icon = el.querySelector('.ch-status-icon');
        if (icon) icon.textContent = '⬜';
    });
    var el = document.querySelector('.outline-chapter[data-vol="' + vol + '"][data-ch="' + ch + '"]');
    if (!el) return;
    el.classList.add('generating');
    var icon = el.querySelector('.ch-status-icon');
    if (icon) icon.innerHTML = '<span class="batch-spin">⏳</span>';
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
}
function _markChapterDone(vol, ch) {
    var el = document.querySelector('.outline-chapter[data-vol="' + vol + '"][data-ch="' + ch + '"]');
    if (!el) return;
    el.classList.remove('generating');
    el.classList.add('just-completed');
    var icon = el.querySelector('.ch-status-icon');
    if (icon) icon.textContent = '✅';
    setTimeout(function() { el.classList.remove('just-completed'); }, 700);
}
function _clearBatchHighlight() {
    document.querySelectorAll('.outline-chapter.generating').forEach(function(el) {
        el.classList.remove('generating');
        var icon = el.querySelector('.ch-status-icon');
        if (icon) icon.textContent = '⬜';
    });
}

function dragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
function dropChapter(e) { e.preventDefault(); toast('拖拽排序功能已就绪（保存时生效）', 'info'); }

function selectChapter(num, el, volNum) {
    if (currentChapterNum === num && currentVolumeNum === volNum) return;
    // 切换章节时：只隐藏状态条，不中断后台生成
    var oldKey = _genKey(currentProjectId, currentVolumeNum, currentChapterNum);
    var oldStream = _activeStreams[oldKey];
    if (oldStream && !oldStream.done) {
        // 隐藏状态条但不中断
        var oldSb = document.getElementById('stream-status'); if (oldSb) oldSb.remove();
    }
    _isGeneratingChapter = false;
    document.getElementById('btn-generate').disabled = false;
    currentChapterNum = num;
    currentVolumeNum = volNum || 1;
    var title = el ? el.getAttribute('data-title') || ('第' + num + '章') : ('第' + num + '章');
    document.getElementById('current-chapter-label').textContent = '第' + currentVolumeNum + '卷第' + num + '章: ' + title;
    document.getElementById('btn-ab').disabled = false;
    document.getElementById('btn-annotate').disabled = false;
    document.getElementById('btn-ch-versions').disabled = false;
    var ta = document.getElementById('chapter-content');
    ta.value = ''; ta.disabled = false; updateWordCount();
    document.querySelectorAll('.outline-chapter.active').forEach(function(e) { e.classList.remove('active'); });
    if (el) el.classList.add('active');
    loadChapterContent(num, volNum);
    loadAnnotations(num, volNum);
}

async function loadChapterContent(num, volNum) {
    var ta = document.getElementById('chapter-content');
    volNum = volNum || currentVolumeNum || 1;
    var key = _genKey(currentProjectId, volNum, num);
    var stream = _activeStreams[key];
    // 如果该章节正在流式生成中，恢复显示（不请求API）
    if (stream && !stream.done) {
        ta.value = stream.bufferedText; ta.disabled = false;
        _isGeneratingChapter = true;
        document.getElementById('btn-generate').disabled = true;
        _restoreStreamStatus(stream);
        updateWordCount();
        return;
    }
    // 如果该章节刚生成完毕（stream.done但还未清理），直接用缓冲文本
    if (stream && stream.done && stream.bufferedText) {
        ta.value = stream.bufferedText; ta.disabled = false;
        delete _activeStreams[key];
        updateWordCount();
        return;
    }
    // 正常从API加载
    ta.value = '加载中...'; ta.disabled = true;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + num + '?volume=' + volNum);
        if (resp.ok) { var data = await resp.json(); ta.value = data.content || ''; ta.disabled = false; updateWordCount(); }
        else { ta.value = ''; ta.disabled = false; updateWordCount(); }
    } catch(e) { ta.value = ''; ta.disabled = false; }
}

// ===== Generate =====
function _ensureStreamStatus() {
    var sb = document.getElementById('stream-status');
    if (!sb) {
        sb = document.createElement('div'); sb.id = 'stream-status';
        sb.style.cssText = 'position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent);z-index:10;opacity:0.8;border-radius:0';
        var editor = document.querySelector('.chapter-editor');
        if (editor) editor.appendChild(sb);
    }
    return sb;
}
function _restoreStreamStatus(stream) {
    // 恢复生成中章节的状态条
    _ensureStreamStatus();
}

async function generateStream() {
    if (!currentProjectId || !currentChapterNum) return;
    if (_isGeneratingChapter) { toast('请等待当前章节生成完成', 'info'); return; }
    var ta = document.getElementById('chapter-content');
    // 检查章节是否已有内容，如果有则强制重新生成
    var hasContent = !!(ta.value && ta.value.trim().length > 0 && !ta.value.startsWith('加载中'));
    document.getElementById('btn-generate').disabled = true;
    _isGeneratingChapter = true;
    _chapterAbortCtrl = new AbortController();
    var signal = _chapterAbortCtrl.signal;
    var genVol = currentVolumeNum, genCh = currentChapterNum;
    var genKey = _genKey(currentProjectId, genVol, genCh);
    _ensureStreamStatus();
    ta.value = ''; ta.disabled = false;
    _saveGenerating(currentProjectId, genVol, genCh);
    // 注册到活跃流表
    _activeStreams[genKey] = {vol: genVol, ch: genCh, abortCtrl: _chapterAbortCtrl, bufferedText: '', done: false, error: null};
    try {
        var resp = await fetch('/api/v1/stream/chapter', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project_id: currentProjectId, chapter_number: genCh, volume_number: genVol, force: hasContent}), signal: signal});
        var reader = resp.body.getReader(); var decoder = new TextDecoder(); var buffer = '';
        while (true) {
            var result = await reader.read(); if (result.done) break;
            buffer += decoder.decode(result.value, {stream: true});
            var lines = buffer.split('\n'); buffer = lines.pop() || '';
            for (var li = 0; li < lines.length; li++) {
                if (lines[li].startsWith('data: ')) {
                    try {
                        var d = JSON.parse(lines[li].slice(6));
                        if (d.token) {
                            _activeStreams[genKey].bufferedText += d.token;
                            if (currentVolumeNum === genVol && currentChapterNum === genCh) { ta.value = _activeStreams[genKey].bufferedText; ta.scrollTop = ta.scrollHeight; updateWordCount(); }
                        }
                        if (d.status === 'saved') {
                            _activeStreams[genKey].done = true;
                            _clearGenerating(currentProjectId, genVol, genCh);
                            // 立即更新大纲树（不经过 loadProject 避免闪烁）
                            if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === genVol) { v.chapters.forEach(function(ch) { if (ch.chapter_number === genCh) ch.status = 'completed'; }); } });
                            _renderOutlineTree();
                            if (currentVolumeNum === genVol && currentChapterNum === genCh) {
                                toast('已保存 (' + d.word_count + '字)', 'success');
                                updateWordCount();
                            }
                        }
                        if (d.error) {
                            _activeStreams[genKey].error = d.error;
                            _activeStreams[genKey].done = true;
                            _clearGenerating(currentProjectId, genVol, genCh);
                            if (currentVolumeNum === genVol && currentChapterNum === genCh) toast('生成失败: ' + d.error, 'error');
                        }
                    } catch(e) {}
                }
            }
        }
    } catch(e) {
        if (e.name === 'AbortError') {} else {
            _activeStreams[genKey].error = '' + e;
            _activeStreams[genKey].done = true;
            _clearGenerating(currentProjectId, genVol, genCh);
            if (currentVolumeNum === genVol && currentChapterNum === genCh) toast('流式生成失败: ' + e, 'error');
        }
    }
    // 如果当前正在看这个章节，清理UI；否则留给loadChapterContent处理
    if (currentVolumeNum === genVol && currentChapterNum === genCh) {
        var sb = document.getElementById('stream-status'); if (sb) sb.remove();
        _isGeneratingChapter = false; _chapterAbortCtrl = null;
        document.getElementById('btn-generate').disabled = false;
    } else {
        _isGeneratingChapter = false; _chapterAbortCtrl = null;
    }
    // 延迟清理_activeStreams（给切回来的时间）
    setTimeout(function() { delete _activeStreams[genKey]; }, 30000);
    updateWordCount();
}

async function generateBatch(startIdx, savedPending) {
    if (!currentProjectId || !outlineData || !outlineData.volumes) return;
    var pending;
    if (savedPending && savedPending.length > 0) {
        // 恢复模式：直接使用保存的 pending 数组，保证索引一致
        pending = savedPending;
    } else {
        // 新建模式：从大纲中筛选未完成章节
        pending = [];
        outlineData.volumes.forEach(function(vol) {
            (vol.chapters || []).forEach(function(ch) {
                if (ch.status !== 'completed') pending.push({vol: vol.volume_number, ch: ch.chapter_number, title: ch.title || ('第' + ch.chapter_number + '章')});
            });
        });
    }
    if (pending.length === 0) { toast('所有章节已完成', 'info'); _clearBatchState(); return; }
    var isResume = (startIdx > 0);
    if (!isResume) {
        modalConfirm('批量生成', '将按顺序生成 <b>' + pending.length + '</b> 个未完成章节，预计耗时较长。', function() { _startBatchGeneration(pending, startIdx); });
        return;
    }
    _startBatchGeneration(pending, startIdx);
}
async function _startBatchGeneration(pending, startIdx) {
    _batchCancelled = false; _batchActive = true;
    var progDiv = document.getElementById('batch-progress');
    var statusBar = document.getElementById('batch-status');
    var bar = document.getElementById('batch-bar');
    var detail = document.getElementById('batch-detail');
    progDiv.style.display = 'block';
    document.getElementById('btn-batch').disabled = true;
    document.getElementById('btn-generate').disabled = true;
    var isResume = (startIdx > 0);
    var total = pending.length, done = isResume ? startIdx : 0, failed = 0;
    for (var i = (startIdx || 0); i < pending.length; i++) {
        if (_batchCancelled) { statusBar.textContent = '已停止'; _clearBatchHighlight(); _clearBatchState(); break; }
        var p = pending[i];
        statusBar.textContent = isResume ? '恢复批量生成...' : '批量生成中...';
        detail.textContent = '第' + p.vol + '卷第' + p.ch + '章: ' + p.title + ' (' + (i + 1) + '/' + total + ')';
        bar.style.width = Math.round(i / total * 100) + '%';
        _saveBatchState(currentProjectId, pending, i, done, failed);
        // 批量生成中，只更新编辑器和标签当用户正在查看当前批量生成的章节
        var ta = document.getElementById('chapter-content');
        ta.value = ''; ta.disabled = false;
        _saveGenerating(currentProjectId, p.vol, p.ch);
        _highlightBatchChapter(p.vol, p.ch);
        var batchGenKey = _genKey(currentProjectId, p.vol, p.ch);
        _activeStreams[batchGenKey] = {vol: p.vol, ch: p.ch, abortCtrl: null, bufferedText: '', done: false, error: null};
        try {
            var resp = await fetch('/api/v1/stream/chapter', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({project_id: currentProjectId, chapter_number: p.ch, volume_number: p.vol})});
            var reader = resp.body.getReader(); var decoder = new TextDecoder(); var buffer = '';
            while (true) {
                var r = await reader.read(); if (r.done) break;
                buffer += decoder.decode(r.value, {stream: true});
                var lines = buffer.split('\n'); buffer = lines.pop() || '';
                for (var li = 0; li < lines.length; li++) {
                    if (lines[li].startsWith('data: ')) {
                        try {
                            var d = JSON.parse(lines[li].slice(6));
                            if (d.token) {
                                _activeStreams[batchGenKey].bufferedText += d.token;
                                if (currentVolumeNum === p.vol && currentChapterNum === p.ch) { ta.value = _activeStreams[batchGenKey].bufferedText; ta.scrollTop = ta.scrollHeight; updateWordCount(); }
                            }
                            if (d.status === 'saved') {
                                _activeStreams[batchGenKey].done = true;
                                done++;
                                _clearGenerating(currentProjectId, p.vol, p.ch);
                                _markChapterDone(p.vol, p.ch);
                                if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === p.vol) { v.chapters.forEach(function(ch) { if (ch.chapter_number === p.ch) ch.status = 'completed'; }); } });
                                _renderOutlineTree();
                            }
                            if (d.error) { _activeStreams[batchGenKey].error = d.error; _activeStreams[batchGenKey].done = true; _clearGenerating(currentProjectId, p.vol, p.ch); _clearBatchHighlight(); }
                        } catch(e) {}
                    }
                }
            }
        } catch(e) { failed++; _activeStreams[batchGenKey].error = '' + e; _activeStreams[batchGenKey].done = true; _clearGenerating(currentProjectId, p.vol, p.ch); _clearBatchHighlight(); }
        setTimeout(function() { delete _activeStreams[batchGenKey]; }, 30000);
        bar.style.width = Math.round((i + 1) / total * 100) + '%';
        _saveBatchState(currentProjectId, pending, i + 1, done, failed);
        isResume = false;
    }
    _clearBatchHighlight(); _clearBatchState(); _batchActive = false;
    var wasCancelled = _batchCancelled;
    statusBar.textContent = wasCancelled ? '已停止' : '批量完成';
    detail.textContent = '成功 ' + done + ' 章' + (failed > 0 ? '，失败 ' + failed + ' 章' : '');
    bar.style.width = wasCancelled ? bar.style.width : '100%';
    loadOutline();
    document.getElementById('btn-batch').disabled = false;
    document.getElementById('btn-generate').disabled = false;
    document.getElementById('btn-cancel-batch').disabled = false;
    toast(wasCancelled ? '批量生成已停止' : '批量生成完成: ' + done + '/' + total + ' 章', wasCancelled ? 'info' : 'success');
    setTimeout(function() { document.getElementById('batch-progress').style.display = 'none'; }, 3000);
}
function cancelBatch() {
    _batchCancelled = true; _clearBatchState(); _batchActive = false;
    document.getElementById('btn-cancel-batch').disabled = true;
}

// ===== 风格圣经 =====
function toggleStyleBible() {
    var panel = document.getElementById('style-bible-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}
async function loadStyleBible(pid) {
    if (!pid) return;
    try {
        var resp = await fetch('/api/v1/projects/' + pid);
        var proj = await resp.json();
        _styleBible = proj.style_bible || '';
        document.getElementById('style-bible-input').value = _styleBible;
    } catch(e) {}
}
async function saveStyleBible() {
    if (!currentProjectId) return;
    var text = document.getElementById('style-bible-input').value.trim();
    try {
        await fetch('/api/v1/projects/' + currentProjectId, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({style_bible: text})});
        _styleBible = text; toast('风格圣经已保存', 'success');
    } catch(e) { toast('保存失败', 'error'); }
}

// ===== AI 编辑批注 =====
async function runAutoAnnotate() {
    if (!currentProjectId || !currentChapterNum) return;
    var btn = document.getElementById('btn-annotate');
    btn.disabled = true; btn.textContent = '批注中...';
    var panel = document.getElementById('annotation-panel');
    panel.style.display = 'block';
    document.getElementById('ann-list').innerHTML = '<p class="muted" style="text-align:center;padding:12px">AI 编辑正在审阅...</p>';
    document.getElementById('ann-overall').style.display = 'none';
    try {
        var content = document.getElementById('chapter-content').value;
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/annotations/auto', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({chapter_number: currentChapterNum, volume_number: currentVolumeNum, content: content})
        });
        var d = await resp.json();
        if (d.error) { document.getElementById('ann-list').innerHTML = '<p class="error">' + d.error + '</p>'; return; }
        renderAnnotations(d);
    } catch(e) { document.getElementById('ann-list').innerHTML = '<p class="error">批注失败</p>'; }
    btn.disabled = false; btn.textContent = '📋';
}
function renderAnnotations(data) {
    var anns = data.annotations || [];
    document.getElementById('ann-stats').textContent = (data.issues || 0) + '问题 · ' + (data.suggestions || 0) + '建议 · ' + (data.praises || 0) + '亮点';
    var overall = document.getElementById('ann-overall');
    if (data.overall_comment) { overall.textContent = data.overall_comment; overall.style.display = 'block'; }
    var html = '';
    var typeColors = {issue: 'var(--bad)', suggestion: 'var(--warn)', praise: 'var(--ok)'};
    var typeIcons = {issue: '🔴', suggestion: '🟡', praise: '🟢'};
    var typeLabels = {issue: '问题', suggestion: '建议', praise: '亮点'};
    anns.forEach(function(ann) {
        var color = typeColors[ann.type] || 'var(--accent)';
        var icon = typeIcons[ann.type] || '💬';
        var label = typeLabels[ann.type] || '批注';
        html += '<div style="padding:8px;margin:4px 0;background:var(--bg);border-left:3px solid ' + color + ';border-radius:0 4px 4px 0;font-size:12px">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">';
        html += '<span style="font-weight:600;color:' + color + '">' + icon + ' ' + label + '</span>';
        if (ann.start >= 0) html += '<button class="btn btn-sm" onclick="jumpToAnnotation(' + ann.start + ',' + ann.end + ')" style="font-size:10px;padding:1px 4px">定位</button>';
        html += '</div>';
        if (ann.quote) html += '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;font-style:italic">"' + esc(ann.quote).substring(0, 60) + '"</div>';
        html += '<div>' + esc(ann.comment || '') + '</div>';
        if (ann.fix) html += '<div style="margin-top:4px;color:var(--accent);font-size:11px">💡 ' + esc(ann.fix) + '</div>';
        html += '</div>';
    });
    document.getElementById('ann-list').innerHTML = html || '<p class="muted" style="text-align:center">暂无批注</p>';
}
function jumpToAnnotation(start, end) {
    var ta = document.getElementById('chapter-content');
    if (start < 0) return;
    ta.focus(); ta.setSelectionRange(start, end);
    var linesBefore = ta.value.substring(0, start).split('\n').length;
    ta.scrollTop = (linesBefore - 5) * 20;
}
async function loadAnnotations(chNum, volNum) {
    if (!currentProjectId) return;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/annotations/' + chNum + '?volume=' + (volNum || 1));
        var d = await resp.json();
        if (d.annotations && d.annotations.length > 0) { document.getElementById('annotation-panel').style.display = 'block'; renderAnnotations({annotations: d.annotations}); }
    } catch(e) {}
}

// ===== A/B 对比 =====
async function startABCompare() {
    if (!currentProjectId || !currentChapterNum) return;
    document.getElementById('ab-ch-num').textContent = currentChapterNum;
    document.getElementById('ab-modal').style.display = 'flex';
    document.getElementById('ab-loading').style.display = 'block';
    document.getElementById('ab-content').style.display = 'none';
    document.getElementById('ab-actions').style.display = 'none';
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/ab-compare', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({chapter_number: currentChapterNum, volume_number: currentVolumeNum})
        });
        var d = await resp.json();
        if (d.error) { document.getElementById('ab-loading').innerHTML = '<p class="error">' + d.error + '</p>'; return; }
        document.getElementById('ab-text-a').value = d.version_a.content || '';
        document.getElementById('ab-text-b').value = d.version_b.content || '';
        document.getElementById('ab-score-a').textContent = '评分: ' + (d.version_a.score || 0).toFixed(2) + ' (' + d.version_a.words + '字)';
        document.getElementById('ab-score-b').textContent = '评分: ' + (d.version_b.score || 0).toFixed(2) + ' (' + d.version_b.words + '字)';
        var scoreA = d.version_a.score || 0, scoreB = d.version_b.score || 0;
        if (scoreB > scoreA) document.getElementById('ab-score-b').style.color = 'var(--ok)';
        else if (scoreA > scoreB) document.getElementById('ab-score-a').style.color = 'var(--ok)';
        document.getElementById('ab-loading').style.display = 'none';
        document.getElementById('ab-content').style.display = 'flex';
        document.getElementById('ab-actions').style.display = 'flex';
    } catch(e) { document.getElementById('ab-loading').innerHTML = '<p class="error">对比失败</p>'; }
}
function closeABModal() { document.getElementById('ab-modal').style.display = 'none'; }
function applyABVersionB() {
    var content = document.getElementById('ab-text-b').value;
    if (!content) return;
    document.getElementById('chapter-content').value = content;
    updateWordCount(); closeABModal(); toast('已采用版本B', 'success');
}

// ===== 选区重写 =====
function initRewriteToolbar() {
    var ta = document.getElementById('chapter-content');
    ta.addEventListener('mouseup', function() {
        var start = ta.selectionStart, end = ta.selectionEnd;
        if (start !== end && end - start >= 10) {
            var toolbar = document.getElementById('rewrite-toolbar');
            var linesBefore = ta.value.substring(0, start).split('\n').length;
            var topPx = Math.min((linesBefore - 1) * 20, ta.getBoundingClientRect().height - 120);
            toolbar.style.display = 'block';
            toolbar.style.top = Math.max(topPx, 10) + 'px';
            toolbar.style.left = '10px'; toolbar.style.right = '10px';
            document.getElementById('rewrite-selected-info').textContent = '已选中 ' + (end - start) + ' 字';
            document.getElementById('rewrite-result').style.display = 'none';
            _rewriteRange = {start: start, end: end, original: ta.value.substring(start, end)};
        }
    });
    document.addEventListener('mousedown', function(e) {
        var toolbar = document.getElementById('rewrite-toolbar');
        if (toolbar.style.display === 'block' && !toolbar.contains(e.target) && e.target !== ta) hideRewriteToolbar();
    });
}
initRewriteToolbar();
function hideRewriteToolbar() {
    document.getElementById('rewrite-toolbar').style.display = 'none';
    document.getElementById('rewrite-result').style.display = 'none';
    _rewriteRange = null;
}
async function rewriteSelection() {
    if (!_rewriteRange || !currentProjectId) return;
    var ta = document.getElementById('chapter-content');
    var btn = document.getElementById('btn-rewrite');
    btn.disabled = true; btn.textContent = '改写中...';
    var resultDiv = document.getElementById('rewrite-result');
    var resultText = document.getElementById('rewrite-result-text');
    resultDiv.style.display = 'block'; resultText.textContent = 'AI 正在改写...';
    var before = ta.value.substring(Math.max(0, _rewriteRange.start - 500), _rewriteRange.start);
    var after = ta.value.substring(_rewriteRange.end, _rewriteRange.end + 500);
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/rewrite', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: _rewriteRange.original, context_before: before, context_after: after, platform: ''})
        });
        var d = await resp.json();
        if (d.rewritten) { resultText.textContent = d.rewritten; resultText.dataset.rewritten = d.rewritten; }
        else { resultText.textContent = d.error || '改写失败'; }
    } catch(e) { resultText.textContent = '请求失败'; }
    btn.disabled = false; btn.textContent = '✨ 重写选区';
}
function applyRewrite() {
    var ta = document.getElementById('chapter-content');
    var resultText = document.getElementById('rewrite-result-text');
    var rewritten = resultText.dataset.rewritten;
    if (!_rewriteRange || !rewritten) return;
    ta.value = ta.value.substring(0, _rewriteRange.start) + rewritten + ta.value.substring(_rewriteRange.end);
    updateWordCount(); hideRewriteToolbar(); toast('已替换选区内容', 'success');
}

async function saveChapter() {
    var content = document.getElementById('chapter-content').value;
    if (!currentProjectId || !currentChapterNum || !content) return;
    try {
        await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + currentChapterNum, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: content, volume_number: currentVolumeNum})});
        if (outlineData) outlineData.volumes.forEach(function(v) { if (v.volume_number === currentVolumeNum) { v.chapters.forEach(function(ch) { if (ch.chapter_number === currentChapterNum) ch.status = 'completed'; }); } });
        _renderOutlineTree();
        toast('已保存', 'success');
    } catch(e) { toast('保存失败: ' + e.message, 'error'); }
}

// ===== Word Count =====
function updateWordCount() {
    var len = document.getElementById('chapter-content').value.length;
    document.getElementById('word-count').textContent = len + ' 字';
}
document.getElementById('chapter-content').addEventListener('input', updateWordCount);

// ===== Mini D3 Graph =====
async function loadMiniGraph() {
    var container = document.getElementById('mini-graph');
    if (!container) return;
    container.innerHTML = ''; // 清除旧图谱
    if (!currentProjectId) return;
    try {
        var resp = await fetch('/api/v1/graph/export?project_id=' + currentProjectId);
        var data = await resp.json();
        if (!data.nodes || data.nodes.length === 0) { container.innerHTML = '<p class="muted" style="text-align:center;padding-top:100px">暂无图谱<br><small>请先生成项目设定或点击「建图」</small></p>'; return; }
        drawGraph(container, data);
    } catch(e) {}
}
function drawGraph(container, data) {
    container.innerHTML = '';
    var W = container.clientWidth, H = container.clientHeight;
    var svg = d3.select(container).append('svg').attr('width', W).attr('height', H);
    var color = d3.scaleOrdinal(d3.schemeCategory10);
    var relTypeMap = {'ALLY':'盟友','ENEMY':'敌人','FAMILY':'家人','MASTER_DISCIPLE':'师徒','RIVAL':'对手','SUBORDINATE':'从属','ROMANTIC':'恋人','ACQUAINTANCE':'相识','BELONGS_TO':'属于','LOCATED_AT':'位于','POSSESSES':'拥有','PARTICIPATES_IN':'参与'};
    var nodes = data.nodes.map(function(n) { return Object.assign({}, n); });
    var edges = data.edges.map(function(e) { return {source: e.source, target: e.target, type: e.type}; });
    var sim = d3.forceSimulation(nodes).force('link', d3.forceLink(edges).id(function(d) { return d.id; }).distance(60)).force('charge', d3.forceManyBody().strength(-200)).force('center', d3.forceCenter(W / 2, H / 2));
    var link = svg.append('g').selectAll('line').data(edges).join('line').attr('stroke', '#444').attr('stroke-width', 1.5);
    var node = svg.append('g').selectAll('circle').data(nodes).join('circle').attr('r', 8).attr('fill', function(d) { return color(d.group); }).call(d3.drag().on('start', function(e, d) { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }).on('drag', function(e, d) { d.fx = e.x; d.fy = e.y; }).on('end', function(e, d) { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    var label = svg.append('g').selectAll('text').data(nodes).join('text').text(function(d) { return d.label || d.id; }).attr('font-size', 10).attr('fill', '#aaa').attr('dx', 10).attr('dy', 4);
    svg.append('g').selectAll('text').data(edges).join('text').text(function(d) { return relTypeMap[d.type] || d.type; }).attr('font-size', 8).attr('fill', '#666').attr('x', function(d) { return (d.source.x + d.target.x) / 2; }).attr('y', function(d) { return (d.source.y + d.target.y) / 2; });
    sim.on('tick', function() { link.attr('x1', function(d) { return d.source.x; }).attr('y1', function(d) { return d.source.y; }).attr('x2', function(d) { return d.target.x; }).attr('y2', function(d) { return d.target.y; }); node.attr('cx', function(d) { return d.x; }).attr('cy', function(d) { return d.y; }); label.attr('x', function(d) { return d.x; }).attr('y', function(d) { return d.y; }); });
}

// ===== Right Panel =====
function switchTab(tab) {
    document.querySelectorAll('.wb-tab').forEach(function(t) { t.classList.remove('active'); });
    document.querySelectorAll('.wb-tab-content').forEach(function(c) { c.classList.remove('active'); });
    event.target.classList.add('active'); document.getElementById('tab-' + tab).classList.add('active');
}

document.getElementById('seed-input').addEventListener('input', function() { document.getElementById('seed-char-count').textContent = this.value.length; });
function fillSeed(el) { document.getElementById('seed-input').value = el.textContent; document.getElementById('seed-char-count').textContent = el.textContent.length; }

async function createProjectFromIdea(idx) {
    var dir = _incubatedDirections[idx]; if (!dir) return;
    var platform = document.getElementById('seed-platform').value;
    try {
        var r = await fetch('/api/v1/projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: dir.logline || '新项目', platform: platform, one_liner: dir.core_conflict || '', genre_tags: dir.genre_tags || [], length: 'medium'})});
        var d = await r.json();
        if (d.project_id) { toast('项目已创建', 'success'); window.location.href = '/workbench?project_id=' + d.project_id; }
    } catch(e) { toast('创建失败', 'error'); }
}
async function incubateIdea() {
    var seed = document.getElementById('seed-input').value.trim(); if (!seed) { toast('请输入灵感种子', 'info'); return; }
    var resultDiv = document.getElementById('idea-results');
    var btn = document.getElementById('btn-incubate');
    btn.disabled = true; btn.textContent = '孵化中...';
    resultDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-muted)"><div class="batch-spin" style="font-size:20px;display:inline-block">💡</div><div style="margin-top:6px;font-size:12px">AI 正在构思创意方向...</div></div>';
    try {
        var count = parseInt(document.getElementById('seed-count').value) || 2;
        var resp = await fetch('/api/v1/skills/incubate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({args: {seed: seed, platform: document.getElementById('seed-platform').value, count: count}})});
        var d = await resp.json();
        if (d.directions && d.directions.length > 0) {
            _incubatedDirections = d.directions;
            resultDiv.innerHTML = d.directions.map(function(dir, i) {
                var tags = (dir.genre_tags || []).map(function(t) { return '<span>' + t + '</span>'; }).join('');
                return '<div class="idea-card"><div class="idea-title"><span class="badge">方案' + (i + 1) + '</span>' + esc(dir.logline || '未命名') + '</div>' +
                    (dir.core_conflict ? '<div class="idea-row"><strong>核心冲突:</strong> ' + esc(dir.core_conflict) + '</div>' : '') +
                    (dir.golden_finger ? '<div class="idea-row"><strong>金手指:</strong> ' + esc(dir.golden_finger) + '</div>' : '') +
                    (dir.protagonist ? '<div class="idea-row"><strong>主角:</strong> ' + esc(dir.protagonist) + '</div>' : '') +
                    (dir.antagonist ? '<div class="idea-row"><strong>对手:</strong> ' + esc(dir.antagonist) + '</div>' : '') +
                    (tags ? '<div class="idea-tags">' + tags + '</div>' : '') +
                    '<div class="idea-actions"><button class="btn btn-primary btn-sm" onclick="createProjectFromIdea(' + i + ')">📖 以此创建项目</button></div></div>';
            }).join('');
        } else { resultDiv.innerHTML = '<p class="muted" style="text-align:center;padding:12px">未生成有效方案，请换个灵感试试</p>'; }
    } catch(e) { resultDiv.innerHTML = '<p class="error" style="text-align:center">孵化失败</p>'; }
    btn.disabled = false; btn.textContent = '💡 孵化';
}

// ===== 大纲生成 =====
async function generateOutline() {
    if (!currentProjectId) return;
    var btn = document.getElementById('btn-outline'); btn.disabled = true; btn.textContent = '生成中...';
    var tree = document.getElementById('outline-tree'); tree.innerHTML = '<p class="muted">⏳ 启动大纲生成...</p>';
    _outlineVersions = [];

    try {
        // 使用异步接口启动后台生成
        var resp = await fetch('/api/v1/outline/generate-async', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project_id: currentProjectId, versions: 3})
        });
        var data = await resp.json();

        if (data.status === 'already_running') {
            toast('大纲生成已在进行中，请等待', 'info');
        } else {
            toast('大纲生成已启动', 'info');
        }

        // 开始轮询状态
        _pollOutlineStatus();
    } catch(e) {
        toast('启动生成失败: ' + e.message, 'error');
        tree.innerHTML = '<p class="muted">启动失败</p>';
        btn.disabled = false; btn.textContent = '生成';
    }
}

// 轮询大纲生成状态
var _outlinePollTimer = null;

// 页面加载时检查是否有进行中的大纲生成任务
async function _checkOutlineGenerationStatus(pid) {
    if (!pid) return;
    try {
        var resp = await fetch('/api/v1/outline/status/' + pid);
        var data = await resp.json();

        if (data.status === 'generating') {
            // 有进行中的任务，开始轮询
            toast('检测到进行中的大纲生成，正在恢复状态...', 'info');
            document.getElementById('btn-outline').disabled = true;
            document.getElementById('btn-outline').textContent = '生成中...';
            _pollOutlineStatus();
            return;
        }

        if (data.status === 'done' && data.versions && data.versions.length > 0) {
            // 有已完成但未领取的结果（可能是服务重启后恢复的）
            _outlineVersions = data.versions.map(function(v) {
                v.data._style_tag = v.style_tag || '';
                return v.data;
            });
            var tree = document.getElementById('outline-tree');
            var treeIsEmpty = !outlineData || !outlineData.volumes || outlineData.volumes.length === 0;
            var source = data.from_db ? '服务重启前' : '上次生成';
            var msg = '已恢复 ' + _outlineVersions.length + ' 个大纲方案（来自' + source + '）';

            if (treeIsEmpty) {
                // 没有现有大纲，直接显示恢复的方案
                tree.innerHTML = '<p class="muted">✅ ' + msg + '</p>' +
                    '<button class="btn btn-sm btn-primary" style="margin-top:8px;width:100%" onclick="renderVersionCards(_outlineVersions)">📋 查看方案</button>';
            } else {
                // 已有大纲，在大纲树下方添加提示
                var hint = document.createElement('div');
                hint.style.cssText = 'margin-top:8px;padding:8px;background:rgba(124,92,252,0.08);border:1px solid var(--accent);border-radius:6px;font-size:11px;text-align:center';
                hint.innerHTML = '💡 ' + msg + ' <button class="btn btn-sm btn-primary" onclick="renderVersionCards(_outlineVersions)" style="margin-left:8px;font-size:10px">📋 查看方案</button>';
                tree.appendChild(hint);
            }

            _saveOutlineVersions(_outlineVersions, pid);
            toast(msg, 'success', 5000);
            // 清除后端状态
            fetch('/api/v1/outline/status/' + pid, {method: 'DELETE'});
        }
    } catch(e) {
        // 静默失败
    }
}

// 记录已展示的版本数，用于增量显示
var _shownOutlineVersionCount = 0;

async function _pollOutlineStatus() {
    if (_outlinePollTimer) { clearTimeout(_outlinePollTimer); _outlinePollTimer = null; }
    if (!currentProjectId) return;

    var tree = document.getElementById('outline-tree');
    var btn = document.getElementById('btn-outline');

    try {
        var resp = await fetch('/api/v1/outline/status/' + currentProjectId);
        var data = await resp.json();

        if (data.status === 'not_found') {
            btn.disabled = false; btn.textContent = '生成';
            _shownOutlineVersionCount = 0;
            return;
        }

        if (data.status === 'generating') {
            var versions = data.versions || [];
            var current = data.current || 0;
            var total = data.total || 3;
            var msg = data.message || '生成中...';

            // 构建 HTML：进度条 + 已生成的版本卡片
            var html = '';

            // 进度条
            html += '<div style="margin-bottom:12px">';
            html += '<p class="muted" style="margin-bottom:6px">⏳ ' + msg + '</p>';
            html += '<div style="width:100%;height:6px;background:var(--bg);border-radius:3px;overflow:hidden">';
            html += '<div style="width:' + (current / total * 100) + '%;height:100%;background:var(--accent);transition:width 0.3s;border-radius:3px"></div>';
            html += '</div>';
            html += '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;text-align:right">' + versions.length + '/' + total + ' 个方案已生成</div>';
            html += '</div>';

            // 增量显示已生成的版本卡片
            if (versions.length > 0) {
                html += '<div style="display:flex;flex-direction:column;gap:8px">';
                versions.forEach(function(ver, idx) {
                    var verData = ver.data || ver;
                    var vols = verData.volumes || [];
                    var totalChs = 0;
                    vols.forEach(function(v) { totalChs += (v.chapters || []).length; });
                    var styleTag = ver.style_tag || verData._style_tag || '';
                    var isEmpty = vols.length === 0 || totalChs === 0;
                    html += '<div class="version-card" style="cursor:pointer;animation:fadeIn 0.3s ease' + (isEmpty ? ';opacity:0.5' : '') + '" onclick="_previewOutlineVersion(' + idx + ')">';
                    html += '<div style="display:flex;justify-content:space-between;align-items:center">';
                    html += '<strong style="font-size:13px">方案 ' + (idx + 1) + '</strong>';
                    if (styleTag) html += '<span style="font-size:10px;padding:2px 8px;background:var(--accent);color:white;border-radius:10px">' + esc(styleTag) + '</span>';
                    html += '</div>';
                    if (isEmpty) {
                        html += '<div style="font-size:11px;color:var(--bad);margin-top:4px">⚠ 该方案生成失败（无有效内容）</div>';
                    } else {
                        html += '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">' + vols.length + ' 卷 · ' + totalChs + ' 章</div>';
                    }
                    // 显示第一章标题预览
                    if (vols.length > 0 && vols[0].chapters && vols[0].chapters.length > 0) {
                        var firstCh = vols[0].chapters[0];
                        html += '<div style="font-size:11px;margin-top:4px;color:var(--text)">Ch1: ' + esc(firstCh.title || '无标题') + '</div>';
                        if (firstCh.summary) html += '<div style="font-size:10px;color:var(--text-muted);margin-top:2px">' + esc(firstCh.summary).substring(0, 60) + '...</div>';
                    }
                    html += '<div style="display:flex;gap:4px;margin-top:8px">';
                    html += '<button class="btn btn-sm btn-primary" onclick="event.stopPropagation();_applyOutlineVersion(' + idx + ')" style="font-size:10px">✅ 应用此方案</button>';
                    html += '<button class="btn btn-sm" onclick="event.stopPropagation();_previewOutlineVersion(' + idx + ')" style="font-size:10px">👁️ 预览</button>';
                    html += '</div>';
                    html += '</div>';
                });
                html += '</div>';
            }

            tree.innerHTML = html;
            btn.disabled = true; btn.textContent = '生成中...';

            // 保存到内存供其他函数使用
            _outlineVersions = versions.map(function(v) {
                if (!v.data._style_tag) v.data._style_tag = v.style_tag || '';
                return v.data;
            });
            _shownOutlineVersionCount = versions.length;

            _outlinePollTimer = setTimeout(_pollOutlineStatus, 1500);
            return;
        }

        if (data.status === 'done') {
            var versions = data.versions || [];
            _shownOutlineVersionCount = 0;

            if (versions.length > 0) {
                _outlineVersions = versions.map(function(v) {
                    v.data._style_tag = v.style_tag || '';
                    return v.data;
                });

                // 显示完成状态 + 所有版本卡片
                var html = '<p class="muted" style="margin-bottom:12px">✅ 大纲生成完成，共 ' + _outlineVersions.length + ' 个方案</p>';
                html += '<div style="display:flex;flex-direction:column;gap:8px">';
                _outlineVersions.forEach(function(ver, idx) {
                    var vols = ver.volumes || [];
                    var totalChs = 0;
                    vols.forEach(function(v) { totalChs += (v.chapters || []).length; });
                    var styleTag = ver._style_tag || '';
                    html += '<div class="version-card" style="cursor:pointer" onclick="_previewOutlineVersion(' + idx + ')">';
                    html += '<div style="display:flex;justify-content:space-between;align-items:center">';
                    html += '<strong style="font-size:13px">方案 ' + (idx + 1) + '</strong>';
                    if (styleTag) html += '<span style="font-size:10px;padding:2px 8px;background:var(--accent);color:white;border-radius:10px">' + esc(styleTag) + '</span>';
                    html += '</div>';
                    html += '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">' + vols.length + ' 卷 · ' + totalChs + ' 章</div>';
                    if (vols.length > 0 && vols[0].chapters && vols[0].chapters.length > 0) {
                        var firstCh = vols[0].chapters[0];
                        html += '<div style="font-size:11px;margin-top:4px;color:var(--text)">Ch1: ' + esc(firstCh.title || '无标题') + '</div>';
                        if (firstCh.summary) html += '<div style="font-size:10px;color:var(--text-muted);margin-top:2px">' + esc(firstCh.summary).substring(0, 60) + '...</div>';
                    }
                    html += '<div style="display:flex;gap:4px;margin-top:8px">';
                    html += '<button class="btn btn-sm btn-primary" onclick="event.stopPropagation();_applyOutlineVersion(' + idx + ')" style="font-size:10px">✅ 应用此方案</button>';
                    html += '<button class="btn btn-sm" onclick="event.stopPropagation();_previewOutlineVersion(' + idx + ')" style="font-size:10px">👁️ 预览</button>';
                    html += '</div>';
                    html += '</div>';
                });
                html += '</div>';
                tree.innerHTML = html;

                _clearOutlineVersions(currentProjectId);
                _saveOutlineVersions(_outlineVersions, currentProjectId);
                _saveDraftOutlineToBackend(_outlineVersions[0]);
                toast('大纲生成完成，共 ' + _outlineVersions.length + ' 个方案', 'success');
            } else {
                tree.innerHTML = '<p class="muted">未生成有效大纲</p>';
                toast('所有版本均生成失败', 'error');
            }
            fetch('/api/v1/outline/status/' + currentProjectId, {method: 'DELETE'});
            btn.disabled = false; btn.textContent = '生成';
            return;
        }

        if (data.status === 'error') {
            _shownOutlineVersionCount = 0;
            tree.innerHTML = '<p class="muted">❌ ' + (data.message || '生成失败') + '</p>';
            toast('大纲生成失败', 'error');
            btn.disabled = false; btn.textContent = '生成';
            return;
        }
    } catch(e) {
        _outlinePollTimer = setTimeout(_pollOutlineStatus, 5000);
    }
}

// 预览大纲版本
function _previewOutlineVersion(idx) {
    if (!_outlineVersions || idx >= _outlineVersions.length) return;
    renderVersionCards(_outlineVersions);
}

// 应用大纲版本
async function _applyOutlineVersion(idx) {
    if (!_outlineVersions || idx >= _outlineVersions.length) return;
    var ver = _outlineVersions[idx];
    if (!ver || !ver.volumes || ver.volumes.length === 0) {
        toast('该方案无有效内容，无法应用', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline/apply', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({data: ver})
        });
        var result = await resp.json();
        if (result.status === 'applied') {
            toast('已应用方案 ' + (idx + 1) + ' (' + result.volumes + '卷' + result.chapters + '章)', 'success');
            // 清除生成状态
            fetch('/api/v1/outline/status/' + currentProjectId, {method: 'DELETE'});
            // 重新加载大纲
            await loadOutline();
        } else {
            toast('应用失败', 'error');
        }
    } catch(e) {
        toast('应用失败: ' + e.message, 'error');
    }
}

// 保存草稿大纲到后端（不覆盖已有大纲，仅在后端没有大纲时保存）
async function _saveDraftOutlineToBackend(outlineData) {
    if (!currentProjectId || !outlineData || !outlineData.volumes) return;
    try {
        // 先检查后端是否已有大纲
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline');
        var existing = await resp.json();
        if (existing && existing.volumes && existing.volumes.length > 0) {
            // 后端已有大纲，不覆盖
            return;
        }
        // 后端没有大纲，保存草稿
        await fetch('/api/v1/projects/' + currentProjectId + '/outline', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(outlineData)
        });
    } catch(e) {
        // 静默失败，不影响用户体验
    }
}
function renderVersionCards(versions) {
    var modal = document.getElementById('outline-versions-modal');
    var listDiv = document.getElementById('outline-versions-list');
    var countSpan = document.getElementById('outline-versions-count');
    countSpan.textContent = versions.length + ' 个方案';
    var html = '';
    versions.forEach(function(ver, idx) {
        var vols = ver.volumes || [];
        var totalChs = 0;
        vols.forEach(function(v) { totalChs += (v.chapters || []).length; });
        var estWords = totalChs * 3000;
        var estLabel = estWords >= 10000 ? (estWords / 10000).toFixed(1) + '万字' : estWords + '字';
        var styleTag = ver._style_tag || '';
        var keyChars = new Set();
        vols.forEach(function(vol) { (vol.chapters || []).forEach(function(ch) { (ch.character_moments || []).forEach(function(cm) { if (keyChars.size < 5) keyChars.add(cm.substring(0, 10)); }); }); });
        var charList = Array.from(keyChars);
        var climaxCount = 0, hookCount = 0;
        vols.forEach(function(vol) { (vol.chapters || []).forEach(function(ch) { if (ch.is_climax) climaxCount++; if (ch.is_hook_point) hookCount++; }); });

        var isEmpty = vols.length === 0 || totalChs === 0;
        html += '<div class="version-card" id="ver-card-' + idx + '" style="margin-bottom:20px;padding:20px' + (isEmpty ? ';opacity:0.6;border:1px dashed var(--bad)' : '') + '">';
        // 头部：方案名 + 风格标签 + 应用按钮
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">';
        html += '<div style="display:flex;align-items:center;gap:12px"><h3 style="margin:0;font-size:18px">📋 方案 ' + (idx + 1) + '</h3>';
        if (styleTag) html += '<span style="font-size:13px;padding:4px 12px;background:var(--accent);color:white;border-radius:12px;font-weight:500">' + escAttr(styleTag) + '</span>';
        html += '</div>';
        if (!isEmpty) html += '<button class="btn btn-primary" onclick="applyOutlineVersion(' + idx + ')" style="font-size:14px;padding:8px 20px">✅ 应用此方案</button>';
        else html += '<span style="font-size:13px;color:var(--bad);padding:4px 12px">⚠ 无有效内容</span>';
        html += '</div>';

        if (isEmpty) {
            html += '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:14px">该方案未生成有效大纲内容，可能是 LLM 返回格式异常。请尝试重新生成。</div>';
        }

        // 统计标签
        html += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">';
        html += '<span style="font-size:13px;padding:4px 12px;background:var(--surface2);border-radius:6px">📚 ' + vols.length + ' 卷</span>';
        html += '<span style="font-size:13px;padding:4px 12px;background:var(--surface2);border-radius:6px">📄 ' + totalChs + ' 章</span>';
        html += '<span style="font-size:13px;padding:4px 12px;background:var(--surface2);border-radius:6px">✏️ ~' + estLabel + '</span>';
        if (climaxCount) html += '<span style="font-size:13px;padding:4px 12px;background:rgba(255,152,0,0.15);color:#ff9800;border-radius:6px">⚡ ' + climaxCount + ' 高潮</span>';
        if (hookCount) html += '<span style="font-size:13px;padding:4px 12px;background:rgba(76,175,80,0.15);color:#4caf50;border-radius:6px">⭐ ' + hookCount + ' 名场面</span>';
        html += '</div>';

        // 人物标签
        if (charList.length > 0) {
            html += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">';
            charList.forEach(function(c) { html += '<span style="font-size:13px;padding:4px 12px;background:rgba(33,150,243,0.1);color:#2196f3;border-radius:6px">👤 ' + escAttr(c) + '</span>'; });
            html += '</div>';
        }

        // 卷摘要（可折叠）
        vols.forEach(function(vol, vi) {
            var descId = 'vol-desc-' + idx + '-' + vi;
            var fullDesc = escAttr(vol.arc_description || '');
            var shortDesc = fullDesc.substring(0, 120);
            var needExpand = fullDesc.length > 120;

            html += '<div style="margin-top:10px">';
            html += '<div style="font-size:15px;font-weight:600;color:var(--accent)">📘 第' + vol.volume_number + '卷: ' + escAttr(vol.title || '未命名') + ' <span style="color:var(--text-muted);font-weight:400;font-size:13px">(' + (vol.chapters || []).length + '章)</span></div>';
            if (vol.arc_description) {
                html += '<div style="padding-left:16px;font-size:14px;color:var(--text-muted);line-height:1.7;margin-top:4px">';
                html += '<span id="' + descId + '-short">' + shortDesc + (needExpand ? '...' : '') + '</span>';
                if (needExpand) {
                    html += '<span id="' + descId + '-full" style="display:none">' + fullDesc + '</span>';
                    html += ' <a href="javascript:void(0)" onclick="toggleVolDesc(\'' + descId + '\')" id="' + descId + '-btn" style="color:var(--accent);font-size:12px;white-space:nowrap">展开全部</a>';
                }
                html += '</div>';
            }
            html += '</div>';
        });

        // 展开/折叠章节详情
        html += '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">';
        html += '<button class="version-expand-btn" onclick="toggleVersionDetail(' + idx + ')" style="font-size:14px;padding:6px 12px">▶ 查看章节详情</button>';
        html += '<div class="version-detail" id="ver-detail-' + idx + '">';
        vols.forEach(function(vol, vi) {
            html += '<div style="font-size:15px;font-weight:600;margin:12px 0 6px;color:var(--accent)">📘 第' + vol.volume_number + '卷: ' + escAttr(vol.title || '') + '</div>';
            (vol.chapters || []).forEach(function(ch, ci) {
                var marks = '';
                if (ch.is_climax) marks += '<span style="color:#ff9800">⚡</span>';
                if (ch.is_hook_point) marks += '<span style="color:#4caf50">⭐</span>';
                var summaryId = 'ch-summary-' + idx + '-' + vi + '-' + ci;
                var fullSummary = escAttr(ch.summary || '');
                var shortSummary = fullSummary.substring(0, 80);
                var needSummaryExpand = fullSummary.length > 80;

                html += '<div style="display:flex;gap:8px;padding:6px 0;font-size:14px"><span style="color:var(--text-muted);min-width:50px">Ch' + ch.chapter_number + '</span><span style="flex:1;line-height:1.5">' + marks + ' ' + escAttr(ch.title || '') + '</span></div>';
                if (ch.summary) {
                    html += '<div style="display:flex;gap:8px;padding:2px 0 6px 58px;font-size:13px;color:var(--text-muted);line-height:1.6">';
                    html += '<span id="' + summaryId + '-short" style="flex:1">' + shortSummary + (needSummaryExpand ? '...' : '') + '</span>';
                    if (needSummaryExpand) {
                        html += '<span id="' + summaryId + '-full" style="display:none;flex:1">' + fullSummary + '</span>';
                        html += ' <a href="javascript:void(0)" onclick="toggleChSummary(\'' + summaryId + '\')" id="' + summaryId + '-btn" style="color:var(--accent);font-size:11px;white-space:nowrap">展开</a>';
                    }
                    html += '</div>';
                }
            });
        });
        html += '</div></div>';
        html += '</div>';
    });
    listDiv.innerHTML = html;
    modal.style.display = 'flex';
}

function closeOutlineVersionsModal() {
    document.getElementById('outline-versions-modal').style.display = 'none';
}
function toggleVersionDetail(idx) {
    var el = document.getElementById('ver-detail-' + idx);
    var btn = el.previousElementSibling;
    if (el.classList.contains('open')) { el.classList.remove('open'); btn.textContent = '▶ 查看章节详情'; }
    else { el.classList.add('open'); btn.textContent = '▼ 收起章节详情'; }
}
function toggleVolDesc(descId) {
    var shortEl = document.getElementById(descId + '-short');
    var fullEl = document.getElementById(descId + '-full');
    var btnEl = document.getElementById(descId + '-btn');
    if (!shortEl || !fullEl || !btnEl) return;
    if (fullEl.style.display === 'none') {
        shortEl.style.display = 'none';
        fullEl.style.display = 'inline';
        btnEl.textContent = '收起';
    } else {
        shortEl.style.display = 'inline';
        fullEl.style.display = 'none';
        btnEl.textContent = '展开全部';
    }
}
function toggleChSummary(summaryId) {
    var shortEl = document.getElementById(summaryId + '-short');
    var fullEl = document.getElementById(summaryId + '-full');
    var btnEl = document.getElementById(summaryId + '-btn');
    if (!shortEl || !fullEl || !btnEl) return;
    if (fullEl.style.display === 'none') {
        shortEl.style.display = 'none';
        fullEl.style.display = 'inline';
        btnEl.textContent = '收起';
    } else {
        shortEl.style.display = 'inline';
        fullEl.style.display = 'none';
        btnEl.textContent = '展开';
    }
}
async function applyOutlineVersion(idx) {
    var data = _outlineVersions[idx];
    if (!data || !data.volumes || data.volumes.length === 0) { toast('该方案无有效内容，无法应用', 'error'); return; }
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline/apply', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({data: data})});
        var result = await resp.json();
        if (result.status === 'applied') { toast('已应用方案' + (idx + 1) + ' (' + result.volumes + '卷' + result.chapters + '章)', 'success'); closeOutlineVersionsModal(); _clearOutlineVersions(currentProjectId); _outlineVersions = []; await loadOutline(); }
        else { toast('应用失败', 'error'); }
    } catch(e) { toast('应用失败: ' + e, 'error'); }
}

// ===== Foreshadow =====
async function loadForeshadows() {
    if (!currentProjectId) return;
    var pendingList = document.getElementById('pending-list');
    var torecoverList = document.getElementById('torecover-list');
    var doneList = document.getElementById('done-list');
    pendingList.innerHTML = '<p class="muted">加载中...</p>';
    torecoverList.innerHTML = '<p class="muted">加载中...</p>';
    doneList.innerHTML = '<p class="muted">加载中...</p>';
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/foreshadows/audit');
        var d = await resp.json();
        if (!d || d.total === 0) { pendingList.innerHTML = '<p class="muted">暂无伏笔，点击"添加伏笔"创建</p>'; torecoverList.innerHTML = '<p class="muted">暂无</p>'; doneList.innerHTML = '<p class="muted">暂无</p>'; return; }
        var unpaid = d.unpaid || [];
        var pending = unpaid.filter(function(f) { return f.status === 'planted'; });
        var toRecover = unpaid.filter(function(f) { return f.status === 'building'; });
        var paid = d.paid || [];
        if (pending.length === 0) pendingList.innerHTML = '<p class="muted">暂无</p>';
        else pendingList.innerHTML = pending.map(function(f) { return '<div style="padding:6px;margin:4px 0;background:var(--bg);border-radius:4px;border-left:3px solid var(--bad)"><div style="font-size:12px">' + esc(f.description || '').substring(0, 60) + '</div><div style="font-size:10px;color:var(--text-muted);margin-top:2px">' + (f.type || '') + ' | Ch' + (f.planted_chapter || '?') + '</div></div>'; }).join('');
        if (toRecover.length === 0) torecoverList.innerHTML = '<p class="muted">暂无</p>';
        else torecoverList.innerHTML = toRecover.map(function(f) { return '<div style="padding:6px;margin:4px 0;background:var(--bg);border-radius:4px;border-left:3px solid var(--warn);cursor:pointer" onclick="markForeshadowPaid(\'' + f.foreshadow_id + '\')"><div style="font-size:12px">' + esc(f.description || '').substring(0, 60) + '</div><div style="font-size:10px;color:var(--text-muted);margin-top:2px">' + (f.type || '') + ' | Ch' + (f.planted_chapter || '?') + '<span style="color:var(--ok);margin-left:8px">点击标记回收</span></div></div>'; }).join('');
        if (paid.length === 0) doneList.innerHTML = '<p class="muted">暂无</p>';
        else { doneList.innerHTML = paid.slice(0, 5).map(function(f) { return '<div style="padding:4px;margin:2px 0;font-size:11px;opacity:0.6">✓ ' + (f.description || '').substring(0, 40) + '</div>'; }).join(''); if (paid.length > 5) doneList.innerHTML += '<p class="muted" style="font-size:11px">...还有' + (paid.length - 5) + '个</p>'; }
    } catch(e) { pendingList.innerHTML = '<p class="error">加载失败</p>'; torecoverList.innerHTML = ''; doneList.innerHTML = ''; }
}
function showAddForeshadow() { document.getElementById('foreshadow-add-form').style.display = 'block'; }
function hideAddForeshadow() { document.getElementById('foreshadow-add-form').style.display = 'none'; }
async function addForeshadow() {
    if (!currentProjectId) return;
    var desc = document.getElementById('foreshadow-input').value.trim();
    var type = document.getElementById('foreshadow-type').value;
    if (!desc) { toast('请输入伏笔描述', 'error'); return; }
    try {
        await fetch('/api/v1/projects/' + currentProjectId + '/foreshadows', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({description: desc, type: type})});
        document.getElementById('foreshadow-input').value = ''; loadForeshadows(); toast('伏笔已添加', 'success');
    } catch(e) { toast('添加失败', 'error'); }
}
function markForeshadowPaid(fsId) {
    modalConfirm('标记回收', '确定标记此伏笔为已回收？', async function() {
        try { await fetch('/api/v1/projects/' + currentProjectId + '/foreshadows/' + fsId + '/payoff', {method: 'POST', headers: {'Content-Type': 'application/json'}}); loadForeshadows(); toast('伏笔已标记回收', 'success'); } catch(e) { toast('操作失败', 'error'); }
    });
}

async function addChapter() {
    if (!outlineData || !outlineData.volumes) return;
    modalPrompt('追加章节', '新章节标题:', '新章节', async function(title) {
        if (!title) return;
        var lastVol = outlineData.volumes[outlineData.volumes.length - 1];
        var lastCh = lastVol.chapters[lastVol.chapters.length - 1];
        var newNum = (lastCh ? lastCh.chapter_number : 0) + 1;
        lastVol.chapters.push({chapter_number: newNum, volume_number: lastVol.volume_number, title: title, summary: '', status: 'planned', key_events: [], character_moments: [], is_climax: false, is_hook_point: false});
        await fetch('/api/v1/projects/' + currentProjectId + '/outline', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(outlineData)});
        loadOutline(); toast('已追加第' + newNum + '章', 'success');
    });
}
function editOutline() {
    if (!outlineData || !outlineData.volumes) { toast('暂无大纲', 'info'); return; }
    _openOutlineEditor();
}

function _openOutlineEditor() {
    _closeModal();
    var overlay = document.createElement('div'); overlay.id = '_modal'; overlay.className = 'modal-overlay';
    var box = document.createElement('div'); box.className = 'modal-box';
    box.style.cssText = 'max-width:750px;width:92vw;max-height:88vh;display:flex;flex-direction:column';
    box.innerHTML = '<div class="modal-header" style="font-size:16px">📋 编辑大纲<span style="flex:1"></span><button onclick="_closeModal()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:20px">✕</button></div>' +
        '<div class="modal-body" id="_outline-editor-body" style="flex:1;overflow-y:auto;max-height:70vh;padding:20px"></div>' +
        '<div class="modal-footer"><button class="btn btn-ghost" onclick="_closeModal()">取消</button><button class="btn btn-primary" onclick="_saveOutlineFromModal()" style="padding:8px 24px">💾 保存</button></div>';
    overlay.appendChild(box); document.body.appendChild(overlay);
    _renderOutlineEditor();
}

var _oeDragVi = -1, _oeDragCi = -1;

function _renderOutlineEditor() {
    var body = document.getElementById('_outline-editor-body');
    if (!body) return;
    var html = '';
    outlineData.volumes.forEach(function(vol, vi) {
        html += '<div style="margin-bottom:20px">' +
            '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">' +
            '<span style="font-weight:700;color:var(--accent);font-size:15px">📘 第' + vol.volume_number + '卷</span>' +
            '<input value="' + escAttr(vol.title || '') + '" data-vi="' + vi + '" class="vol-title-input" style="flex:1;background:var(--bg);color:var(--text);border:1px solid var(--accent);border-radius:8px;padding:8px 12px;font-size:14px;outline:none;font-weight:500">' +
            '<button class="btn btn-sm" onclick="_addChapterToVol(' + vi + ')" style="font-size:13px;white-space:nowrap;padding:6px 12px">+ 添加</button></div>';
        var chs = vol.chapters || [];
        if (chs.length === 0) {
            html += '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;border:1px dashed var(--border);border-radius:8px">暂无章节，点击"+ 添加"创建</div>';
        }
        chs.forEach(function(ch, ci) {
            var statusIcon = ch.status === 'completed' ? '✅' : '⬜';
            html += '<div class="edit-ch-card" draggable="true" data-vi="' + vi + '" data-ci="' + ci + '" ' +
                'ondragstart="_oeDragStart(event,' + vi + ',' + ci + ')" ondragend="_oeDragEnd(event)" ondragover="_oeDragOver(event)" ondragleave="_oeDragLeave(event)" ondrop="_oeDrop(event,' + vi + ',' + ci + ')" ' +
                'style="background:var(--surface2);border-radius:8px;padding:10px 12px;margin-bottom:6px;border:1px solid transparent;transition:border-color 0.15s;cursor:grab">' +
                '<div style="display:flex;align-items:center;gap:8px">' +
                '<span style="color:var(--text-muted);font-size:14px;cursor:grab;padding:0 2px" title="拖拽排序">⠿</span>' +
                '<span style="font-size:14px">' + statusIcon + '</span>' +
                '<span style="color:var(--text-muted);font-size:14px;font-weight:600">Ch</span>' +
                '<input value="' + ch.chapter_number + '" data-vi="' + vi + '" data-ci="' + ci + '" data-field="chapter_number" class="ch-num-input" ' +
                    'style="width:48px;background:var(--bg);color:var(--accent);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:14px;font-weight:700;text-align:center;outline:none" placeholder="序号">' +
                '<input value="' + escAttr(ch.title || '') + '" data-vi="' + vi + '" data-ci="' + ci + '" data-field="title" class="ch-title-input" ' +
                    'style="flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:14px;outline:none" placeholder="章节标题">' +
                '<button class="btn btn-sm" onclick="_deleteOutlineChapter(' + vi + ',' + ci + ')" style="color:var(--bad);font-size:13px;cursor:pointer;padding:6px 8px" title="删除此章">✕</button></div>' +
                '<div style="margin-top:6px;margin-left:34px">' +
                '<div contenteditable="true" data-vi="' + vi + '" data-ci="' + ci + '" data-field="summary" class="ch-summary-edit" ' +
                    'style="background:var(--bg);color:var(--text-muted);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:13px;line-height:1.6;min-height:22px;outline:none;white-space:pre-wrap;word-break:break-all" ' +
                    'data-placeholder="章节概要（可选）">' + esc(ch.summary || '') + '</div></div></div>';
        });
        html += '</div>';
    });
    body.innerHTML = html;
    // 给 contenteditable 占位符效果
    body.querySelectorAll('.ch-summary-edit').forEach(function(el) {
        if (!el.textContent.trim()) { el.innerHTML = ''; }
        el.addEventListener('focus', function() { if (!this.textContent.trim()) this.innerHTML = ''; });
        el.addEventListener('blur', function() { if (!this.textContent.trim()) this.innerHTML = ''; });
    });
}

// ===== 拖拽排序 =====
function _oeDragStart(e, vi, ci) {
    _oeDragVi = vi; _oeDragCi = ci;
    e.dataTransfer.effectAllowed = 'move';
    e.target.style.opacity = '0.5';
    setTimeout(function() { e.target.style.borderColor = 'var(--accent)'; }, 0);
}
function _oeDragEnd(e) {
    e.target.style.opacity = '';
    e.target.style.borderColor = 'transparent';
}
function _oeDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    var card = e.target.closest('.edit-ch-card');
    if (card) card.style.borderColor = 'var(--accent)';
}
function _oeDragLeave(e) {
    var card = e.target.closest('.edit-ch-card');
    if (card) card.style.borderColor = 'transparent';
}
function _oeDrop(e, targetVi, targetCi) {
    e.preventDefault();
    var card = e.target.closest('.edit-ch-card');
    if (card) card.style.borderColor = 'transparent';
    if (_oeDragVi < 0) return;
    if (_oeDragVi !== targetVi) { _oeDragVi = -1; return; } // 只支持同卷内拖拽
    if (_oeDragCi === targetCi) { _oeDragVi = -1; return; }
    _syncOutlineFromInputs();
    var chs = outlineData.volumes[_oeDragVi].chapters;
    var item = chs.splice(_oeDragCi, 1)[0];
    chs.splice(targetCi, 0, item);
    _oeDragVi = -1; _oeDragCi = -1;
    _renderOutlineEditor();
}

function _addChapterToVol(vi) {
    var chs = outlineData.volumes[vi].chapters;
    var maxNum = 0; chs.forEach(function(c) { if (c.chapter_number > maxNum) maxNum = c.chapter_number; });
    _syncOutlineFromInputs();
    chs.push({chapter_number: maxNum + 1, volume_number: outlineData.volumes[vi].volume_number, title: '新章节', summary: '', status: 'planned', key_events: [], character_moments: [], is_climax: false, is_hook_point: false});
    _renderOutlineEditor();
    var body = document.getElementById('_outline-editor-body');
    if (body) body.scrollTop = body.scrollHeight;
}

function _deleteOutlineChapter(vi, ci) {
    var ch = outlineData.volumes[vi].chapters[ci];
    modalConfirm('删除章节', '确定从大纲中删除「Ch' + ch.chapter_number + ': ' + (ch.title || '') + '」？', function() {
        _syncOutlineFromInputs();
        outlineData.volumes[vi].chapters.splice(ci, 1);
        _renderOutlineEditor();
    }, {danger: true, okText: '删除'});
}

function _syncOutlineFromInputs() {
    var body = document.getElementById('_outline-editor-body');
    if (!body) return;
    body.querySelectorAll('.vol-title-input').forEach(function(inp) {
        var vi = parseInt(inp.getAttribute('data-vi'));
        outlineData.volumes[vi].title = inp.value;
    });
    body.querySelectorAll('.ch-num-input').forEach(function(inp) {
        var vi = parseInt(inp.getAttribute('data-vi')), ci = parseInt(inp.getAttribute('data-ci'));
        var num = parseInt(inp.value);
        if (!isNaN(num) && num > 0) outlineData.volumes[vi].chapters[ci].chapter_number = num;
    });
    body.querySelectorAll('.ch-title-input').forEach(function(inp) {
        var vi = parseInt(inp.getAttribute('data-vi')), ci = parseInt(inp.getAttribute('data-ci'));
        outlineData.volumes[vi].chapters[ci].title = inp.value;
    });
    body.querySelectorAll('.ch-summary-edit').forEach(function(el) {
        var vi = parseInt(el.getAttribute('data-vi')), ci = parseInt(el.getAttribute('data-ci'));
        outlineData.volumes[vi].chapters[ci].summary = el.textContent;
    });
}

async function _saveOutlineFromModal() {
    _syncOutlineFromInputs();
    try {
        await fetch('/api/v1/projects/' + currentProjectId + '/outline', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(outlineData)});
        _closeModal();
        loadOutline();
        toast('大纲已保存', 'success');
    } catch(e) { toast('保存失败', 'error'); }
}
async function saveOutlineEdit() {
    try { await fetch('/api/v1/projects/' + currentProjectId + '/outline', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(outlineData)}); _editing = false; loadOutline(); document.getElementById('btn-edit-ol').textContent = '✏️'; toast('大纲已保存', 'success'); } catch(e) { toast('保存失败', 'error'); }
}
async function loadCharactersMini() {
    var container = document.getElementById('characters-mini');
    container.innerHTML = '<p class="muted">加载中...</p>';
    if (!currentProjectId) { container.innerHTML = '<p class="muted">暂无</p>'; return; }
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/characters');
        var data = await resp.json();
        var chars = data.characters || {};
        if (Object.keys(chars).length === 0) {
            var graphResp = await fetch('/api/v1/graph/export?project_id=' + currentProjectId);
            var graphData = await graphResp.json();
            (graphData.nodes || []).forEach(function(n) {
                if (n.group === 'Character') chars[n.id] = {name: n.label || (n.properties && n.properties.name) || '?', personality_tags: (n.properties && n.properties.personality_tags) || [], core_motivation: (n.properties && n.properties.core_motivation) || ''};
            });
        }
        var charList = Object.values(chars).slice(0, 8);
        if (charList.length === 0) { document.getElementById('characters-mini').innerHTML = '<p class="muted">暂无</p>'; return; }
        document.getElementById('characters-mini').innerHTML = charList.map(function(c) {
            var name = c.name || '?';
            var tags = (c.personality_tags || []).slice(0, 3).join('、');
            var motivation = c.core_motivation || '';
            return '<div style="padding:8px 0;border-bottom:1px solid var(--border)"><strong style="font-size:14px">' + name + '</strong>' + (tags ? '<span class="muted" style="font-size:12px"> · ' + tags + '</span>' : '') + (motivation ? '<div class="muted" style="font-size:12px;margin-top:3px;line-height:1.4">' + motivation.slice(0, 50) + '</div>' : '') + '</div>';
        }).join('');
    } catch(e) {}
}
async function buildGraph() {
    if (!currentProjectId) return;
    try {
        var resp = await fetch('/api/v1/graph/build?project_id=' + currentProjectId, {method: 'POST'});
        var d = await resp.json();
        toast('图谱已构建: ' + (d.nodes_added || 0) + '节点 ' + (d.edges_added || 0) + '边', 'success');
        loadMiniGraph();
    } catch(e) { toast('图谱构建失败', 'error'); }
}

// ===== 章节版本历史 =====
var _chVersions = [];
var _chDiffMode = false;
var _chDiffSelected = [];

var _chCurrentVersionId = null;

async function showChapterVersions() {
    if (!currentProjectId || !currentChapterNum) return;
    document.getElementById('ch-version-title').textContent = '第' + currentVolumeNum + '卷第' + currentChapterNum + '章';
    document.getElementById('ch-version-modal').style.display = 'flex';
    document.getElementById('ch-version-list').innerHTML = '<p class="muted" style="text-align:center;padding:20px">加载中...</p>';
    document.getElementById('ch-version-preview').innerHTML = '<p class="muted" style="text-align:center;padding:40px">选择一个版本查看内容</p>';
    _chDiffMode = false;
    _chDiffSelected = [];
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + currentChapterNum + '/versions?volume=' + currentVolumeNum);
        var data = await resp.json();
        _chVersions = data.versions || [];
        _chCurrentVersionId = data.current_version_id || null;
        document.getElementById('ch-version-stats').textContent = '共 ' + _chVersions.length + ' 个历史版本';
        renderChVersionList();
    } catch(e) {
        document.getElementById('ch-version-list').innerHTML = '<p class="muted" style="color:var(--bad)">加载失败</p>';
    }
}

function renderChVersionList() {
    var html = '<div style="margin-bottom:8px;display:flex;gap:4px">' +
        '<button class="btn btn-sm" onclick="chVersionToggleDiff()" id="btn-ch-diff" style="font-size:10px">⚖️ 对比模式</button>' +
        '</div>';
    if (_chVersions.length === 0) {
        html += '<p class="muted" style="text-align:center;padding:20px">暂无历史版本</p>';
    }
    _chVersions.forEach(function(v, i) {
        var src = v.source || 'manual';
        var srcLabel = {manual:'手动保存', generate:'AI生成', optimize:'优化', rollback:'回滚', pre_rollback:'回滚前'}[src] || src;
        var srcColor = {generate:'var(--accent)', optimize:'var(--ok)', rollback:'var(--warn)', pre_rollback:'var(--text-muted)'}[src] || 'var(--text-muted)';
        var selected = _chDiffSelected.indexOf(v.id) >= 0;
        var isCurrent = _chCurrentVersionId && v.id === _chCurrentVersionId;
        html += '<div class="version-card' + (selected ? ' selected' : '') + '" style="' + (selected ? 'border-color:var(--accent);background:rgba(124,92,252,0.05)' : '') + (isCurrent ? 'border:2px solid var(--ok);' : '') + '" onclick="loadChVersion(' + v.id + ',' + i + ')">';
        html += '<h4><span>v' + v.id + '</span>';
        if (isCurrent) html += '<span style="font-size:10px;background:var(--ok);color:#fff;padding:1px 6px;border-radius:8px;margin-left:6px">当前使用</span>';
        html += '<span style="font-size:10px;color:var(--text-muted)">' + (v.word_count || 0) + '字</span></h4>';
        html += '<div class="vol-info">' + (v.created_at || '') + '</div>';
        html += '<div class="version-meta"><span class="tag" style="color:' + srcColor + '">' + srcLabel + '</span>';
        if (v.change_summary) html += '<span class="tag accent">' + esc(v.change_summary).substring(0, 30) + '</span>';
        html += '</div>';
        if (_chDiffMode) {
            html += '<div style="margin-top:6px"><label style="font-size:10px;cursor:pointer"><input type="checkbox" ' + (selected ? 'checked' : '') + ' onclick="event.stopPropagation();chVersionToggleSelect(' + v.id + ')" style="margin-right:4px">选择对比</label></div>';
        }
        html += '</div>';
    });
    document.getElementById('ch-version-list').innerHTML = html;
}

async function loadChVersion(versionId, idx) {
    if (_chDiffMode) {
        chVersionToggleSelect(versionId);
        return;
    }
    var preview = document.getElementById('ch-version-preview');
    preview.innerHTML = '<p class="muted" style="text-align:center;padding:20px">加载中...</p>';
    try {
        var resp = await fetch('/api/v1/versions/chapter/' + versionId);
        var data = await resp.json();
        var content = data.content || '';
        var html = '<div style="margin-bottom:12px;padding:8px;background:var(--surface2);border-radius:6px;font-size:12px">';
        html += '<strong>版本 #' + versionId + '</strong>';
        html += ' · ' + (data.word_count || content.length) + '字';
        html += ' · ' + (data.created_at || '');
        html += ' · <span style="color:var(--accent)">' + (data.source || '') + '</span>';
        if (data.change_summary) html += '<br><span style="color:var(--text-muted)">' + esc(data.change_summary) + '</span>';
        html += '</div>';
        html += '<div style="white-space:pre-wrap;font-size:13px;line-height:1.8;background:var(--bg);padding:12px;border-radius:6px;max-height:calc(80vh - 200px);overflow-y:auto">' + esc(content) + '</div>';
        html += '<div style="margin-top:8px;display:flex;gap:8px">';
        var isCurrent = _chCurrentVersionId && versionId === _chCurrentVersionId;
        if (!isCurrent) html += '<button class="btn btn-primary btn-sm" onclick="rollbackChVersion(' + versionId + ')">🔄 回滚到此版本</button>';
        else html += '<span style="font-size:12px;color:var(--ok);padding:4px 0">✓ 这是当前使用的版本</span>';
        html += '<button class="btn btn-sm" style="color:var(--bad)" onclick="deleteChVersion(' + versionId + ')">🗑️ 删除</button>';
        html += '</div>';
        preview.innerHTML = html;
    } catch(e) {
        preview.innerHTML = '<p class="muted" style="color:var(--bad)">加载失败</p>';
    }
}

function chVersionToggleDiff() {
    _chDiffMode = !_chDiffMode;
    _chDiffSelected = [];
    var btn = document.getElementById('btn-ch-diff');
    if (btn) btn.className = _chDiffMode ? 'btn btn-sm btn-primary' : 'btn btn-sm';
    renderChVersionList();
    if (!_chDiffMode) {
        document.getElementById('ch-version-preview').innerHTML = '<p class="muted" style="text-align:center;padding:40px">选择一个版本查看内容</p>';
    }
}

function chVersionToggleSelect(versionId) {
    var idx = _chDiffSelected.indexOf(versionId);
    if (idx >= 0) {
        _chDiffSelected.splice(idx, 1);
    } else {
        if (_chDiffSelected.length >= 2) _chDiffSelected.shift();
        _chDiffSelected.push(versionId);
    }
    renderChVersionList();
    if (_chDiffSelected.length === 2) {
        loadChVersionDiff(_chDiffSelected[0], _chDiffSelected[1]);
    }
}

async function loadChVersionDiff(v1, v2) {
    var preview = document.getElementById('ch-version-preview');
    preview.innerHTML = '<p class="muted" style="text-align:center;padding:20px">对比中...</p>';
    try {
        var resp = await fetch('/api/v1/versions/chapter/' + v1 + '/diff/' + v2);
        var data = await resp.json();
        var items = data.diff_items || [];
        var stats = data.stats || {};
        var html = '<div style="margin-bottom:8px;padding:6px 10px;background:var(--surface2);border-radius:6px;font-size:11px">';
        html += '版本 #' + v1 + ' vs #' + v2;
        html += ' · <span style="color:var(--ok)">+' + (stats.added || 0) + '</span>';
        html += ' · <span style="color:var(--bad)">-' + (stats.removed || 0) + '</span>';
        html += ' · <span style="color:var(--text-muted)">' + (stats.unchanged || 0) + ' 不变</span>';
        html += '</div>';
        html += '<div style="background:var(--bg);padding:12px;border-radius:6px;max-height:calc(80vh - 200px);overflow-y:auto;font-size:13px;line-height:1.8;font-family:monospace">';
        items.forEach(function(item) {
            if (item.type === 'equal') {
                html += '<div style="color:var(--text-muted);padding:2px 0">' + esc(item.text) + '</div>';
            } else if (item.type === 'add') {
                html += '<div style="color:var(--ok);background:rgba(76,175,80,0.1);padding:2px 4px;border-left:3px solid var(--ok)">+ ' + esc(item.text) + '</div>';
            } else if (item.type === 'del') {
                html += '<div style="color:var(--bad);background:rgba(244,67,54,0.1);padding:2px 4px;border-left:3px solid var(--bad)">- ' + esc(item.text) + '</div>';
            }
        });
        html += '</div>';
        preview.innerHTML = html;
    } catch(e) {
        preview.innerHTML = '<p class="muted" style="color:var(--bad)">对比失败</p>';
    }
}

async function rollbackChVersion(versionId) {
    if (!confirm('确定要回滚到版本 #' + versionId + ' 吗？当前内容会先自动保存为历史版本。')) return;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + currentChapterNum + '/versions/' + versionId + '/rollback?volume=' + currentVolumeNum, {method: 'POST'});
        var data = await resp.json();
        if (data.status === 'rolled_back') {
            toast('已回滚到版本 #' + versionId + '，' + (data.word_count || 0) + '字', 'success');
            // 重新加载章节内容
            if (typeof loadChapter === 'function') loadChapter(currentChapterNum, currentVolumeNum);
            // 刷新版本列表（更新当前版本标识）
            showChapterVersions();
        } else {
            toast('回滚失败', 'error');
        }
    } catch(e) {
        toast('回滚失败: ' + e.message, 'error');
    }
}

async function deleteChVersion(versionId) {
    if (!confirm('确定要删除版本 #' + versionId + ' 吗？此操作不可恢复。')) return;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/chapters/' + currentChapterNum + '/versions/' + versionId, {method: 'DELETE'});
        var data = await resp.json();
        if (data.status === 'deleted') {
            toast('版本 #' + versionId + ' 已删除', 'success');
            // 刷新列表和预览
            showChapterVersions();
        } else {
            toast('删除失败', 'error');
        }
    } catch(e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

function closeChVersionModal() {
    document.getElementById('ch-version-modal').style.display = 'none';
    _chDiffMode = false;
    _chDiffSelected = [];
}

// ===== 大纲版本历史 =====
var _olVersionList = [];
var _olCurrentVersionId = null;

async function showOutlineVersions() {
    if (!currentProjectId) return;
    document.getElementById('ol-version-modal').style.display = 'flex';
    document.getElementById('ol-version-list').innerHTML = '<p class="muted" style="text-align:center;padding:20px">加载中...</p>';
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline/versions');
        var data = await resp.json();
        _olVersionList = data.versions || [];
        _olCurrentVersionId = data.current_version_id || null;
        document.getElementById('ol-version-count').textContent = '共 ' + _olVersionList.length + ' 个历史版本';
        renderOlVersionList();
    } catch(e) {
        document.getElementById('ol-version-list').innerHTML = '<p class="muted" style="color:var(--bad)">加载失败</p>';
    }
}

function renderOlVersionList() {
    var html = '';
    if (_olVersionList.length === 0) {
        html += '<p class="muted" style="text-align:center;padding:20px">暂无历史版本</p>';
    }
    _olVersionList.forEach(function(v) {
        var src = v.source || 'manual';
        var srcLabel = {manual:'手动保存', generate:'生成', pre_generate:'生成前', apply:'应用方案', pre_apply:'应用前', rollback:'回滚', pre_rollback:'回滚前'}[src] || src;
        var isCurrent = _olCurrentVersionId && v.id === _olCurrentVersionId;
        html += '<div class="version-card" style="' + (isCurrent ? 'border:2px solid var(--ok);' : '') + '">';
        html += '<h4><span>v' + v.id + '</span>';
        if (isCurrent) html += '<span style="font-size:10px;background:var(--ok);color:#fff;padding:1px 6px;border-radius:8px;margin-left:6px">当前使用</span>';
        html += '<span style="font-size:10px;color:var(--text-muted)">' + (v.volumes_count || 0) + '卷/' + (v.chapters_count || 0) + '章</span></h4>';
        html += '<div class="vol-info">' + (v.created_at || '') + '</div>';
        html += '<div class="version-meta"><span class="tag">' + srcLabel + '</span>';
        if (v.change_summary) html += '<span class="tag accent">' + esc(v.change_summary).substring(0, 40) + '</span>';
        html += '</div>';
        html += '<div style="margin-top:8px;display:flex;gap:6px">';
        html += '<button class="btn btn-sm" onclick="previewOlVersion(' + v.id + ')">👁️ 预览</button>';
        if (!isCurrent) html += '<button class="btn btn-primary btn-sm" onclick="rollbackOlVersion(' + v.id + ')">🔄 回滚</button>';
        html += '<button class="btn btn-sm" style="color:var(--bad)" onclick="deleteOlVersion(' + v.id + ')">🗑️ 删除</button>';
        html += '</div>';
        html += '</div>';
    });
    document.getElementById('ol-version-list').innerHTML = html;
}

async function previewOlVersion(versionId) {
    try {
        var resp = await fetch('/api/v1/versions/outline/' + versionId);
        var data = await resp.json();
        var outline = data.outline_data || {};
        var volumes = outline.volumes || [];
        var html = '<div style="padding:8px;background:var(--bg);border-radius:6px;max-height:300px;overflow-y:auto;font-size:12px">';
        volumes.forEach(function(vol) {
            html += '<div style="margin-bottom:8px"><strong>第' + (vol.volume_number || '?') + '卷: ' + esc(vol.title || '无标题') + '</strong>';
            if (vol.arc_description) html += '<div style="color:var(--text-muted);font-size:11px;margin-top:2px">' + esc(vol.arc_description).substring(0, 100) + '</div>';
            html += '</div>';
            (vol.chapters || []).forEach(function(ch) {
                html += '<div style="padding:2px 0 2px 12px;font-size:11px;border-left:2px solid var(--border);margin-left:4px">';
                html += '<span style="color:var(--text-muted)">Ch' + (ch.chapter_number || '?') + '</span> ';
                html += esc(ch.title || '无标题');
                if (ch.summary) html += '<span style="color:var(--text-muted)"> — ' + esc(ch.summary).substring(0, 50) + '</span>';
                html += '</div>';
            });
        });
        html += '</div>';
        _modal({icon: '👁️', title: '大纲版本 #' + versionId + ' 预览', body: html, okText: '关闭', cancelText: false, onOk: null});
    } catch(e) {
        toast('预览失败', 'error');
    }
}

async function rollbackOlVersion(versionId) {
    if (!confirm('确定要回滚大纲到版本 #' + versionId + ' 吗？当前大纲会先自动保存为历史版本。')) return;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline/versions/' + versionId + '/rollback', {method: 'POST'});
        var data = await resp.json();
        if (data.status === 'rolled_back') {
            toast('大纲已回滚到版本 #' + versionId, 'success');
            // 重新加载大纲
            if (typeof loadOutline === 'function') loadOutline();
            // 刷新版本列表（更新当前版本标识）
            showOutlineVersions();
        } else {
            toast('回滚失败', 'error');
        }
    } catch(e) {
        toast('回滚失败: ' + e.message, 'error');
    }
}

async function deleteOlVersion(versionId) {
    if (!confirm('确定要删除版本 #' + versionId + ' 吗？此操作不可恢复。')) return;
    try {
        var resp = await fetch('/api/v1/projects/' + currentProjectId + '/outline/versions/' + versionId, {method: 'DELETE'});
        var data = await resp.json();
        if (data.status === 'deleted') {
            toast('版本 #' + versionId + ' 已删除', 'success');
            // 刷新列表
            showOutlineVersions();
        } else {
            toast('删除失败', 'error');
        }
    } catch(e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

function closeOlVersionModal() {
    document.getElementById('ol-version-modal').style.display = 'none';
}
