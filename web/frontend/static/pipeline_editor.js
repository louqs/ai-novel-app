// ===== pipeline_editor.js — 统一编辑优化工作流 =====

var peState = {
    projectId: '',
    chapterNum: 0,
    stepsResult: null,
    currentStep: '',
    diffItems: [],
    editingIndex: -1,
    source: 'project',  // project, paste, file
    batchChapters: [],
    batchRunning: false,
    batchCancelled: false,
    fileContent: '',
};

// ===== 初始化 =====
window.addEventListener('DOMContentLoaded', async function() {
    await peLoadProjects();
    // 粘贴文本字数统计
    var pasteArea = document.getElementById('pe-paste-content');
    if (pasteArea) {
        pasteArea.addEventListener('input', function() {
            document.getElementById('pe-paste-char-count').textContent = this.value.length + ' 字';
        });
    }
    // 文件拖拽
    var uploadArea = document.getElementById('pe-upload-area');
    if (uploadArea) {
        uploadArea.addEventListener('dragover', function(e) {
            e.preventDefault();
            this.classList.add('dragover');
        });
        uploadArea.addEventListener('dragleave', function() {
            this.classList.remove('dragover');
        });
        uploadArea.addEventListener('drop', function(e) {
            e.preventDefault();
            this.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                peProcessFile(e.dataTransfer.files[0]);
            }
        });
    }
});

// ===== 来源切换 =====
function peSwitchSource(source) {
    peState.source = source;
    // 更新标签样式
    document.querySelectorAll('.source-tab').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById('source-' + source).classList.add('active');
    // 显示对应面板
    ['project', 'paste', 'file'].forEach(function(s) {
        var panel = document.getElementById('source-panel-' + s);
        if (panel) panel.style.display = (s === source) ? 'block' : 'none';
    });
}

// ===== 审查范围切换 =====
function peUpdateRange() {
    var range = document.getElementById('pe-range').value;
    var singleRow = document.getElementById('pe-single-chapter-row');
    var batchPanel = document.getElementById('pe-batch-chapter-panel');

    if (range === 'single') {
        singleRow.style.display = 'flex';
        batchPanel.style.display = 'none';
    } else {
        singleRow.style.display = 'none';
        batchPanel.style.display = 'block';
        peLoadBatchChapters();
    }
}

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

    // 加载章节列表
    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/outline');
        var data = await resp.json();
        var volumes = data.volumes || [];
        var chapters = [];
        volumes.forEach(function(vol) {
            (vol.chapters || []).forEach(function(ch) {
                chapters.push({
                    chapter_number: ch.chapter_number,
                    title: ch.title || '',
                    volume_number: vol.volume_number || 1,
                    status: ch.status || 'pending'
                });
            });
        });
        peState.batchChapters = chapters;
        peUpdateChapterSelect(chapters);
    } catch(e) {
        // 从数据库加载
        try {
            var resp2 = await fetch('/api/v1/projects/' + pid + '/chapters/list');
            var chs = await resp2.json();
            peState.batchChapters = (chs || []).map(function(ch) {
                return {
                    chapter_number: ch.chapter_number,
                    title: ch.title || '',
                    volume_number: ch.volume_number || 1,
                    status: ch.content ? 'completed' : 'pending'
                };
            });
            peUpdateChapterSelect(peState.batchChapters);
        } catch(e2) {}
    }

    // 如果是批量模式，刷新列表
    var range = document.getElementById('pe-range').value;
    if (range !== 'single') {
        peRenderBatchChapterList();
    }
}

function peUpdateChapterSelect(chapters) {
    var chSel = document.getElementById('pe-chapter');
    chSel.innerHTML = '<option value="">选择章节...</option>';
    chapters.forEach(function(ch) {
        var num = ch.chapter_number;
        var title = ch.title || '';
        chSel.innerHTML += '<option value="' + num + '">' + num + '. ' + esc(title) + '</option>';
    });
}

function peSelectChapter() {
    peState.chapterNum = parseInt(document.getElementById('pe-chapter').value) || 0;
}

