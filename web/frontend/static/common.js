// ===== common.js — 共享工具与 Diff/Merge 组件 =====

// ===== Toast 通知系统 =====
function toast(msg, type='info', duration=3000) {
    const c = document.getElementById('toast-container');
    if(!c) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.style.opacity='0'; setTimeout(() => el.remove(), 300); }, duration);
}

// ===== 自动保存 =====
let _autoSaveTimer = null;
let _autoSaveEnabled = false;
function enableAutoSave(fn, delay=3000) {
    _autoSaveEnabled = true;
    document.addEventListener('keydown', e => {
        if (e.ctrlKey && e.key === 's') { e.preventDefault(); fn(); toast('已保存', 'success', 1500); }
    });
    return (content) => {
        if (!_autoSaveEnabled) return;
        clearTimeout(_autoSaveTimer);
        _autoSaveTimer = setTimeout(() => {
            fn();
            const el = document.getElementById('auto-save-status');
            if(el) { el.textContent = '已自动保存'; setTimeout(() => { if(el) el.textContent=''; }, 2000); }
        }, delay);
    };
}

// ===== Sidebar =====
function toggleSidebar() { document.getElementById('sidebar').classList.toggle('collapsed'); }

// ===== Health Check =====
fetch('/api/v1/admin/health').then(r=>r.json()).then(d=>{
    if(d.status!=='healthy') document.getElementById('health-dot').innerHTML='🔴 异常';
}).catch(()=>{ document.getElementById('health-dot').innerHTML='🔴 离线'; });

