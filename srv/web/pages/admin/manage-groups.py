
from srv.web.app import *

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
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.header-actions { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }
.card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:4px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; }
.group-list { list-style:none; margin:0; padding:0; }
.group-item { display:flex; justify-content:space-between; gap:14px; padding:14px 0; border-bottom:1px solid #EEE; }
.group-item:last-child { border-bottom:none; }
.group-main { flex:1; min-width:0; }
.group-name { font-weight:500; color:#202124; }
.group-members { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.group-actions { display:flex; align-items:center; gap:4px; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.editor-card { margin-top:18px; }
.transfer-list-container { display:flex; gap:14px; align-items:stretch; margin-top:8px; }
.tl-panel { flex:1; min-width:0; }
.tl-header { font-weight:500; margin-bottom:8px; color:#555; }
.tl-search { width:100%; box-sizing:border-box; border:1px solid #CCC; border-radius:4px; padding:9px; margin-bottom:8px; font:inherit; }
.tl-list { min-height:280px; max-height:360px; overflow:auto; border:1px solid #EEE; border-radius:8px; padding:8px; background:#FFF; }
.tl-item { padding:8px 10px; margin-bottom:4px; background:#FAFAFA; border:1px solid #EEE; cursor:pointer; user-select:none; border-radius:3px; font-size:0.95em; }
.tl-item.selected { background:#1976D2; color:#FFF; border-color:#1565C0; }
.tl-item.dragging { opacity:0.5; }
.tl-controls { display:flex; flex-direction:column; justify-content:center; gap:8px; }
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
.muted { color:#777; font-size:0.9em; }
@media(max-width:900px){ .transfer-list-container{ flex-direction:column; } .tl-controls{ flex-direction:row; justify-content:flex-start; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.field label,.muted,.group-members{ color:#BBB; }
.field input { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; }
.group-item { border-bottom:1px solid #333; }
.group-name { color:#EDEDED; }
.tl-header { color:#BBB; }
.tl-search { background:#121212; border-color:#444; color:#E0E0E0; }
.tl-list { background:#121212; border-color:#333; }
.tl-item { background:#242424; border-color:#333; }
.tl-item.selected { background:#BB86FC; color:#000; border-color:#A370F7; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
}
"""

GROUPS_SCRIPT = r"""
let draggedItem = null;
function selectItem(el) {
  const siblings = el.parentElement.querySelectorAll('.tl-item');
  siblings.forEach(s => s.classList.remove('selected'));
  el.classList.add('selected');
}
function filterDevices() {
  const search = document.getElementById('deviceSearch').value.toLowerCase();
  const items = document.getElementById('availableDeviceList').querySelectorAll('.tl-item');
  items.forEach(item => {
    if (item.innerText.toLowerCase().includes(search)) item.style.display = '';
    else { item.style.display = 'none'; item.classList.remove('selected'); }
  });
}
function appendHiddenInput(item) {
  if (!item.querySelector('input[type="hidden"]')) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'members[]';
    input.value = item.getAttribute('data-value');
    item.appendChild(input);
  }
}
function removeHiddenInput(item) {
  const input = item.querySelector('input[type="hidden"]');
  if (input) input.remove();
}
function moveRight() {
  const selected = document.querySelector('#availableDeviceList .tl-item.selected');
  if (selected) {
    selected.classList.remove('selected');
    appendHiddenInput(selected);
    document.getElementById('selectedDeviceList').appendChild(selected);
  }
}
function moveLeft() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected) {
    selected.classList.remove('selected');
    removeHiddenInput(selected);
    document.getElementById('availableDeviceList').appendChild(selected);
    filterDevices();
  }
}
function moveUp() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected && selected.previousElementSibling) selected.parentNode.insertBefore(selected, selected.previousElementSibling);
}
function moveDown() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected && selected.nextElementSibling) selected.parentNode.insertBefore(selected.nextElementSibling, selected);
}
function dragStart(e) {
  draggedItem = e.target;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedItem.getAttribute('data-value'));
  setTimeout(() => draggedItem.classList.add('dragging'), 0);
}
document.addEventListener('dragend', function(e) {
  if (e.target.classList && e.target.classList.contains('tl-item')) {
    e.target.classList.remove('dragging');
    draggedItem = null;
  }
});
function allowDrop(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}
function dropToAvailable(e) {
  e.preventDefault();
  if (draggedItem && draggedItem.parentElement.id === 'selectedDeviceList') {
    removeHiddenInput(draggedItem);
    draggedItem.classList.remove('selected');
    document.getElementById('availableDeviceList').appendChild(draggedItem);
    filterDevices();
  }
}
function dropToSelected(e) {
  e.preventDefault();
  if (!draggedItem) return;
  if (draggedItem.parentElement.id === 'availableDeviceList') {
    appendHiddenInput(draggedItem);
    draggedItem.classList.remove('selected');
  }
  const list = document.getElementById('selectedDeviceList');
  const afterElement = getDragAfterElement(list, e.clientY);
  if (afterElement == null) list.appendChild(draggedItem);
  else list.insertBefore(draggedItem, afterElement);
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
function syncSelectedInputs() {
  document.querySelectorAll('#selectedDeviceList .tl-item').forEach(appendHiddenInput);
}
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


def endpoint_choices():
    data = endpoint_ipc("LIST_ENDPOINTS")
    warning = data.get("warning") or ""
    error = None if data.get("ok", True) else data.get("error") or "Endpoint manager returned an error."
    module_errors = []
    choices = []
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
                choices.append({"value": f"{module_name}/{endpoint_id}", "label": endpoint_label(endpoint, display_name)})
    choices.sort(key=lambda item: item["label"].lower())
    return choices, error, warning, module_errors


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    choices, endpoint_error, endpoint_warning, module_errors = endpoint_choices()
    allowed = {choice["value"] for choice in choices}

    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            gid = request.form.get("group_id", "").strip()
            if gid:
                execute("DELETE FROM `groups` WHERE id=%s", (gid,))
            return redirect("/admin/manage-groups")
        if action == "save":
            gid = request.form.get("group_id", "").strip()
            name = request.form.get("name", "").strip()
            members = [item.strip() for item in request.form.getlist("members[]") if item.strip() in allowed]
            if name:
                if not gid:
                    gid = next_group_id()
                    execute("INSERT INTO `groups` (id, name, members) VALUES (%s,%s,%s)", (gid, name, ",".join(members)))
                else:
                    execute("UPDATE `groups` SET name=%s, members=%s WHERE id=%s", (name, ",".join(members), gid))
            return redirect("/admin/manage-groups")

    groups = query_all("SELECT id, name, members FROM `groups` ORDER BY name ASC")
    edit_id = request.args.get("edit", "")
    edit_group = next((group for group in groups if str(group.get("id")) == str(edit_id)), None)
    show_editor = bool(edit_group or "new" in request.args)
    selected_members = [part for part in re.split(r"[\s,]+", str((edit_group or {}).get("members") or "")) if part]
    selected_lookup = set(selected_members)
    choice_map = {choice["value"]: choice["label"] for choice in choices}

    notices = ""
    for text in [endpoint_error, endpoint_warning] + module_errors:
        if text:
            notices += f'<div class="error">{h(text)}</div>'

    if show_editor:
        available_items = "\n".join(
            f'<div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="{h(choice["value"])}">{h(choice["label"])}</div>'
            for choice in choices
            if choice["value"] not in selected_lookup
        )
        selected_items = "\n".join(
            f'<div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="{h(member)}">{h(choice_map[member])}<input type="hidden" name="members[]" value="{h(member)}"></div>'
            for member in selected_members
            if member in choice_map
        )
        output_notice = '<p class="muted">No output endpoints available.</p>' if not choices else f"""
                <div class="transfer-list-container">
                    <div class="tl-panel">
                        <div class="tl-header">Available Devices</div>
                        <input type="text" id="deviceSearch" class="tl-search" placeholder="Search devices..." onkeyup="filterDevices()">
                        <div class="tl-list" id="availableDeviceList" ondrop="dropToAvailable(event)" ondragover="allowDrop(event)">{available_items}</div>
                    </div>
                    <div class="tl-controls">
                        <button type="button" class="btn-primary" onclick="moveRight()" title="Move Selected Right"><i class="fa-solid fa-angle-right"></i></button>
                        <button type="button" class="btn-primary" onclick="moveLeft()" title="Move Selected Left"><i class="fa-solid fa-angle-left"></i></button>
                        <button type="button" class="btn-primary" onclick="moveUp()" title="Move Selected Up"><i class="fa-solid fa-angle-up"></i></button>
                        <button type="button" class="btn-primary" onclick="moveDown()" title="Move Selected Down"><i class="fa-solid fa-angle-down"></i></button>
                    </div>
                    <div class="tl-panel">
                        <div class="tl-header">Selected Devices</div>
                        <div class="tl-list" id="selectedDeviceList" ondrop="dropToSelected(event)" ondragover="allowDrop(event)">{selected_items}</div>
                    </div>
                </div>"""
        content = f"""    <div class="header-actions">
        <h1>{"Edit Group" if edit_group else "New Group"}</h1>
        <a class="btn-secondary" href="/admin/manage-groups"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>
    {notices}
    <form class="card editor-card" method="POST" action="/admin/manage-groups" onsubmit="syncSelectedInputs()">
        <h2>{"Edit Group" if edit_group else "New Group"}</h2>
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="group_id" value="{h((edit_group or {}).get("id") or "")}">
        <div class="field">
            <label for="name">Name</label>
            <input id="name" name="name" value="{h((edit_group or {}).get("name") or "")}" required>
        </div>
        {output_notice}
        <div style="margin-top:16px;">
            <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Group</button>
            <a class="btn-secondary" href="/admin/manage-groups">Cancel</a>
        </div>
    </form>"""
    else:
        if groups:
            rows = []
            for group in groups:
                count = group_member_count(group.get("members"))
                suffix = "" if count == 1 else "s"
                rows.append(
                    f"""<li class="group-item">
                            <div class="group-main">
                                <div class="group-name">{h(group.get("name"))}</div>
                                <div class="group-members">{h(count)} member{suffix}</div>
                            </div>
                            <div class="group-actions">
                                <a class="icon-action" href="/admin/manage-groups?edit={h(group.get("id"))}" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                                <form method="POST" action="/admin/manage-groups" onsubmit="return confirm('Delete this group?')">
                                    <input type="hidden" name="action" value="delete">
                                    <input type="hidden" name="group_id" value="{h(group.get("id"))}">
                                    <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                </form>
                            </div>
                        </li>"""
                )
            group_list = '<ul class="group-list">' + "\n".join(rows) + "</ul>"
        else:
            group_list = '<p class="muted">No groups yet.</p>'
        content = f"""    <div class="header-actions">
        <h1>Manage Groups</h1>
        <a class="btn-primary" href="/admin/manage-groups?new=1"><i class="fa-solid fa-plus"></i> New Group</a>
    </div>
    {notices}
    <div class="card">
        <h2>Groups</h2>
        {group_list}
    </div>"""
    return legacy_page("Manage Groups", ctx, "groups", GROUPS_STYLE, content, GROUPS_SCRIPT)