// ===== 批量章节列表 =====
async function peLoadBatchChapters() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) return;

    // 如果已有数据，直接渲染
    if (peState.batchChapters.length > 0) {
        peRenderBatchChapterList();
        return;
    }

    // 否则加载数据
    await peLoadProject(pid);
}

function peRenderBatchChapterList() {
    var container = document.getElementById('pe-batch-chapter-list');
    var range = document.getElementById('pe-range').value;
    var html = '';

    peState.batchChapters.forEach(function(ch, i) {
        var show = false;
        if (range === 'all') show = true;
        else if (range === 'selected') show = true;
        else if (range === 'unchecked') show = (ch.status !== 'completed');

        if (!show) return;

        var checked = (range === 'all' || range === 'selected') ? 'checked' : '';
        var statusIcon = ch.status === 'completed' ? '✅' : '⬜';

        html += '<div class="batch-item">';
        html += '<input type="checkbox" class="pe-batch-cb" data-idx="' + i + '" ' + checked + ' onchange="peUpdateBatchSelected()">';
        html += '<span class="status-icon">' + statusIcon + '</span>';
        html += '<span class="chapter-name">' + ch.chapter_number + '. ' + esc(ch.title) + '</span>';
        html += '</div>';
    });

    container.innerHTML = html || '<p class="muted" style="padding:12px">暂无章节</p>';
    peUpdateBatchSelected();
}

function peUpdateBatchSelected() {
    var checked = document.querySelectorAll('.pe-batch-cb:checked').length;
    document.getElementById('pe-batch-selected').textContent = checked;
}

function peBatchSelectAll() {
    document.querySelectorAll('.pe-batch-cb').forEach(function(cb) { cb.checked = true; });
    peUpdateBatchSelected();
}

function peBatchSelectNone() {
    document.querySelectorAll('.pe-batch-cb').forEach(function(cb) { cb.checked = false; });
    peUpdateBatchSelected();
}

// ===== 粘贴文本 =====
function peClearPaste() {
    document.getElementById('pe-paste-content').value = '';
    document.getElementById('pe-paste-char-count').textContent = '0 字';
}

// ===== 文件导入 =====
function peHandleFileSelect(event) {
    var file = event.target.files[0];
    if (file) peProcessFile(file);
}

function peProcessFile(file) {
    var ext = '.' + file.name.split('.').pop().toLowerCase();
    if (['.txt', '.md'].indexOf(ext) === -1) {
        toast('不支持的文件格式，请使用 .txt 或 .md 文件', 'error');
        return;
    }

    var reader = new FileReader();
    reader.onload = function(e) {
        peState.fileContent = e.target.result;
        document.getElementById('pe-file-info').style.display = 'block';
        document.getElementById('pe-file-name').textContent = file.name;
        document.getElementById('pe-file-size').textContent = (file.size / 1024).toFixed(1) + ' KB | ' + peState.fileContent.length + ' 字';
    };
    reader.readAsText(file);
}

function peClearFile() {
    peState.fileContent = '';
    document.getElementById('pe-file-info').style.display = 'none';
    document.getElementById('pe-file-input').value = '';
}

// ===== 统一启动入口 =====
async function peStart() {
    var source = peState.source;

    if (source === 'project') {
        var range = document.getElementById('pe-range').value;
        if (range === 'single') {
            await peStartSingle();
        } else {
            await peStartBatch();
        }
    } else if (source === 'paste') {
        await peStartForText();
    } else if (source === 'file') {
        await peStartForFile();
    }
}

