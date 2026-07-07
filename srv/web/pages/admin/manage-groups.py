from srv.web.app import *
from group_features import ensure_group_feature_schema, fetch_group_rows, serialize_monitor_categories
from srv.web.pages.messages.form_common import _audio_file_picker_item_html, _audio_picker_options


GROUPS_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; margin:0; }
.header-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap;}
.header-actions h1{margin:0;}
.page-subtitle{margin:6px 0 0;color:#666;line-height:1.45;}
.card{ background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.08); padding:16px; }
.stack{display:flex;flex-direction:column;gap:16px;}
.section-title{margin:0 0 10px;font-size:1em;font-weight:500;color:#202124;}
.section-help{margin:0;color:#666;line-height:1.45;font-size:0.94em;}
.main-label{display:block;margin-bottom:8px;font-weight:500;font-size:1.1em;color:#202124;}
.field{ display:flex; flex-direction:column; gap:6px; }
.field label { color:#555; font-size:0.9em; font-weight:500; }
.field input { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; }
.field-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:8px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-primary:hover { background:#1565C0; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; border-radius:8px; }
.btn-secondary:hover{background:rgba(25,118,210,0.08);}
.group-list { list-style:none; margin:0; padding:0; }
.group-item { display:flex; justify-content:space-between; gap:12px; padding:14px 0; border-bottom:1px solid #EEE; align-items:flex-start; }
.group-item:last-child { border-bottom:none; }
.group-main { flex:1; min-width:0; }
.group-name { font-weight:500; color:#202124; font-size:1.02em; }
.group-members { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.group-actions { display:flex; align-items:center; gap:4px; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.editor-grid{display:grid;grid-template-columns:1fr;gap:16px;}
.editor-card{padding:16px;}
.form-section{margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid #F0F0F0;}
.form-section:last-of-type{margin-bottom:0;padding-bottom:0;border-bottom:none;}
.transfer-wrap{display:grid;grid-template-columns:1fr;gap:12px;}
.transfer-list-container { display:flex; gap:12px; align-items:stretch; margin-top:8px; }
.tl-panel { flex:1; min-width:0; display:flex; flex-direction:column; }
.tl-header { font-weight:500; margin-bottom:8px; color:#555; }
.tl-search { width:100%; box-sizing:border-box; border:1px solid #CCC; border-radius:4px; padding:10px; margin-bottom:8px; font:inherit; height:42px; }
.tl-search-spacer { height:42px; margin-bottom:8px; flex:0 0 auto; }
.tl-list { min-height:320px; height:320px; max-height:320px; overflow:auto; border:1px solid #EEE; border-radius:4px; padding:8px; background:#FFF; flex:1 1 auto; }
.tl-item { padding:8px 10px; margin-bottom:4px; background:#FAFAFA; border:1px solid #EEE; cursor:pointer; user-select:none; border-radius:3px; font-size:0.95em; }
.tl-item.selected { background:#1976D2; color:#FFF; border-color:#1565C0; }
.tl-item.dragging { opacity:0.5; }
.tl-controls { display:flex; flex-direction:column; justify-content:center; gap:8px; }
.tl-controls .btn-primary{justify-content:center;padding:10px;width:42px;height:42px;}
.field-group{display:grid;grid-template-columns:1fr;gap:10px;}
.checkbox-list{display:flex;flex-direction:column;gap:8px;}
.md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:500;color:#555;user-select:none;width:100%;padding:5px 0;gap:12px;}
.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0;}
.md-checkmark{position:relative;display:inline-block;flex:0 0 auto;height:20px;width:20px;background-color:#fff;border:2px solid #5f6368;border-radius:2px;transition:all 0.2s;}
.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124;}
.md-checkbox-container input:checked ~ .md-checkmark{background-color:#1976D2;border-color:#1976D2;}
.md-checkmark:after{content:"";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid white;border-width:0 2px 2px 0;transform:rotate(45deg);}
.md-checkbox-container input:checked ~ .md-checkmark:after{display:block;}
.checkbox-text{display:flex;flex-direction:column;gap:2px;min-width:0;}
.checkbox-note{font-size:0.88em;font-weight:400;color:#6b7280;}
.tone-card{border:1px solid #DDD;border-radius:6px;background:#FFF;padding:12px;}
.tone-title{font-weight:500;color:#202124;}
.tone-summary{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;}
.tone-name{font-size:0.94em;color:#555;min-width:0;overflow-wrap:anywhere;margin-top:4px;}
.tone-empty{color:#888;}
.tone-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.tone-clear{border:1px solid #DADCE0;background:#FFF;color:#374151;border-radius:8px;padding:9px 12px;cursor:pointer;font:inherit;}
.tone-clear:hover{background:#F9FAFB;}
.message-icon-picker-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1450;}
.message-icon-picker-backdrop.open{display:block;}
.message-icon-picker-modal{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(980px,calc(100vw - 32px));max-height:calc(100vh - 40px);overflow:hidden;background:#FFF;border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,0.28);z-index:1500;}
.message-icon-picker-modal.open{display:flex;flex-direction:column;}
.message-icon-picker-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid #EEE;}
.message-icon-picker-header h2{margin:0;font-size:1.2em;font-weight:500;}
.message-icon-picker-close{border:none;background:transparent;color:#666;font-size:1.4em;cursor:pointer;line-height:1;padding:4px 6px;}
.message-icon-picker-close:hover{background:transparent;color:#111;}
.message-icon-picker-body{padding:18px 20px 20px;overflow-y:auto;}
.message-icon-picker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;}
.message-icon-asset-card{border:1px solid #DADCE0;border-radius:16px;background:#FFF;padding:0;cursor:pointer;text-align:left;color:inherit;overflow:hidden;box-shadow:0 1px 2px rgba(60,64,67,.08);display:block;width:100%;appearance:none;-webkit-appearance:none;}
.message-icon-asset-card:hover{background:#FFF;border-color:#B6C8E1;box-shadow:0 4px 10px rgba(60,64,67,.12);}
.message-icon-asset-card.selected{border-color:#1976D2;box-shadow:0 0 0 2px rgba(25,118,210,0.18);}
.message-icon-asset-card.unsupported{opacity:0.48;}
.message-icon-asset-card.unsupported:hover{background:#FFF;border-color:#DADCE0;box-shadow:0 1px 2px rgba(60,64,67,.08);}
.message-icon-asset-preview{height:132px;background:#F1F3F4;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#5F6368;font-size:2em;}
.message-icon-asset-preview img{width:100%;height:100%;object-fit:cover;}
.message-icon-asset-info{padding:12px 14px 14px;}
.message-icon-asset-name{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.message-icon-asset-meta{font-size:0.84em;color:#666;margin-top:4px;}
.message-icon-asset-status{font-size:0.83em;margin-top:8px;color:#1976D2;}
.message-icon-asset-card.unsupported .message-icon-asset-status{color:#A50E0E;}
.message-icon-picker-empty{padding:28px;border:1px dashed #DADCE0;border-radius:16px;text-align:center;color:#5F6368;background:#F8FAFD;}
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:10px; margin-bottom:16px; }
.muted { color:#777; font-size:0.92em; }
@media(max-width:900px){ .transfer-list-container{ flex-direction:column; } .tl-controls{ flex-direction:row; justify-content:flex-start; } }
@media(max-width:767px){ .tone-summary{grid-template-columns:1fr;} }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(max-width:767px){
    .message-icon-picker-modal{width:calc(100vw - 20px);max-height:calc(100vh - 20px);}
    .message-icon-picker-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card{ border:1px solid #333; background-color:#1E1E1E; box-shadow:none; }
.page-subtitle,.section-help,.muted,.checkbox-note,.tone-name,.message-icon-asset-meta{ color:#BBB; }
.field label,.main-label,.tl-header,.group-name,.section-title,.tone-title{ color:#EDEDED; }
.field input,.tl-search{ background:#121212; border-color:#444; color:#E0E0E0; }
.tl-list{ background:#121212; border-color:#333; }
.tl-item{ background:#242424; border-color:#333; }
.tl-item.selected{ background:#3B2A4D; color:#F6ECFF; border-color:#6F4F92; }
.btn-primary{ background:#BB86FC; color:#121212; }
.btn-primary:hover{ background:#A874E8; }
.btn-secondary{ color:#BB86FC; }
.btn-secondary:hover{ background:rgba(187,134,252,0.12); }
.group-item{ border-bottom:1px solid #333; }
.form-section{ border-bottom-color:#333; }
.group-members,.tone-card{ color:#BBB; }
.tone-card{ background:#252525; border-color:#444; }
.icon-action{ color:#BBB; }
.icon-action:hover{ background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover{ background:rgba(244,67,54,0.12); color:#EF9A9A; }
.md-checkbox-container{ color:#E0E0E0; }
.md-checkmark{ border-color:#9AA0A6; background:#1E1E1E; }
.md-checkbox-container:hover input ~ .md-checkmark{ border-color:#E8EAED; }
.md-checkbox-container input:checked ~ .md-checkmark{ background:#BB86FC; border-color:#BB86FC; }
.md-checkmark:after{ border-color:#1E1E1E; }
.tone-clear{ background:#252525; border-color:#444; color:#E5E7EB; }
.tone-clear:hover{ background:#303030; }
.message-icon-picker-modal{ background:#1E1E1E; }
.message-icon-picker-header{ border-bottom-color:#333; }
.message-icon-picker-close{ color:#AAA; }
.message-icon-picker-close:hover{ background:transparent; color:#FFF; }
.message-icon-asset-card,.message-icon-asset-preview{ background:#202124; border-color:#333; }
.message-icon-asset-card:hover{background:#202124;border-color:#4A4A4A;box-shadow:none;}
.message-icon-asset-card.selected{border-color:#BB86FC;box-shadow:0 0 0 2px rgba(187,134,252,0.22);}
.message-icon-asset-card.unsupported:hover{background:#202124;border-color:#333;box-shadow:none;}
.message-icon-asset-status{ color:#BB86FC; }
.message-icon-picker-empty{ background:#202020; border-color:#333; color:#BBB; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
}
"""


GROUPS_SCRIPT = r"""
let draggedItem = null;
let tonePickerField = '';

function selectItem(el) {
  const siblings = el.parentElement.querySelectorAll('.tl-item');
  siblings.forEach(s => s.classList.remove('selected'));
  el.classList.add('selected');
}
function searchBox(prefix) {
  return document.getElementById(prefix + 'Search');
}
function availableList(prefix) {
  return document.getElementById(prefix + 'AvailableList');
}
function selectedList(prefix) {
  return document.getElementById(prefix + 'SelectedList');
}
function filterTransfer(prefix) {
  const search = (searchBox(prefix).value || '').toLowerCase();
  availableList(prefix).querySelectorAll('.tl-item').forEach(item => {
    const visible = item.innerText.toLowerCase().includes(search);
    item.style.display = visible ? '' : 'none';
    if (!visible) item.classList.remove('selected');
  });
}
function appendHiddenInput(item, prefix) {
  if (!item.querySelector('input[type="hidden"]')) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = prefix === 'monitor' ? 'monitor_members[]' : 'members[]';
    input.value = item.getAttribute('data-value');
    item.appendChild(input);
  }
}
function removeHiddenInput(item) {
  const input = item.querySelector('input[type="hidden"]');
  if (input) input.remove();
}
function moveRight(prefix) {
  const selected = availableList(prefix).querySelector('.tl-item.selected');
  if (!selected) return;
  selected.classList.remove('selected');
  appendHiddenInput(selected, prefix);
  selectedList(prefix).appendChild(selected);
}
function moveLeft(prefix) {
  const selected = selectedList(prefix).querySelector('.tl-item.selected');
  if (!selected) return;
  selected.classList.remove('selected');
  removeHiddenInput(selected);
  availableList(prefix).appendChild(selected);
  filterTransfer(prefix);
}
function moveUp(prefix) {
  const selected = selectedList(prefix).querySelector('.tl-item.selected');
  if (selected && selected.previousElementSibling) selected.parentNode.insertBefore(selected, selected.previousElementSibling);
}
function moveDown(prefix) {
  const selected = selectedList(prefix).querySelector('.tl-item.selected');
  if (selected && selected.nextElementSibling) selected.parentNode.insertBefore(selected.nextElementSibling, selected);
}
function dragStart(e) {
  draggedItem = e.target.closest('.tl-item');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedItem.getAttribute('data-value') || '');
  setTimeout(() => draggedItem.classList.add('dragging'), 0);
}
document.addEventListener('dragend', function(e) {
  const item = e.target && e.target.closest ? e.target.closest('.tl-item') : null;
  if (item) item.classList.remove('dragging');
  draggedItem = null;
});
function allowDrop(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}
function getDragAfterElement(container, y) {
  const draggableElements = [...container.querySelectorAll('.tl-item:not(.dragging)')];
  return draggableElements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) return { offset: offset, element: child };
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}
function dropToAvailable(prefix, e) {
  e.preventDefault();
  if (!draggedItem || draggedItem.parentElement !== selectedList(prefix)) return;
  removeHiddenInput(draggedItem);
  draggedItem.classList.remove('selected');
  availableList(prefix).appendChild(draggedItem);
  filterTransfer(prefix);
}
function dropToSelected(prefix, e) {
  e.preventDefault();
  if (!draggedItem) return;
  if (draggedItem.parentElement === availableList(prefix)) {
    appendHiddenInput(draggedItem, prefix);
    draggedItem.classList.remove('selected');
  }
  const list = selectedList(prefix);
  const afterElement = getDragAfterElement(list, e.clientY);
  if (afterElement == null) list.appendChild(draggedItem);
  else list.insertBefore(draggedItem, afterElement);
}
function syncSelectedInputs() {
  ['members', 'monitor'].forEach(prefix => {
    selectedList(prefix).querySelectorAll('.tl-item').forEach(item => appendHiddenInput(item, prefix));
  });
}
function toneHidden(field) {
  return document.getElementById(field + 'ToneInput');
}
function toneName(field) {
  return document.getElementById(field + 'ToneName');
}
function syncToneCard(field) {
  const input = toneHidden(field);
  const value = input ? input.value : '';
  const target = toneName(field);
  if (!target) return;
  if (value) {
    target.textContent = value;
    target.classList.remove('tone-empty');
  } else {
    target.textContent = 'No tone selected';
    target.classList.add('tone-empty');
  }
}
function openTonePicker(field) {
  tonePickerField = field;
  document.querySelectorAll('#tonePickerModal .message-icon-asset-card').forEach(function(card) {
    card.classList.remove('selected');
  });
  var current = toneHidden(field);
  var currentValue = current ? current.value : '';
  if (currentValue) {
    var button = document.querySelector('#tonePickerModal .message-icon-asset-card[data-name="' + CSS.escape(currentValue) + '"]');
    if (button) button.classList.add('selected');
  }
  document.getElementById('tonePickerBackdrop').classList.add('open');
  document.getElementById('tonePickerModal').classList.add('open');
}
function closeTonePicker() {
  document.getElementById('tonePickerBackdrop').classList.remove('open');
  document.getElementById('tonePickerModal').classList.remove('open');
  tonePickerField = '';
}
function addAudioFileBlockFromPicker(button) {
  if (!tonePickerField || !button || button.getAttribute('data-supported') !== '1') return;
  const input = toneHidden(tonePickerField);
  if (!input) return;
  input.value = button.getAttribute('data-name') || '';
  document.querySelectorAll('#tonePickerModal .message-icon-asset-card').forEach(function(card) {
    card.classList.toggle('selected', card === button);
  });
  syncToneCard(tonePickerField);
  closeTonePicker();
}
function clearTone(field) {
  const input = toneHidden(field);
  if (!input) return;
  input.value = '';
  syncToneCard(field);
}
document.addEventListener('DOMContentLoaded', function() {
  syncToneCard('pre');
  syncToneCard('post');
});
"""


def output_capable(endpoint):
    if endpoint.get("output_capable") is False:
        return False
    value = (str(endpoint.get("direction") or "") + " " + str(endpoint.get("input_type") or "")).lower()
    if "output" in value:
        return True
    capabilities = endpoint.get("capabilities") or []
    return isinstance(capabilities, list) and ("output" in capabilities or "bells" in capabilities)


def endpoint_label(endpoint, module_display):
    name = str(endpoint.get("name") or endpoint.get("id") or "").strip()
    model = str(endpoint.get("model") or "").strip()
    endpoint_type = str(endpoint.get("type") or "").strip()
    address = str(endpoint.get("address") or "").strip()
    parts = [part for part in (model, endpoint_type, address) if part]
    return name + ((" - " + " - ".join(parts)) if parts else "") + f" ({module_display})"


def next_group_id():
    used = set()
    for row in query_all("SELECT id FROM `groups`"):
        token = str(row.get("id") or "")
        if token.isdigit():
            used.add(int(token))
    candidate = 1
    while candidate in used:
        candidate += 1
    return str(candidate)


def group_member_count(members):
    return len([part for part in re.split(r"[\s,]+", str(members or "")) if part])


def endpoint_choices(user=None, group_rows=None):
    data = endpoint_ipc("LIST_ENDPOINTS")
    warning = data.get("warning") or ""
    error = None if data.get("ok", True) else data.get("error") or "Endpoint manager returned an error."
    module_errors = []
    choices = []
    allowed_tokens = restricted_group_endpoint_tokens(user, group_rows or [])
    for module_info in data.get("modules") or []:
        module_name = module_info.get("module") or ""
        display_name = module_info.get("display_name") or module_name
        if module_info.get("error"):
            module_errors.append(f"{display_name}: {module_info['error']}")
        for endpoint in module_info.get("endpoints") or []:
            if not output_capable(endpoint):
                continue
            endpoint_id = str(endpoint.get("id") or "").strip()
            if module_name and endpoint_id:
                value = f"{module_name}/{endpoint_id}"
                if allowed_tokens is not None and value not in allowed_tokens:
                    continue
                choices.append({"value": value, "label": endpoint_label(endpoint, display_name)})
    for choice in desktop_user_choices():
        if allowed_tokens is not None and choice["value"] not in allowed_tokens:
            continue
        choices.append(choice)
    choices.sort(key=lambda item: item["label"].lower())
    return choices, error, warning, module_errors


def transfer_panel(prefix, available_title, selected_title, choices, selected_values, choice_map):
    selected_lookup = set(selected_values)
    available_items = "\n".join(
        f'<div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="{h(choice["value"])}">{h(choice["label"])}</div>'
        for choice in choices
        if choice["value"] not in selected_lookup
    )
    selected_items = "\n".join(
        f'<div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="{h(value)}">{h(choice_map.get(value, value))}<input type="hidden" name="{"monitor_members[]" if prefix == "monitor" else "members[]"}" value="{h(value)}"></div>'
        for value in selected_values
        if value in choice_map
    )
    return f"""
<div class="transfer-list-container">
    <div class="tl-panel">
        <div class="tl-header">{h(available_title)}</div>
        <input type="text" id="{h(prefix)}Search" class="tl-search" placeholder="Search endpoints..." onkeyup="filterTransfer('{h(prefix)}')">
        <div class="tl-list" id="{h(prefix)}AvailableList" ondrop="dropToAvailable('{h(prefix)}', event)" ondragover="allowDrop(event)">{available_items}</div>
    </div>
    <div class="tl-controls">
        <button type="button" class="btn-primary" onclick="moveRight('{h(prefix)}')" title="Move Selected Right"><i class="fa-solid fa-angle-right"></i></button>
        <button type="button" class="btn-primary" onclick="moveLeft('{h(prefix)}')" title="Move Selected Left"><i class="fa-solid fa-angle-left"></i></button>
        <button type="button" class="btn-primary" onclick="moveUp('{h(prefix)}')" title="Move Selected Up"><i class="fa-solid fa-angle-up"></i></button>
        <button type="button" class="btn-primary" onclick="moveDown('{h(prefix)}')" title="Move Selected Down"><i class="fa-solid fa-angle-down"></i></button>
    </div>
    <div class="tl-panel">
        <div class="tl-header">{h(selected_title)}</div>
        <div class="tl-search-spacer" aria-hidden="true"></div>
        <div class="tl-list" id="{h(prefix)}SelectedList" ondrop="dropToSelected('{h(prefix)}', event)" ondragover="allowDrop(event)">{selected_items}</div>
    </div>
</div>"""


def tone_picker_body():
    items = [_audio_file_picker_item_html(item) for item in _audio_picker_options()]
    if not items:
        return '<div class="message-icon-picker-empty">No assets are stored on the server yet.</div>'
    return '<div class="message-icon-picker-grid">' + "".join(items) + "</div>"


def tone_card(field, title, value):
    return f"""<div class="tone-card">
    <div class="tone-summary">
        <div>
            <div class="tone-title">{h(title)}</div>
            <div id="{h(field)}ToneName" class="tone-name{' tone-empty' if not value else ''}">{h(value or 'No tone selected')}</div>
        </div>
        <div class="tone-actions">
            <input type="hidden" id="{h(field)}ToneInput" name="page_{h(field)}_tone" value="{h(value or '')}">
            <button type="button" class="btn-primary" onclick="openTonePicker('{h(field)}')"><i class="fa-solid fa-file-audio"></i> Select Audio File</button>
            <button type="button" class="tone-clear" onclick="clearTone('{h(field)}')">Clear</button>
        </div>
    </div>
</div>"""


def handle_request():
    user = require_user()
    if not isinstance(user, dict):
        return user
    if not (is_admin_user(user) or can_manage_groups(user)):
        abort(403)
    ensure_group_feature_schema()
    ctx = legacy_user_context(user)
    demo = demo_mode_enabled()
    conn = db()
    try:
        with conn.cursor() as cur:
            all_groups = fetch_group_rows(cur)
    finally:
        conn.close()
    groups = filter_group_rows_for_user(user, all_groups)
    all_group_lookup = {str(group.get("id") or ""): group for group in all_groups}
    choices, endpoint_error, endpoint_warning, module_errors = endpoint_choices(user, groups)
    allowed = {choice["value"] for choice in choices}
    choice_map = {choice["value"]: choice["label"] for choice in choices}

    if request.method == "POST":
        if demo:
            return demo_mode_iframe_html("manage-groups")
        action = request.form.get("action")
        if action == "delete":
            gid = request.form.get("group_id", "").strip()
            if gid:
                if not user_can_access_group(user, gid):
                    abort(403)
                delete_group_access_records(gid)
                execute("DELETE FROM `groups` WHERE id=%s", (gid,))
            return redirect("/admin/manage-groups")
        if action == "save":
            gid = request.form.get("group_id", "").strip()
            if gid and not user_can_access_group(user, gid):
                abort(403)
            name = request.form.get("name", "").strip()
            members = [item.strip() for item in request.form.getlist("members[]") if item.strip() in allowed]
            monitor_members = [item.strip() for item in request.form.getlist("monitor_members[]") if item.strip() in allowed]
            monitor_categories = serialize_monitor_categories(request.form.getlist("monitor_categories[]"))
            page_pre_tone = request.form.get("page_pre_tone", "").strip()
            page_post_tone = request.form.get("page_post_tone", "").strip()
            suspend_bells = "1" if request.form.get("suspend_bells_on_emergency") else "0"
            if name:
                params = (
                    name,
                    ",".join(members),
                    ",".join(monitor_members),
                    monitor_categories,
                    page_pre_tone,
                    page_post_tone,
                    suspend_bells,
                )
                if not gid:
                    gid = next_group_id()
                    columns = table_columns("groups")
                    insert_columns = ["id", "name", "members", "monitor_members", "monitor_categories", "page_pre_tone", "page_post_tone", "suspend_bells_on_emergency"]
                    insert_values = [gid, *params]
                    if "owner_user_id" in columns:
                        insert_columns.append("owner_user_id")
                        insert_values.append(user.get("id"))
                    execute(
                        f"INSERT INTO `groups` ({', '.join('`' + column + '`' for column in insert_columns)}) VALUES ({', '.join(['%s'] * len(insert_columns))})",
                        tuple(insert_values),
                    )
                else:
                    execute(
                        """
                        UPDATE `groups`
                        SET name=%s, members=%s, monitor_members=%s, monitor_categories=%s, page_pre_tone=%s, page_post_tone=%s, suspend_bells_on_emergency=%s
                        WHERE id=%s
                        """,
                        params + (gid,),
                    )
            return redirect("/admin/manage-groups")

    edit_id = request.args.get("edit", "")
    edit_group = next((group for group in groups if str(group.get("id")) == str(edit_id)), None)
    if edit_id and not edit_group:
        if edit_id in all_group_lookup:
            abort(403)
        abort(404)
    show_editor = bool(edit_group or "new" in request.args)
    selected_members = [part for part in re.split(r"[\s,]+", str((edit_group or {}).get("members") or "")) if part]
    selected_monitor_members = [part for part in re.split(r"[\s,]+", str((edit_group or {}).get("monitor_members") or "")) if part]
    selected_categories = set((edit_group or {}).get("monitor_categories") or [])

    notices = ""
    for text in [endpoint_error, endpoint_warning] + module_errors:
        if text:
            notices += f'<div class="error">{h(text)}</div>'

    if show_editor:
        if demo:
            return demo_mode_iframe_html("manage-groups")
        member_picker = '<p class="muted">No output endpoints available.</p>' if not choices else transfer_panel(
            "members",
            "Available Endpoints",
            "Selected Endpoints",
            choices,
            selected_members,
            choice_map,
        )
        monitor_picker = '<p class="muted">No output endpoints available.</p>' if not choices else transfer_panel(
            "monitor",
            "Available Endpoints",
            "Monitor Endpoints",
            choices,
            selected_monitor_members,
            choice_map,
        )
        categories_html = "\n".join(
            f"""<label class="md-checkbox-container">
    <input type="checkbox" name="monitor_categories[]" value="{h(category)}"{" checked" if category in selected_categories else ""}>
    <span class="md-checkmark"></span>
    <span class="checkbox-text"><span>{h(label)}</span></span>
</label>"""
            for category, label in (
                ("messages", "Messages"),
                ("paging", "Paging"),
                ("bells", "Bells"),
            )
        )
        content = f"""<div class="header-actions">
    <div>
        <h1>{"Edit Group" if edit_group else "New Group"}</h1>
    </div>
    <a class="btn-secondary" href="/admin/manage-groups"><i class="fa-solid fa-arrow-left"></i> Back</a>
</div>
{notices}
<form class="card editor-card" method="POST" action="/admin/manage-groups" onsubmit="syncSelectedInputs()">
    <input type="hidden" name="action" value="save">
    <input type="hidden" name="group_id" value="{h((edit_group or {}).get("id") or "")}">
    <div>
        <div class="form-section field">
            <label class="main-label" for="name">Name</label>
            <input id="name" name="name" value="{h((edit_group or {}).get("name") or "")}" required>
        </div>
        <div class="form-section">
            <label class="main-label">Endpoints</label>
            {member_picker}
        </div>
        <div class="form-section">
            <label class="main-label">Monitor Endpoints</label>
            {monitor_picker}
            <p class="section-help" style="margin-top:12px;">Selected endpoints will receive a notification when a broadcast is sent to this group.</p>
            <div class="checkbox-list" style="margin-top:16px;">{categories_html}</div>
        </div>
        <div class="form-section">
            <div class="field-group">
                {tone_card("pre", "Pre-page tone", (edit_group or {}).get("page_pre_tone") or "")}
                {tone_card("post", "Post-page tone", (edit_group or {}).get("page_post_tone") or "")}
            </div>
        </div>
        <div class="form-section checkbox-list">
            <label class="md-checkbox-container">
                <input type="checkbox" name="suspend_bells_on_emergency" value="1"{" checked" if (edit_group or {}).get("suspend_bells_on_emergency") else ""}>
                <span class="md-checkmark"></span>
                <span class="checkbox-text"><span>Suspend bells while an emergency message is in effect</span></span>
            </label>
        </div>
        <div class="field-actions">
            <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Group</button>
            <a class="btn-secondary" href="/admin/manage-groups">Cancel</a>
        </div>
    </div>
</form>
<div id="tonePickerBackdrop" class="message-icon-picker-backdrop" onclick="closeTonePicker()"></div>
<div id="tonePickerModal" class="message-icon-picker-modal" role="dialog" aria-modal="true" aria-labelledby="tonePickerTitle">
    <div class="message-icon-picker-header">
        <h2 id="tonePickerTitle">Select Audio File</h2>
        <button type="button" class="message-icon-picker-close" onclick="closeTonePicker()" aria-label="Close">&times;</button>
    </div>
    <div class="message-icon-picker-body">
        {tone_picker_body()}
    </div>
</div>"""
    else:
        if groups:
            rows = []
            for group in groups:
                count = group_member_count(group.get("members"))
                edit_href = "javascript:openDemoModePopup('manage-groups')" if demo else f"/admin/manage-groups?edit={h(group.get('id'))}"
                delete_onsubmit = "openDemoModePopup('manage-groups'); return false;" if demo else "return confirm('Delete this group?')"
                rows.append(
                    f"""<li class="group-item">
    <div class="group-main">
        <div class="group-name">{h(group.get("name"))}</div>
        <div class="group-members">{count} member{'s' if count != 1 else ''}</div>
    </div>
    <div class="group-actions">
        <a class="icon-action" href="{edit_href}" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
        <form method="POST" action="/admin/manage-groups" onsubmit="{delete_onsubmit}">
            <input type="hidden" name="action" value="delete">
            <input type="hidden" name="group_id" value="{h(group.get("id"))}">
            <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
        </form>
    </div>
</li>"""
                )
            group_list = '<ul class="group-list">' + "".join(rows) + "</ul>"
        else:
            group_list = '<p class="muted">No groups yet.</p>'
        new_href = "javascript:openDemoModePopup('manage-groups')" if demo else "/admin/manage-groups?new=1"
        content = f"""<div class="header-actions">
    <div>
        <h1>Manage Groups</h1>
    </div>
    <a class="btn-primary" href="{new_href}"><i class="fa-solid fa-plus"></i> New Group</a>
</div>
{notices}
<div class="card">
    {group_list}
</div>"""
    return legacy_page("Manage Groups", ctx, "groups", GROUPS_STYLE, content, GROUPS_SCRIPT)
