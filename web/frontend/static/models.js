// ===== models.js — 模型管理 =====

var _providers = {};

(async function init() { await loadModels(); })();

function _renderModelList() {
    var el = document.getElementById('model-list');
    var html = '';
    Object.keys(_providers).forEach(function(name) {
        var p = _providers[name];
        var statusIcon = p.healthy ? '🟢' : (p.healthy === false ? '🔴' : '⚪');
        html += '<div class="model-card"><div class="model-info"><strong>' + statusIcon + ' ' + name + '</strong><span class="muted">' + (p.type || '') + ' | ' + (p.base_url || '') + '</span></div><div class="model-actions">';
        (p.models || []).forEach(function(m) { html += '<span class="badge">' + m + '</span> '; });
        html += '<button class="btn btn-sm" onclick="testModel(\'' + name + '\')">🔗 测试</button>';
        html += '<button class="btn btn-sm" onclick="editModel(\'' + name + '\')">✏️ 编辑</button>';
        html += '<button class="btn btn-sm" style="color:var(--bad)" onclick="deleteModel(\'' + name + '\')">🗑</button>';
        html += '</div></div>';
    });
    el.innerHTML = html || '<p class="muted">暂无模型</p>';
}

function _refreshTierDropdowns() {
    ['premium', 'standard', 'budget'].forEach(function(tier) {
        var providerSel = document.getElementById('tier-' + tier + '-provider');
        if (!providerSel) return;
        var currentProvider = providerSel.value;
        providerSel.innerHTML = Object.keys(_providers).map(function(n) {
            return '<option value="' + n + '"' + (n === currentProvider ? ' selected' : '') + '>' + n + '</option>';
        }).join('');
        _populateModelDropdown(tier, document.getElementById('tier-' + tier + '-model').value);
    });
}

async function loadModels() {
    try {
        var resp = await fetch('/api/v1/models');
        var data = await resp.json();

        // API 返回 providers 为数组，转换为对象方便查找
        var providersList = data.providers || [];
        _providers = {};
        if (Array.isArray(providersList)) {
            providersList.forEach(function(p) { _providers[p.name] = p; });
        } else {
            _providers = providersList;
        }

        _renderModelList();

        // Tier 配置
        var tiers = data.tiers || {};
        ['premium', 'standard', 'budget'].forEach(function(tier) {
            var t = tiers[tier] || {};
            var providerSel = document.getElementById('tier-' + tier + '-provider');
            if (!providerSel) return;
            providerSel.innerHTML = Object.keys(_providers).map(function(n) {
                return '<option value="' + n + '"' + (n === t.provider ? ' selected' : '') + '>' + n + '</option>';
            }).join('');
            var selectedProvider = t.provider || Object.keys(_providers)[0] || '';
            if (selectedProvider && providerSel.value !== selectedProvider) providerSel.value = selectedProvider;
            _populateModelDropdown(tier, t.model);
        });
    } catch(e) { document.getElementById('model-list').innerHTML = '<p class="error">加载失败: ' + e.message + '</p>'; }
}

function _populateModelDropdown(tier, selectedModel) {
    var provider = document.getElementById('tier-' + tier + '-provider').value;
    var p = _providers[provider] || {};
    var models = p.models || [];
    // 如果 models 为空，使用 default_model 作为 fallback
    if (models.length === 0 && p.default_model) {
        models = [p.default_model];
    }
    var modelSel = document.getElementById('tier-' + tier + '-model');
    modelSel.innerHTML = models.map(function(m) {
        return '<option value="' + m + '"' + (m === selectedModel ? ' selected' : '') + '>' + m + '</option>';
    }).join('');
}

function onTierProviderChange(tier) {
    _populateModelDropdown(tier, '');
}