// ===== 单章优化 =====
async function peStartSingle() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    var chNum = parseInt(document.getElementById('pe-chapter').value) || 0;

    if (!pid || !chNum) {
        toast('请选择项目和章节', 'error');
        return;
    }

    peState.projectId = pid;
    peState.chapterNum = chNum;

    var steps = peGetSteps();
    if (!steps) return;

    var threshold = parseInt(document.getElementById('pe-threshold').value) || 30;
    var humMode = document.getElementById('pe-hum-mode').value;

    peSetRunning(true);
    peMarkStepsRunning(steps);
    peUpdateLoadingMsg('正在获取章节内容...');

    // 设置超时（120秒）
    var timeoutId = setTimeout(function() {
        if (peState.batchRunning) {
            peUpdateLoadingMsg('处理时间较长，请耐心等待...');
        }
    }, 30000);

    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/optimize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                chapter_num: chNum,
                steps: steps,
                ai_threshold: threshold / 100,
                humanize_mode: humMode,
            })
        });

        clearTimeout(timeoutId);

        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || '请求失败');
        }

        var result = await resp.json();
        peHandleResult(result, steps);
        document.getElementById('pe-status').textContent = '✅ 完成';
        toast('优化完成', 'success');
    } catch(e) {
        clearTimeout(timeoutId);
        document.getElementById('pe-status').textContent = '❌ ' + e.message;
        toast('优化失败: ' + e.message, 'error');
        peMarkStepsError(steps);
    } finally {
        peSetRunning(false);
    }
}

// ===== 批量审查 =====
async function peStartBatch() {
    var pid = peState.projectId || document.getElementById('pe-project').value;
    if (!pid) {
        toast('请选择项目', 'error');
        return;
    }

    // 获取选中的章节
    var selectedChapters = [];
    document.querySelectorAll('.pe-batch-cb:checked').forEach(function(cb) {
        var idx = parseInt(cb.getAttribute('data-idx'));
        if (peState.batchChapters[idx]) {
            selectedChapters.push(peState.batchChapters[idx]);
        }
    });

    if (selectedChapters.length === 0) {
        toast('请至少选择一个章节', 'error');
        return;
    }

    var steps = peGetSteps();
    if (!steps) return;

    var threshold = parseInt(document.getElementById('pe-threshold').value) || 30;

    peState.batchRunning = true;
    peState.batchCancelled = false;
    peSetRunning(true);
    document.getElementById('pe-batch-progress').style.display = 'block';

    var total = selectedChapters.length;
    var done = 0;
    var failed = 0;
    var resultsHtml = '';

    for (var i = 0; i < selectedChapters.length; i++) {
        if (peState.batchCancelled) break;

        var ch = selectedChapters[i];
        var chNum = ch.chapter_number;

        // 更新进度
        document.getElementById('pe-batch-progress-text').textContent = done + '/' + total;
        document.getElementById('pe-batch-bar').style.width = Math.round(done / total * 100) + '%';

        // 添加到进度列表
        resultsHtml += '<div class="batch-item">';
        resultsHtml += '<span class="status-icon">⏳</span>';
        resultsHtml += '<span class="chapter-name">' + chNum + '. ' + esc(ch.title) + '</span>';
        resultsHtml += '<span class="score">执行中...</span>';
        resultsHtml += '</div>';
        document.getElementById('pe-batch-items').innerHTML = resultsHtml;

        try {
            var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/optimize', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    chapter_num: chNum,
                    steps: steps,
                    ai_threshold: threshold / 100,
                    humanize_mode: 'standard',
                })
            });

            if (!resp.ok) throw new Error('请求失败');

            var result = await resp.json();
            done++;

            // 更新进度列表最后一项
            var lastItem = document.querySelector('#pe-batch-items .batch-item:last-child');
            if (lastItem) {
                var score = '';
                if (result.steps && result.steps.length > 0) {
                    var lastStep = result.steps[result.steps.length - 1];
                    if (lastStep.step === 'detect') {
                        score = 'AI率: ' + ((lastStep.ai_score_after || 0) * 100).toFixed(0) + '%';
                    }
                }
                lastItem.querySelector('.status-icon').textContent = '✅';
                lastItem.querySelector('.score').textContent = score || '完成';
            }

            // 显示最后一个结果
            if (i === selectedChapters.length - 1) {
                peHandleResult(result, steps);
            }
        } catch(e) {
            failed++;
            done++;
            var lastItem = document.querySelector('#pe-batch-items .batch-item:last-child');
            if (lastItem) {
                lastItem.querySelector('.status-icon').textContent = '❌';
                lastItem.querySelector('.score').textContent = '失败';
            }
        }
    }

    peState.batchRunning = false;
    document.getElementById('pe-batch-progress-text').textContent = done + '/' + total;
    document.getElementById('pe-batch-bar').style.width = '100%';
    document.getElementById('pe-status').textContent = '✅ 完成 ' + done + '章，失败 ' + failed + '章';
    peSetRunning(false);
    toast('批量审查完成', 'success');
}