// ===== 通用工具 =====
function esc(s){ return (s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escAttr(s){ return (s||'').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ===== Diff/Merge 三栏共享组件 =====
// 用法: 在页面中声明 diffState 对象，然后调用 diffXxx 函数
// anti_ai.html 和 coach.html 共用此组件

var diffState = {
    items: [],          // [{index, orig, final, isChanged, selected, edited, customText}]
    editingIndex: -1,
    summaryHTML: '',
    onApply: null       // callback(resultText)
};

function diffBuildItems(originalText, optimizedText) {
    var origParas = originalText.split(/\n\n+/);
    var finalParas = optimizedText.split(/\n\n+/);
    var items = [];
    var maxP = Math.max(origParas.length, finalParas.length);
    for (var i = 0; i < maxP; i++) {
        var op = i < origParas.length ? origParas[i].trim() : '';
        var fp = i < finalParas.length ? finalParas[i].trim() : '';
        if (!op && !fp) continue;
        var isChanged = op !== fp;
        items.push({index: i, orig: op, final: fp, isChanged: isChanged, selected: isChanged, edited: false, customText: ''});
    }
    diffState.items = items;
    return items;
}

function diffRenderOriginal(containerId) {
    containerId = containerId || 'col-original';
    var html = '';
    diffState.items.forEach(function(d, i) {
        if (d.isChanged) {
            html += '<div id="orig-' + i + '" class="diff-orig-row diff-orig-changed" onclick="diffSyncToCard(' + i + ')">' + esc(d.orig).substring(0, 60) + '...</div>';
        } else {
            html += '<div id="orig-' + i + '" class="diff-orig-row diff-orig-unchanged">' + esc(d.orig).substring(0, 60) + '...</div>';
        }
    });
    document.getElementById(containerId).innerHTML = html || '<p class="muted">无内容</p>';
}

function diffRenderCards(summaryHTML, containerId) {
    containerId = containerId || 'col-diffs';
    diffState.summaryHTML = summaryHTML || '';
    var html = summaryHTML || '';
    diffState.items.forEach(function(d, i) {
        if (!d.isChanged) {
            html += '<div class="diff-card diff-card-unchanged" id="card-' + i + '"><div class="card-content" style="font-size:11px;color:var(--text-muted)">' + esc(d.orig).substring(0, 40) + '...</div></div>';
        } else {
            var selectedClass = d.selected ? 'status-accepted' : 'status-rejected';
            var borderColor = d.selected ? 'var(--ok)' : 'var(--bad)';
            var statusIcon = d.selected ? '✓' : '✗';
            var cardContent = '<div class="card-row orig-row"><span class="card-tag del">原文</span>' + esc(d.orig || '').substring(0, 50) + '</div>';
            if (d.edited && d.customText) {
                cardContent += '<div class="card-row card-row-edited"><span class="card-tag" style="color:#ffc107">自定义</span>' + esc(d.customText).substring(0, 50) + '</div>';
            } else {
                cardContent += '<div class="card-row opt-row"><span class="card-tag add">改写</span>' + esc(d.final || '').substring(0, 50) + '</div>';
            }
            html += '<div class="diff-card ' + selectedClass + '" id="card-' + i + '" style="border-color:' + borderColor + '" onclick="diffToggleCard(' + i + ')">' +
                '<div class="card-status-icon">' + statusIcon + '</div>' +
                '<div class="card-content">' + cardContent + '</div>' +
                '<button class="btn btn-sm card-edit-btn" onclick="event.stopPropagation();diffEditCard(' + i + ')">' + (d.edited ? '修改' : '编辑') + '</button>' +
                '</div>';
        }
    });
    document.getElementById(containerId).innerHTML = html;
}

function diffToggleCard(idx) {
    var item = diffState.items.find(function(d) { return d.index === idx; });
    if (!item || !item.isChanged) return;
    item.selected = !item.selected;
    diffRenderCards(diffState.summaryHTML);
    diffUpdateMergeResult();
    diffSyncToCard(idx);
}

function diffSyncToCard(idx) {
    document.querySelectorAll('[id^="orig-"]').forEach(function(el) { el.style.background = ''; });
    var origEl = document.getElementById('orig-' + idx);
    if (origEl) {
        origEl.style.background = 'rgba(76,175,80,0.15)';
        origEl.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
    var cardEl = document.getElementById('card-' + idx);
    if (cardEl) {
        cardEl.scrollIntoView({behavior: 'smooth', block: 'center'});
        cardEl.classList.add('highlight-flash');
        setTimeout(function() { cardEl.classList.remove('highlight-flash'); }, 1500);
    }
    diffSyncToMergeResult(idx);
}

function diffSyncToMergeResult(idx) {
    var mergeResult = document.getElementById('col-final');
    if (!mergeResult) return;
    var paragraphs = [];
    diffState.items.forEach(function(d) {
        if (!d.isChanged) paragraphs.push(d.orig);
        else if (d.selected) paragraphs.push(d.edited ? d.customText : d.final);
        else paragraphs.push(d.orig);
    });
    var charPos = 0;
    for (var i = 0; i < idx && i < paragraphs.length; i++) charPos += paragraphs[i].length + 2;
    mergeResult.focus();
    var endPos = Math.min(charPos + (paragraphs[idx] ? paragraphs[idx].length : 100), mergeResult.value.length);
    mergeResult.setSelectionRange(charPos, endPos);
    var lineHeight = 20;
    var linesBefore = mergeResult.value.substring(0, charPos).split('\n').length;
    mergeResult.scrollTop = (linesBefore - 5) * lineHeight;
    mergeResult.style.boxShadow = '0 0 0 2px var(--accent)';
    setTimeout(function() { mergeResult.style.boxShadow = 'none'; }, 1500);
}

function diffEditCard(idx) {
    var item = diffState.items.find(function(d) { return d.index === idx; });
    if (!item) return;
    diffState.editingIndex = idx;
    document.getElementById('edit-segment-num').textContent = idx + 1;
    document.getElementById('edit-orig-text').textContent = item.orig || '(空)';
    document.getElementById('edit-content').value = item.edited ? item.customText : item.final;
    document.getElementById('edit-char-count').textContent = (item.edited ? item.customText : item.final).length + ' 字';
    document.getElementById('edit-content').oninput = function() {
        document.getElementById('edit-char-count').textContent = this.value.length + ' 字';
    };
    document.getElementById('edit-modal').style.display = 'flex';
    setTimeout(function() {
        var ta = document.getElementById('edit-content');
        ta.focus(); ta.select();
    }, 100);
}

function diffCloseEditModal() {
    document.getElementById('edit-modal').style.display = 'none';
    diffState.editingIndex = -1;
}

function diffSaveEdit() {
    if (diffState.editingIndex < 0) return;
    var item = diffState.items.find(function(d) { return d.index === diffState.editingIndex; });
    if (!item) return;
    var newContent = document.getElementById('edit-content').value;
    if (newContent !== item.final || newContent !== item.customText) {
        item.customText = newContent;
        item.edited = true;
        item.selected = true;
        diffRenderCards(diffState.summaryHTML);
        diffUpdateMergeResult();
        diffSyncToCard(diffState.editingIndex);
    }
    diffCloseEditModal();
    toast('已保存修改', 'success');
}

function diffRestoreSystemVersion() {
    if (diffState.editingIndex < 0) return;
    var item = diffState.items.find(function(d) { return d.index === diffState.editingIndex; });
    if (!item) return;
    document.getElementById('edit-content').value = item.final;
    document.getElementById('edit-char-count').textContent = item.final.length + ' 字';
    toast('已恢复系统改写版本', 'info');
}

function diffUpdateMergeResult(containerId) {
    containerId = containerId || 'col-final';
    var result = '';
    diffState.items.forEach(function(d) {
        if (!d.isChanged) result += d.orig + '\n\n';
        else if (d.selected) result += (d.edited ? d.customText : d.final) + '\n\n';
        else result += d.orig + '\n\n';
    });
    document.getElementById(containerId).value = result.trim();
    var selectedCount = diffState.items.filter(function(d) { return d.isChanged && d.selected; }).length;
    var totalCount = diffState.items.filter(function(d) { return d.isChanged; }).length;
    var statsEl = document.getElementById('merge-stats');
    if (statsEl) statsEl.textContent = '已选: ' + selectedCount + '/' + totalCount + ' 段改写';
}

function diffBatchAccept() {
    diffState.items.forEach(function(d) { if (d.isChanged) d.selected = true; });
    diffRenderCards(diffState.summaryHTML);
    diffUpdateMergeResult();
}

function diffBatchReject() {
    diffState.items.forEach(function(d) { if (d.isChanged) d.selected = false; });
    diffRenderCards(diffState.summaryHTML);
    diffUpdateMergeResult();
}

// ESC 关闭编辑弹窗
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') diffCloseEditModal();
});

// ===== 自定义弹窗（支持多层叠加） =====
var _modalCounter = 0;
function _closeModal(id) {
    if (id) { var m = document.getElementById(id); if (m) m.remove(); return; }
    // 关闭最顶层弹窗
    var modals = document.querySelectorAll('.modal-overlay');
    if (modals.length > 0) modals[modals.length - 1].remove();
}
function _modal(opts) {
    var mid = '_modal_' + (++_modalCounter);
    var depth = document.querySelectorAll('.modal-overlay').length;
    var overlay = document.createElement('div'); overlay.id = mid; overlay.className = 'modal-overlay';
    overlay.style.zIndex = 10000 + depth;
    overlay.onclick = function(e) { if (e.target === overlay) { if (opts.onCancel) opts.onCancel(); _closeModal(mid); } };
    var box = document.createElement('div'); box.className = 'modal-box';
    var icon = opts.icon || '💬';
    var okId = mid + '-ok', cancelId = mid + '-cancel';
    box.innerHTML = '<div class="modal-header">' + icon + ' ' + (opts.title || '提示') + '</div>' +
        '<div class="modal-body">' + (opts.body || '') + '</div>' +
        '<div class="modal-footer">' +
        (opts.cancelText !== false ? '<button class="btn btn-ghost" id="' + cancelId + '">' + (opts.cancelText || '取消') + '</button>' : '') +
        (opts.danger ? '<button class="btn btn-danger" id="' + okId + '">' + (opts.okText || '确定') + '</button>' : '<button class="btn btn-primary" id="' + okId + '">' + (opts.okText || '确定') + '</button>') +
        '</div>';
    overlay.appendChild(box); document.body.appendChild(overlay);
    var okBtn = document.getElementById(okId);
    var cancelBtn = document.getElementById(cancelId);
    if (okBtn) okBtn.onclick = function() { if (opts.onOk) opts.onOk(); _closeModal(mid); };
    if (cancelBtn) cancelBtn.onclick = function() { if (opts.onCancel) opts.onCancel(); _closeModal(mid); };
    overlay._escHandler = function(e) { if (e.key === 'Escape') { if (opts.onCancel) opts.onCancel(); _closeModal(mid); document.removeEventListener('keydown', overlay._escHandler); } };
    document.addEventListener('keydown', overlay._escHandler);
    setTimeout(function() { var inp = box.querySelector('input,textarea'); if (inp) { inp.focus(); inp.select(); } }, 100);
    return overlay;
}
function modalConfirm(title, msg, onOk, opts) {
    _modal({icon: '⚠️', title: title, body: '<p>' + msg + '</p>', okText: (opts && opts.okText) || '确定', danger: opts && opts.danger, onOk: onOk, onCancel: (opts && opts.onCancel) || null});
}
function modalPrompt(title, label, defaultVal, onOk) {
    _modal({icon: '✏️', title: title, body: '<label style="font-size:12px;color:var(--text-muted)">' + label + '</label><input id="_modal-input" value="' + (defaultVal || '').replace(/"/g, '&quot;') + '">',
        onOk: function() { onOk(document.getElementById('_modal-input').value); }});
}