async function saveTier(tier) {
    var provider = document.getElementById('tier-' + tier + '-provider').value;
    var model = document.getElementById('tier-' + tier + '-model').value;
    if (!provider || !model) { toast('请选择 Provider 和 Model', 'error'); return; }
    try {
        var resp = await fetch('/api/v1/models/switch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({tier: tier, provider: provider, model: model})});
        if (!resp.ok) {
            var errText = await resp.text();
            toast('切换失败 (' + resp.status + '): ' + errText, 'error');
            return;
        }
        var d = await resp.json();
        if (d.status === 'ok' || d.status === 'switched') {
            toast(tier + ' 已切换到 ' + provider + '/' + model, 'success');
        } else {
            toast('切换失败: ' + (d.error || d.message || JSON.stringify(d)), 'error');
        }
    } catch(e) { toast('切换失败: ' + e.message, 'error'); }
}

async function deleteModel(name) {
    if (!confirm('确定删除模型「' + name + '」？')) return;
    try {
        var resp = await fetch('/api/v1/models/providers/' + encodeURIComponent(name), {method: 'DELETE'});
        if (!resp.ok) {
            var err = await resp.json().catch(function() { return {detail: '未知错误'}; });
            toast('删除失败: ' + (err.detail || resp.status), 'error');
            return;
        }
        // 立即更新本地数据
        delete _providers[name];
        _renderModelList();
        _refreshTierDropdowns();
        toast('已删除', 'success');
    } catch(e) { toast('删除失败: ' + e.message, 'error'); }
}

async function testModel(name) {
    try {
        var resp = await fetch('/api/v1/models/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({provider: name})});
        var d = await resp.json();
        // 更新本地状态并刷新显示
        if (_providers[name]) _providers[name].healthy = d.healthy;
        _renderModelList();
        if (d.healthy) toast(name + ': 连接成功 ✅', 'success');
        else toast(name + ': ' + (d.error || '连接失败'), 'error');
    } catch(e) { toast('测试失败', 'error'); }
}

async function addModel() {
    var name = document.getElementById('new-name').value.trim();
    var baseUrl = document.getElementById('new-url').value.trim();
    var apiKey = document.getElementById('new-key').value.trim();
    var model = document.getElementById('new-model').value.trim();
    if (!name || !baseUrl) { alert('请填写名称和 URL'); return; }
    if (!model) { alert('请填写默认模型'); return; }
    try {
        var resp = await fetch('/api/v1/models/providers', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, type: 'openai_compatible', base_url: baseUrl, api_key: apiKey, default_model: model, models: [model]})});
        if (!resp.ok) {
            var err = await resp.json().catch(function() { return {detail: '未知错误'}; });
            toast('添加失败: ' + (err.detail || resp.status), 'error');
            return;
        }
        // 立即更新本地数据，不等 loadModels
        _providers[name] = {name: name, type: 'openai_compatible', base_url: baseUrl, default_model: model, models: [model], healthy: null};
        _renderModelList();
        _refreshTierDropdowns();
        toast('已添加', 'success');
        // 清空表单
        document.getElementById('new-name').value = '';
        document.getElementById('new-url').value = '';
        document.getElementById('new-key').value = '';
        document.getElementById('new-model').value = '';
    } catch(e) { toast('添加失败: ' + e.message, 'error'); }
}

var _editModelName = null;
function editModel(name) {
    _editModelName = name;
    var p = _providers[name] || {};
    document.getElementById('edit-original-name').value = name;
    document.getElementById('edit-name').value = name;
    document.getElementById('edit-url').value = p.base_url || '';
    document.getElementById('edit-key').value = '';
    document.getElementById('edit-model').value = p.default_model || (p.models || [])[0] || '';
    document.getElementById('edit-modal').style.display = 'block';
}
function closeEditModal() { document.getElementById('edit-modal').style.display = 'none'; _editModelName = null; }
async function saveEdit() {
    if (!_editModelName) return;
    var newName = document.getElementById('edit-name').value.trim();
    var baseUrl = document.getElementById('edit-url').value.trim();
    var apiKey = document.getElementById('edit-key').value.trim();
    var model = document.getElementById('edit-model').value.trim();
    if (!newName) { alert('名称不能为空'); return; }

    // 名称改变了 → 删除旧的，创建新的
    if (newName !== _editModelName) {
        try {
            await fetch('/api/v1/models/providers/' + encodeURIComponent(_editModelName), {method: 'DELETE'});
            var resp = await fetch('/api/v1/models/providers', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: newName, type: 'openai_compatible', base_url: baseUrl, api_key: apiKey, default_model: model, models: model ? [model] : []})});
            if (!resp.ok) {
                var err = await resp.json().catch(function() { return {detail: '未知错误'}; });
                toast('重命名失败: ' + (err.detail || resp.status), 'error');
                return;
            }
            delete _providers[_editModelName];
            _providers[newName] = {name: newName, type: 'openai_compatible', base_url: baseUrl, default_model: model, models: model ? [model] : [], healthy: null};
            _renderModelList(); _refreshTierDropdowns();
            closeEditModal(); toast('已重命名并更新', 'success');
        } catch(e) { toast('重命名失败: ' + e.message, 'error'); }
        return;
    }

    // 名称没变 → 普通更新
    var body = {};
    if (baseUrl) body.base_url = baseUrl;
    if (apiKey) body.api_key = apiKey;
    if (model) body.default_model = model;
    try {
        var resp = await fetch('/api/v1/models/providers/' + encodeURIComponent(_editModelName), {method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)});
        if (!resp.ok) {
            var err = await resp.json().catch(function() { return {detail: '未知错误'}; });
            toast('更新失败: ' + (err.detail || resp.status), 'error');
            return;
        }
        var result = await resp.json();
        if (_providers[_editModelName]) {
            if (baseUrl) _providers[_editModelName].base_url = baseUrl;
            if (result.models) { _providers[_editModelName].models = result.models; }
            else if (model) { _providers[_editModelName].models = [model]; }
            if (result.default_model) { _providers[_editModelName].default_model = result.default_model; }
            else if (model) { _providers[_editModelName].default_model = model; }
        }
        _renderModelList(); _refreshTierDropdowns(); closeEditModal(); toast('已更新', 'success');
    } catch(e) { toast('更新失败: ' + e.message, 'error'); }
}