function peCancel() {
    peState.batchCancelled = true;
    document.getElementById('pe-status').textContent = '正在停止...';
}

// ===== 粘贴文本优化 =====
async function peStartForText() {
    var content = document.getElementById('pe-paste-content').value.trim();
    if (!content) {
        toast('请粘贴文本', 'error');
        return;
    }

    var steps = peGetSteps();
    if (!steps) return;

    var threshold = parseInt(document.getElementById('pe-threshold').value) || 30;
    var humMode = document.getElementById('pe-hum-mode').value;
    var platform = document.getElementById('pe-paste-platform').value;

    peSetRunning(true);
    peMarkStepsRunning(steps);

    try {
        var resp = await fetch('/api/v1/pipeline/optimize-text', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                content: content,
                platform: platform,
                steps: steps,
                ai_threshold: threshold / 100,
                humanize_mode: humMode,
            })
        });

        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || '请求失败');
        }

        var result = await resp.json();
        peHandleResult(result, steps);
        document.getElementById('pe-status').textContent = '✅ 完成';
        toast('优化完成', 'success');
    } catch(e) {
        document.getElementById('pe-status').textContent = '❌ ' + e.message;
        toast('优化失败: ' + e.message, 'error');
        peMarkStepsError(steps);
    } finally {
        peSetRunning(false);
    }
}

// ===== 文件导入优化 =====
async function peStartForFile() {
    var content = peState.fileContent;
    if (!content) {
        toast('请先选择文件', 'error');
        return;
    }

    var steps = peGetSteps();
    if (!steps) return;

    var threshold = parseInt(document.getElementById('pe-threshold').value) || 30;
    var humMode = document.getElementById('pe-hum-mode').value;
    var platform = document.getElementById('pe-file-platform').value;

    peSetRunning(true);
    peMarkStepsRunning(steps);

    try {
        var resp = await fetch('/api/v1/pipeline/optimize-text', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                content: content,
                platform: platform,
                steps: steps,
                ai_threshold: threshold / 100,
                humanize_mode: humMode,
            })
        });

        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || '请求失败');
        }

        var result = await resp.json();
        peHandleResult(result, steps);
        document.getElementById('pe-status').textContent = '✅ 完成';
        toast('优化完成', 'success');
    } catch(e) {
        document.getElementById('pe-status').textContent = '❌ ' + e.message;
        toast('优化失败: ' + e.message, 'error');
        peMarkStepsError(steps);
    } finally {
        peSetRunning(false);
    }
}

// ===== 通用工具函数 =====
function peGetSteps() {
    var steps = [];
    if (document.getElementById('pe-step-annotate').checked) steps.push('annotate');
    if (document.getElementById('pe-step-coach').checked) steps.push('coach');
    if (document.getElementById('pe-step-detect').checked) steps.push('detect');

    if (steps.length === 0) {
        toast('请至少选择一个步骤', 'error');
        return null;
    }
    return steps;
}

function peSetRunning(running) {
    document.getElementById('btn-pe-start').disabled = running;
    document.getElementById('btn-pe-cancel').style.display = running ? 'inline-block' : 'none';
    var overlay = document.getElementById('pe-loading-overlay');

    if (running) {
        document.getElementById('pe-status').innerHTML = '<span style="color:var(--accent)">⏳ 执行中，请稍候...</span>';
        overlay.style.display = 'flex';
        // 添加加载动画到步骤导航
        document.querySelectorAll('.pipeline-step').forEach(function(el) {
            if (el.classList.contains('running')) {
                el.querySelector('.step-status').innerHTML = '<span class="loading-dots">执行中</span>';
            }
        });
    } else {
        overlay.style.display = 'none';
        document.getElementById('pe-loading-msg').textContent = '正在分析文本，请稍候';
        // 清除加载动画
        document.querySelectorAll('.loading-dots').forEach(function(el) {
            el.remove();
        });
    }
}

