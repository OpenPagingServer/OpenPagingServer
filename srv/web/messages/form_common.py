
from srv.web.app import h


MESSAGE_FORM_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0s; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0s; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
@media(min-width:768px){ #mobile-header{ display:none; } }
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-primary:hover { background:#1565C0; }
.form-group { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #F0F0F0; }
.form-group:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.form-group label.main-label { display: block; margin-bottom: 4px; font-weight: 500; font-size: 1.1em; }
.form-control { width: 100%; padding: 10px; border: 1px solid #DDD; border-radius: 4px; box-sizing: border-box; background: #FFF; color: #000; font-family: inherit; }
.form-control.textarea-long { min-height: 140px; resize: vertical; white-space: pre-wrap; }
.help-text { font-size: 0.9em; color: #666; margin-top: 0; margin-bottom: 12px; line-height: 1.4; }
.radio-group label { display: block; margin-bottom: 8px; font-weight: normal; cursor: pointer; }
.radio-group input[type="radio"] { margin-right: 8px; }
.color-picker-container { display: flex; align-items: center; gap: 12px; }
.color-picker-input { height: 42px; width: 42px; padding: 0; border: 1px solid #DDD; border-radius: 4px; cursor: pointer; background: none; }
.transfer-list-container { display: flex; gap: 15px; align-items: stretch; height: 300px; margin-top: 10px; }
.tl-panel { flex: 1; display: flex; flex-direction: column; border: 1px solid #DDD; border-radius: 4px; background: #FFF; overflow: hidden; }
.tl-panel input.tl-search { border: none; border-bottom: 1px solid #DDD; border-radius: 0; padding: 10px; font-family: inherit; width: 100%; box-sizing: border-box; outline: none; }
.tl-header { background: #F5F5F5; padding: 8px 10px; font-weight: 500; border-bottom: 1px solid #DDD; font-size: 0.9em; }
.tl-list { flex: 1; overflow-y: auto; padding: 5px; min-height: 50px; }
.tl-item { padding: 8px 10px; margin-bottom: 4px; background: #FAFAFA; border: 1px solid #EEE; cursor: pointer; user-select: none; border-radius: 3px; font-size: 0.95em; }
.tl-item:hover { background: #F0F0F0; }
.tl-item.selected { background: #1976D2; color: #FFF; border-color: #1565C0; }
.tl-item.dragging { opacity: 0.5; }
.tl-controls { display: flex; flex-direction: column; justify-content: center; gap: 10px; }
.tl-controls .btn-primary { width: 40px; height: 40px; justify-content: center; padding: 0; font-size: 16px; }
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:10px; border-radius:6px; margin-bottom:12px; }
@media(prefers-color-scheme:dark){
    body,html{ background-color:#121212; color:#E0E0E0; }
    #sidebar{ background-color:#424242; }
    #sidebar h2{ background-color:#303030; color:#FFF; }
    #sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
    #sidebar a.active,#sidebar a:hover{ background-color:#505050; }
    #mobile-header{ background-color:#424242; }
    #content{ background-color:#121212; }
    .info-card{ border:1px solid #333; background-color:#1E1E1E; }
    .form-control { background: #333; border: 1px solid #444; color: #FFF; }
    .btn-primary { background:#BB86FC; color:#000; }
    .btn-primary:hover { background:#A370F7; }
    .form-group { border-bottom: 1px solid #333; }
    .help-text { color: #AAA; }
    .color-picker-input { border: 1px solid #555; }
    .tl-panel { border-color: #444; background: #222; }
    .tl-header { background: #2A2A2A; border-bottom-color: #444; color: #E0E0E0; }
    .tl-panel input.tl-search { background: #222; border-bottom-color: #444; color: #FFF; }
    .tl-item { background: #2A2A2A; border-color: #333; color: #E0E0E0; }
    .tl-item:hover { background: #333; }
    .tl-item.selected { background: #BB86FC; color: #000; border-color: #A370F7; }
    .error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
}
"""


MESSAGE_FORM_SCRIPT = r"""
function toggleFields() {
    const typeRadios = document.getElementsByName('type');
    let selectedType = '';
    for (let i = 0; i < typeRadios.length; i++) {
        if (typeRadios[i].checked) {
            selectedType = typeRadios[i].value;
            break;
        }
    }
    const visualFields = document.getElementById('visual-fields');
    const audioFields = document.getElementById('audio-fields');
    if (visualFields) visualFields.style.display = 'none';
    if (audioFields) audioFields.style.display = 'none';
    if (visualFields && (selectedType === 'text' || selectedType === 'text+audio')) visualFields.style.display = 'block';
    if (audioFields && (selectedType === 'audio' || selectedType === 'text+audio')) audioFields.style.display = 'block';
}
const colorPicker = document.getElementById('colorPicker');
const colorHex = document.getElementById('colorHex');
if (colorPicker && colorHex) {
    colorPicker.addEventListener('input', function() {
        colorHex.value = this.value.substring(1).toUpperCase();
    });
    colorHex.addEventListener('input', function() {
        let val = this.value.replace(/[^A-Fa-f0-9]/g, '');
        this.value = val.toUpperCase();
        if (val.length === 6) colorPicker.value = '#' + val;
    });
}
let draggedItem = null;
function selectItem(el) {
    const siblings = el.parentElement.querySelectorAll('.tl-item');
    siblings.forEach(s => s.classList.remove('selected'));
    el.classList.add('selected');
}
function filterAudio() {
    const searchInput = document.getElementById('audioSearch');
    const list = document.getElementById('availableAudioList');
    if (!searchInput || !list) return;
    const search = searchInput.value.toLowerCase();
    const items = list.querySelectorAll('.tl-item');
    items.forEach(item => {
        if (item.innerText.toLowerCase().includes(search)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
            item.classList.remove('selected');
        }
    });
}
function appendHiddenInput(item) {
    if (!item.querySelector('input[type="hidden"]')) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'audio_files[]';
        input.value = item.getAttribute('data-value');
        item.appendChild(input);
    }
}
function removeHiddenInput(item) {
    const input = item.querySelector('input[type="hidden"]');
    if (input) input.remove();
}
function moveRight() {
    const selected = document.querySelector('#availableAudioList .tl-item.selected');
    if (selected) {
        selected.classList.remove('selected');
        appendHiddenInput(selected);
        document.getElementById('selectedAudioList').appendChild(selected);
    }
}
function moveLeft() {
    const selected = document.querySelector('#selectedAudioList .tl-item.selected');
    if (selected) {
        selected.classList.remove('selected');
        removeHiddenInput(selected);
        document.getElementById('availableAudioList').appendChild(selected);
        filterAudio();
    }
}
function moveUp() {
    const selected = document.querySelector('#selectedAudioList .tl-item.selected');
    if (selected && selected.previousElementSibling) selected.parentNode.insertBefore(selected, selected.previousElementSibling);
}
function moveDown() {
    const selected = document.querySelector('#selectedAudioList .tl-item.selected');
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
    if (draggedItem && draggedItem.parentElement.id === 'selectedAudioList') {
        removeHiddenInput(draggedItem);
        draggedItem.classList.remove('selected');
        document.getElementById('availableAudioList').appendChild(draggedItem);
        filterAudio();
    }
}
function dropToSelected(e) {
    e.preventDefault();
    if (!draggedItem) return;
    if (draggedItem.parentElement.id === 'availableAudioList') {
        appendHiddenInput(draggedItem);
        draggedItem.classList.remove('selected');
    }
    const list = document.getElementById('selectedAudioList');
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
"""


def message_multiline_text(value):
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def audio_item(file_name, selected=False):
    hidden = f'<input type="hidden" name="audio_files[]" value="{h(file_name)}">' if selected else ""
    return (
        f'<div class="tl-item" draggable="true" ondragstart="dragStart(event)" '
        f'onclick="selectItem(this)" data-value="{h(file_name)}">{h(file_name)}{hidden}</div>'
    )


def audio_transfer_html(available_files, selected_files=None):
    selected_files = selected_files or []
    selected_lookup = {str(name) for name in selected_files}
    available = [name for name in available_files if str(name) not in selected_lookup]
    available_items = "\n".join(audio_item(name) for name in available)
    selected_items = "\n".join(audio_item(name, True) for name in selected_files)
    return f"""
                <div class="transfer-list-container">
                    <div class="tl-panel">
                        <div class="tl-header">Available Files</div>
                        <input type="text" id="audioSearch" class="tl-search" placeholder="Search files..." onkeyup="filterAudio()">
                        <div class="tl-list" id="availableAudioList" ondrop="dropToAvailable(event)" ondragover="allowDrop(event)">
                            {available_items}
                        </div>
                    </div>

                    <div class="tl-controls">
                        <button type="button" class="btn-primary" onclick="moveRight()" title="Move Selected Right"><i class="fa-solid fa-angle-right"></i></button>
                        <button type="button" class="btn-primary" onclick="moveLeft()" title="Move Selected Left"><i class="fa-solid fa-angle-left"></i></button>
                        <button type="button" class="btn-primary" onclick="moveUp()" title="Move Selected Up"><i class="fa-solid fa-angle-up"></i></button>
                        <button type="button" class="btn-primary" onclick="moveDown()" title="Move Selected Down"><i class="fa-solid fa-angle-down"></i></button>
                    </div>

                    <div class="tl-panel">
                        <div class="tl-header">Selected Files (In Order)</div>
                        <div class="tl-list" id="selectedAudioList" ondrop="dropToSelected(event)" ondragover="allowDrop(event)">
                            {selected_items}
                        </div>
                    </div>
                </div>"""