function peUpdateLoadingMsg(msg) {
    document.getElementById('pe-loading-msg').textContent = msg;
}

function peMarkStepsRunning(steps) {
    steps.forEach(function(s) {
        var nav = document.getElementById('pe-step-nav-' + s);
        if (nav) { nav.className = 'pipeline-step running'; nav.querySelector('.step-status').textContent = '执行中...'; }
    });
}

function peMarkStepsError(steps) {
    steps.forEach(function(s) {
        var nav = document.getElementById('pe-step-nav-' + s);
        if (nav && nav.classList.contains('running')) {
            nav.className = 'pipeline-step error';
            nav.querySelector('.step-status').textContent = '失败';
        }
    });
}

function peHandleResult(result, steps) {
    peState.stepsResult = result;
    peState.currentContent = result.current_content || '';

    // 更新步骤状态
    (result.steps || []).forEach(function(step) {
        var nav = document.getElementById('pe-step-nav-' + step.step);
        if (nav) {
            if (step.error) {
                nav.className = 'pipeline-step error';
                nav.querySelector('.step-status').textContent = '失败';
            } else {
                nav.className = 'pipeline-step done';
                nav.querySelector('.step-status').textContent = step.summary || '完成';
            }
        }
    });

    // 显示最后一步的结果
    var lastStep = result.steps[result.steps.length - 1];
    if (lastStep) {
        peShowStepResult(lastStep);
    }

    // 更新原文栏
    document.getElementById('pe-col-original').value = result.original || '';
    document.getElementById('pe-orig-count').textContent = (result.original || '').length + ' 字';

    // 更新合并结果栏
    document.getElementById('pe-col-final').value = peState.currentContent;
    peUpdateFinalCount();
}

// ===== 步骤切换 =====
function peShowStep(stepName) {
    if (!peState.stepsResult) return;
    var steps = peState.stepsResult.steps || [];
    var step = steps.find(function(s) { return s.step === stepName; });
    if (step) peShowStepResult(step);
}

function peShowStepResult(step) {
    peState.currentStep = step.step;

    // 高亮当前步骤
    document.querySelectorAll('.pipeline-step').forEach(function(el) { el.style.boxShadow = ''; });
    var nav = document.getElementById('pe-step-nav-' + step.step);
    if (nav) nav.style.boxShadow = '0 0 0 2px var(--accent)';

    // 更新原文栏
    document.getElementById('pe-col-original').value = step.original || '';
    document.getElementById('pe-orig-count').textContent = (step.original || '').length + ' 字';

    // 更新diff卡片
    peState.diffItems = step.diff_items || [];
    peRenderDiffCards(step);

    // 更新合并结果栏
    var optimized = step.optimized || '';
    document.getElementById('pe-col-final').value = optimized;
    peUpdateFinalCount();

    // 显示步骤详情
    peShowStepDetail(step);
}

function peShowStepDetail(step) {
    var panel = document.getElementById('pe-step-detail');
    var title = document.getElementById('pe-detail-title');
    var content = document.getElementById('pe-detail-content');

    panel.style.display = 'block';
    title.textContent = step.name || step.step;

    var html = '';

    // 步骤摘要
    if (step.summary) {
        html += '<div class="result-summary">' + esc(step.summary) + '</div>';
    }

    // AI检测特殊信息
    if (step.step === 'detect' && step.reduction_applied !== undefined) {
        html += '<div style="margin-bottom:8px">';
        html += '<span style="font-size:12px">AI率: </span>';
        html += '<span style="color:var(--bad);font-weight:600">' + ((step.ai_score_before || 0) * 100).toFixed(0) + '%</span>';
        html += ' → ';
        html += '<span style="color:var(--ok);font-weight:600">' + ((step.ai_score_after || 0) * 100).toFixed(0) + '%</span>';
        if (!step.reduction_applied) html += ' <span style="color:var(--text-muted)">（未超过阈值，无需降重）</span>';
        html += '</div>';
    }

    // 编辑批注详情
    if (step.annotations && step.annotations.length > 0) {
        html += '<div style="margin-top:8px">';
        step.annotations.forEach(function(ann) {
            if (!ann.has_issues) return;
            html += '<div style="padding:6px 8px;background:var(--bg);border-radius:4px;margin-bottom:4px;font-size:11px">';
            html += '<strong>段落 #' + (ann.paragraph_index + 1) + '</strong>';
            (ann.issues || []).forEach(function(issue) {
                var color = {high:'var(--bad)', medium:'var(--warn)', low:'var(--text-muted)'}[issue.severity] || 'var(--text-muted)';
                html += '<div style="margin-top:2px;padding-left:8px;border-left:2px solid ' + color + '">';
                html += '<span style="color:' + color + '">[' + (issue.type || '') + ']</span> ';
                html += esc(issue.description || '');
                if (issue.suggestion) html += '<br><span style="color:var(--text-muted)">建议: ' + esc(issue.suggestion) + '</span>';
                html += '</div>';
            });
            html += '</div>';
        });
        html += '</div>';
    }

    // 写作教练变更
    if (step.changes && step.changes.length > 0) {
        html += '<div style="margin-top:8px">';
        step.changes.forEach(function(ch) {
            html += '<div style="padding:4px 8px;font-size:11px">';
            html += '<span style="color:var(--accent)">段落 #' + ((ch.paragraph_index || 0) + 1) + '</span>: ';
            html += esc(ch.reason || ch.strategy || '');
            html += '</div>';
        });
        html += '</div>';
    }

    content.innerHTML = html || '<p class="muted">无详情</p>';
}

// ===== Diff 卡片渲染 =====
function peRenderDiffCards(step) {
    var container = document.getElementById('pe-col-diffs');
    var items = peState.diffItems;

    if (!items || items.length === 0) {
        container.innerHTML = '<p class="muted" style="text-align:center;padding:40px">无改写建议</p>';
        return;
    }

    var html = '';
    // 步骤摘要
    if (step.summary) {
        html += '<div style="padding:8px;background:rgba(124,92,252,0.05);border-radius:6px;margin-bottom:10px;font-size:11px;color:var(--text-muted)">' + esc(step.summary) + '</div>';
    }

    items.forEach(function(d, i) {
        if (!d.isChanged) {
            html += '<div class="diff-card diff-card-unchanged" style="padding:6px 8px;margin-bottom:4px;font-size:11px;color:var(--text-muted);opacity:0.5">' + esc(d.orig).substring(0, 40) + '...</div>';
        } else {
            var selected = d.selected !== false;
            var borderColor = selected ? 'var(--ok)' : 'var(--bad)';
            var statusIcon = selected ? '✓' : '✗';
            var selectedClass = selected ? 'status-accepted' : 'status-rejected';

            html += '<div class="diff-card ' + selectedClass + '" style="border-color:' + borderColor + ';padding:8px;margin-bottom:6px;border-radius:6px;cursor:pointer" onclick="peToggleCard(' + i + ')">';
            html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">';
            html += '<span style="font-size:10px;color:var(--text-muted)">段落 #' + (d.index + 1) + '</span>';
            html += '<span style="font-size:14px">' + statusIcon + '</span>';
            html += '</div>';
            if (d.reason) html += '<div style="font-size:10px;color:var(--accent);margin-bottom:4px">' + esc(d.reason).substring(0, 60) + '</div>';
            html += '<div style="font-size:11px"><span style="color:var(--bad);text-decoration:line-through">' + esc(d.orig || '').substring(0, 50) + '</span></div>';
            html += '<div style="font-size:11px;margin-top:2px"><span style="color:var(--ok)">' + esc(d.final || '').substring(0, 50) + '</span></div>';
            html += '<div style="display:flex;gap:4px;margin-top:6px">';
            html += '<button class="btn btn-sm" onclick="event.stopPropagation();peEditCard(' + i + ')" style="font-size:10px">✏️ 编辑</button>';
            html += '</div>';
            html += '</div>';
        }
    });

    container.innerHTML = html;
    peUpdateMergeResult();
}

function peToggleCard(idx) {
    var item = peState.diffItems[idx];
    if (!item || !item.isChanged) return;
    item.selected = item.selected === false ? true : false;
    peRenderDiffCards({step: peState.currentStep});
    peUpdateMergeResult();
}

function peBatchAccept() {
    peState.diffItems.forEach(function(d) { if (d.isChanged) d.selected = true; });
    peRenderDiffCards({step: peState.currentStep});
    peUpdateMergeResult();
}

function peBatchReject() {
    peState.diffItems.forEach(function(d) { if (d.isChanged) d.selected = false; });
    peRenderDiffCards({step: peState.currentStep});
    peUpdateMergeResult();
}

function peUpdateMergeResult() {
    var result = '';
    peState.diffItems.forEach(function(d) {
        if (!d.isChanged) result += d.orig + '\n\n';
        else if (d.selected !== false) result += (d.final || d.orig) + '\n\n';
        else result += d.orig + '\n\n';
    });
    document.getElementById('pe-col-final').value = result.trim();
    peUpdateFinalCount();

    var selected = peState.diffItems.filter(function(d) { return d.isChanged && d.selected !== false; }).length;
    var total = peState.diffItems.filter(function(d) { return d.isChanged; }).length;
    document.getElementById('pe-merge-stats').textContent = '已选: ' + selected + '/' + total + ' 段改写';
}

function peUpdateFinalCount() {
    var val = document.getElementById('pe-col-final').value;
    document.getElementById('pe-final-count').textContent = val.length + ' 字';
}

// ===== 编辑卡片 =====
function peEditCard(idx) {
    var item = peState.diffItems[idx];
    if (!item) return;
    peState.editingIndex = idx;
    document.getElementById('pe-edit-seg-num').textContent = idx + 1;
    document.getElementById('pe-edit-orig').textContent = item.orig || '(空)';
    document.getElementById('pe-edit-content').value = item.final || '';
    document.getElementById('pe-edit-char-count').textContent = (item.final || '').length + ' 字';
    document.getElementById('pe-edit-content').oninput = function() {
        document.getElementById('pe-edit-char-count').textContent = this.value.length + ' 字';
    };
    document.getElementById('pe-edit-modal').style.display = 'flex';
    setTimeout(function() {
        var ta = document.getElementById('pe-edit-content');
        ta.focus(); ta.select();
    }, 100);
}

function peCloseEditModal() {
    document.getElementById('pe-edit-modal').style.display = 'none';
    peState.editingIndex = -1;
}

function peSaveEdit() {
    if (peState.editingIndex < 0) return;
    var item = peState.diffItems[peState.editingIndex];
    if (!item) return;
    var newContent = document.getElementById('pe-edit-content').value;
    item.final = newContent;
    item.selected = true;
    item.edited = true;
    peRenderDiffCards({step: peState.currentStep});
    peUpdateMergeResult();
    peCloseEditModal();
    toast('已保存修改', 'success');
}

function peRestoreSystemVersion() {
    if (peState.editingIndex < 0) return;
    var item = peState.diffItems[peState.editingIndex];
    if (!item) return;
    document.getElementById('pe-edit-content').value = item.final || '';
    document.getElementById('pe-edit-char-count').textContent = (item.final || '').length + ' 字';
    toast('已恢复系统版本', 'info');
}

// ===== 保存结果 =====
async function peSaveResult() {
    var pid = peState.projectId;
    var chNum = peState.chapterNum;
    var content = document.getElementById('pe-col-final').value;

    if (!pid || !chNum) {
        toast('请先选择项目和章节', 'error');
        return;
    }
    if (!content) {
        toast('没有可保存的内容', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/v1/projects/' + pid + '/pipeline/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                chapter_num: chNum,
                content: content,
            })
        });

        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || '保存失败');
        }

        var data = await resp.json();
        toast('已保存到章节，' + (data.word_count || 0) + '字', 'success');
    } catch(e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

function peCopyResult() {
    var content = document.getElementById('pe-col-final').value;
    if (!content) {
        toast('没有可复制的内容', 'error');
        return;
    }
    navigator.clipboard.writeText(content).then(function() {
        toast('已复制到剪贴板', 'success');
    }).catch(function() {
        toast('复制失败', 'error');
    });
}

// ESC 关闭弹窗
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        peCloseEditModal();
    }
});
