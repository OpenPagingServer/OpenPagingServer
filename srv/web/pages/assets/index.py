"""Assets page ported from the original PHP implementation."""

import mimetypes

from srv.web.app import *

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTS = {"txt", "jpg", "png", "bmp", "wav", "mp3"}

ASSETS_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#F8FAFD; height:100%; color:#202124; }
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
#content{ margin-left:220px; padding:28px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding:84px 14px 20px 14px; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
.page-top{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px; }
.page-title h1{ margin:0; font-weight:400; font-size:2em; }
.muted{ color:#5F6368; font-size:.92em; }
.toolbar{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.button{ background:#1A73E8; color:#FFF; border:none; border-radius:999px; padding:10px 16px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:8px; text-decoration:none; box-shadow:0 1px 2px rgba(60,64,67,.25); }
.button:hover{ background:#1765CC; }
.button.danger{ background:#C62828; border-radius:8px; box-shadow:none; }
.button.subtle{ background:#FFF; color:#1A73E8; border:1px solid #DADCE0; box-shadow:none; border-radius:8px; }
.success{ background:#E6F4EA; border:1px solid #CEEAD6; color:#137333; padding:12px 14px; border-radius:12px; margin-bottom:14px; }
.error{ background:#FCE8E6; border:1px solid #F6AEA9; color:#A50E0E; padding:12px 14px; border-radius:12px; margin-bottom:14px; }
.asset-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:16px; }
.asset-card{ background:#FFF; border:1px solid #DADCE0; border-radius:16px; overflow:visible; position:relative; box-shadow:0 1px 2px rgba(60,64,67,.08); }
.preview-box{ height:150px; background:#F1F3F4; border-radius:16px 16px 0 0; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#5F6368; font-size:2.4em; }
.preview-box img{ width:100%; height:100%; object-fit:cover; }
.preview-box audio{ width:calc(100% - 24px); }
.asset-info{ display:flex; gap:10px; align-items:center; padding:12px 12px 12px 14px; min-height:54px; }
.asset-icon{ color:#5F6368; width:24px; display:flex; justify-content:center; }
.asset-name-wrap{ min-width:0; flex:1; }
.file-name{ font-weight:500; font-size:.95em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.file-meta{ color:#5F6368; font-size:.8em; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.menu-button{ width:34px; height:34px; border:none; background:transparent; color:#5F6368; border-radius:50%; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:1em; }
.menu-button:hover{ background:#F1F3F4; }
.asset-menu{ display:none; position:absolute; right:10px; bottom:48px; min-width:220px; background:#FFF; border:1px solid #DADCE0; border-radius:12px; box-shadow:0 8px 24px rgba(60,64,67,.25); z-index:30; overflow:hidden; padding:6px; }
.asset-card.menu-open .asset-menu{ display:block; }
.menu-link,.menu-action{ width:100%; border:none; background:transparent; color:#202124; padding:10px 12px; border-radius:8px; display:flex; align-items:center; gap:10px; text-decoration:none; font:inherit; cursor:pointer; box-sizing:border-box; text-align:left; }
.menu-link:hover,.menu-action:hover{ background:#F1F3F4; }
.menu-action.danger{ color:#B3261E; }
.rename-inline{ padding:8px; display:none; border-top:1px solid #E8EAED; margin-top:4px; }
.rename-inline.active{ display:block; }
.rename-inline .control{ width:100%; margin-bottom:8px; }
.control{ padding:10px; border:1px solid #DADCE0; border-radius:8px; font:inherit; box-sizing:border-box; background:#FFF; color:#202124; }
.empty-state{ background:#FFF; border:1px dashed #DADCE0; border-radius:16px; padding:36px; text-align:center; color:#5F6368; }
.modal-backdrop{ display:none; position:fixed; inset:0; background:rgba(32,33,36,.55); z-index:2000; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
.modal-backdrop.active{ display:flex; }
.modal-card{ width:100%; max-width:460px; background:#FFF; border-radius:18px; box-shadow:0 20px 60px rgba(0,0,0,.28); overflow:hidden; }
.modal-header{ display:flex; align-items:center; justify-content:space-between; padding:18px 20px; border-bottom:1px solid #E8EAED; }
.modal-header h2{ margin:0; font-weight:400; font-size:1.25em; }
.modal-close{ border:none; background:transparent; width:36px; height:36px; border-radius:50%; cursor:pointer; color:#5F6368; font-size:1.1em; }
.modal-close:hover{ background:#F1F3F4; }
.modal-body{ padding:20px; }
.upload-box{ border:2px dashed #DADCE0; border-radius:16px; padding:22px; text-align:center; background:#F8FAFD; }
.upload-box input{ width:100%; margin-top:14px; }
.modal-actions{ display:flex; justify-content:flex-end; gap:10px; padding:16px 20px; border-top:1px solid #E8EAED; }
@media(max-width:767px){
.page-top{ align-items:center; }
.page-title h1{ font-size:1.45em; }
.asset-grid{ display:block; }
.asset-card{ display:flex; align-items:center; border-radius:14px; margin-bottom:10px; overflow:visible; }
.preview-box{ width:56px; height:56px; flex:none; border-radius:12px; margin:10px; font-size:1.4em; }
.preview-box audio{ display:none; }
.asset-info{ flex:1; padding:10px 10px 10px 0; min-width:0; }
.asset-icon{ display:none; }
.asset-menu{ position:absolute; right:10px; top:54px; bottom:auto; }
}
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E8EAED; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.asset-card,.modal-card,.empty-state{ background:#1E1E1E; border-color:#333; }
.preview-box,.upload-box{ background:#2A2A2A; color:#BBB; border-color:#444; }
.asset-menu{ background:#242424; border-color:#444; }
.menu-link,.menu-action{ color:#E8EAED; }
.menu-link:hover,.menu-action:hover,.menu-button:hover,.modal-close:hover{ background:#333; }
.file-meta,.muted,.asset-icon,.menu-button,.modal-close{ color:#BBB; }
.control{ background:#171717; border-color:#444; color:#EEE; }
.button{ background:#BB86FC; color:#000; }
.button:hover{ background:#A874E8; }
.button.subtle{ background:transparent; color:#BB86FC; border-color:#BB86FC; }
.button.danger{ background:#CF6679; color:#000; }
.success{ background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.modal-header,.modal-actions,.rename-inline{ border-color:#333; }
}
"""

ASSETS_SCRIPT = r"""
function openUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.add("active");
}
function closeUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.remove("active");
}
function modalBackdropClick(event) {
  if (event.target.id === "upload-modal") closeUploadModal();
}
function closeAllAssetMenus(exceptCard) {
  document.querySelectorAll(".asset-card.menu-open").forEach(card => {
    if (card !== exceptCard) card.classList.remove("menu-open");
  });
}
function toggleAssetMenu(event, button) {
  event.stopPropagation();
  const card = button.closest(".asset-card");
  const isOpen = card.classList.contains("menu-open");
  closeAllAssetMenus(card);
  card.classList.toggle("menu-open", !isOpen);
}
function showRenameForm(event, button) {
  event.stopPropagation();
  const menu = button.closest(".asset-menu");
  const form = menu.querySelector(".rename-inline");
  form.classList.toggle("active");
  const input = form.querySelector("input[name='new_name']");
  if (form.classList.contains("active") && input) {
    input.focus();
    input.select();
  }
}
document.addEventListener("click", function(event) {
  if (!event.target.closest(".asset-card")) closeAllAssetMenus(null);
});
document.addEventListener("keydown", function(event) {
  if (event.key === "Escape") {
    closeUploadModal();
    closeAllAssetMenus(null);
  }
});
document.addEventListener("DOMContentLoaded", function() {
  const alerts = document.querySelectorAll('.alert-msg');
  alerts.forEach(function(alert) {
    setTimeout(function() {
      alert.style.transition = "opacity 0.5s ease";
      alert.style.opacity = "0";
      setTimeout(() => alert.remove(), 500);
    }, 5000);
  });
});
"""


def asset_perms(value):
    return {part.strip().lower() for part in str(value or "").split(",") if part.strip()}


def has_asset_perm(value):
    perms = asset_perms(value)
    return "all" in perms or "asset-edit" in perms


def format_bytes(value):
    value = int(value or 0)
    if value >= 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024 * 1024):.2f} GB"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def clean_asset_name(name):
    name = re.sub(r"[\0/\\]", "", str(name or ""))
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .\t\n\r\0\x0B")
    return name


def asset_ext(name):
    return Path(str(name)).suffix.lower().lstrip(".")


def safe_asset_path(name):
    clean = clean_asset_name(name)
    if not clean:
        raise ValueError("Invalid filename.")
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    base = ASSET_DIR.resolve()
    path = (base / clean).resolve()
    if base not in path.parents:
        raise ValueError("Invalid asset path.")
    return path


def read_sample(path, size=65536):
    with open(path, "rb") as handle:
        return handle.read(size)


def looks_like_text(path):
    sample = read_sample(path)
    if b"\0" in sample or re.search(br"<\?(php|=)?", sample, re.IGNORECASE):
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def asset_kind(path, name):
    ext = asset_ext(name)
    if not path.is_file():
        return "unsupported"
    sample = read_sample(path, 65536)
    if ext == "jpg" and sample.startswith(b"\xff\xd8\xff"):
        return "image"
    if ext == "png" and sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if ext == "bmp" and sample.startswith(b"BM"):
        return "image"
    if ext == "wav" and sample[:4] == b"RIFF" and sample[8:12] == b"WAVE":
        return "audio"
    if ext == "mp3":
        has_frame = len(sample) >= 2 and sample[0] == 0xFF and (sample[1] & 0xE0) == 0xE0
        if sample.startswith(b"ID3") or has_frame:
            return "audio"
    if ext == "txt" and looks_like_text(path):
        return "text"
    return "unsupported"


def validate_asset_file(path, name):
    if asset_ext(name) not in ALLOWED_EXTS:
        return "Only txt, jpg, png, bmp, wav, and mp3 files are allowed."
    if not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
        return f"File is missing or too large. Current upload limit is {format_bytes(MAX_UPLOAD_BYTES)}."
    if asset_kind(path, name) == "unsupported":
        return "The file contents do not match an allowed asset type."
    return ""


def asset_mime(kind, name):
    if kind == "image":
        return {"png": "image/png", "bmp": "image/bmp"}.get(asset_ext(name), "image/jpeg")
    if kind == "audio":
        return "audio/wav" if asset_ext(name) == "wav" else "audio/mpeg"
    if kind == "text":
        return "text/plain; charset=UTF-8"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def asset_value_tokens(value):
    return [] if value in (None, "") else str(value).split(":")


def asset_value_has_reference(value, filename):
    return any(token.strip() == filename for token in asset_value_tokens(value))


def asset_value_replace_reference(value, old_name, new_name):
    tokens = asset_value_tokens(value)
    changed = False
    for index, token in enumerate(tokens):
        if token.strip() == old_name:
            tokens[index] = new_name
            changed = True
    return ":".join(tokens) if changed else value


def messages_using_asset(filename):
    matches = []
    for row in query_all("SELECT messageid, name, icon, image, audio FROM messages"):
        if (
            asset_value_has_reference(row.get("icon"), filename)
            or asset_value_has_reference(row.get("image"), filename)
            or asset_value_has_reference(row.get("audio"), filename)
        ):
            matches.append(row)
    return matches


def replace_message_references(old_name, new_name):
    rows = query_all("SELECT messageid, icon, image, audio FROM messages")
    for row in rows:
        icon = asset_value_replace_reference(row.get("icon"), old_name, new_name)
        image = asset_value_replace_reference(row.get("image"), old_name, new_name)
        audio = asset_value_replace_reference(row.get("audio"), old_name, new_name)
        if icon != row.get("icon") or image != row.get("image") or audio != row.get("audio"):
            execute(
                "UPDATE messages SET icon=%s, image=%s, audio=%s WHERE messageid=%s",
                (icon, image, audio, row.get("messageid")),
            )


def user_asset_permissions(user):
    row = query_one("SELECT role, userperm, adminperm FROM users WHERE id=%s LIMIT 1", (user.get("id"),)) or {}
    role = row.get("role") or user.get("role") or ""
    is_admin = role in {"admin", "tempadmin"}
    can_user_edit = has_asset_perm(row.get("userperm"))
    can_admin_edit = is_admin and has_asset_perm(row.get("adminperm"))
    return is_admin, can_user_edit or can_admin_edit, can_admin_edit


def render_asset_card(asset, can_edit, can_delete):
    raw_url = "/assets/?raw=" + urlencode({"": asset["name"]})[1:]
    if asset["kind"] == "image":
        preview = f'<img src="{h(raw_url)}" alt="">'
        icon = "image"
    elif asset["kind"] == "audio":
        preview = f'<audio controls src="{h(raw_url)}"></audio>'
        icon = "music"
    elif asset["kind"] == "text":
        preview = '<i class="fa-solid fa-file-lines"></i>'
        icon = "file-lines"
    else:
        preview = '<i class="fa-solid fa-file-circle-question"></i>'
        icon = "file-circle-question"
    rename = ""
    if can_edit:
        rename = f"""
                            <button class="menu-action" type="button" onclick="showRenameForm(event, this)"><i class="fa-solid fa-pen"></i> Rename</button>
                            <div class="rename-inline">
                                <form method="post">
                                    <input type="hidden" name="action" value="rename">
                                    <input type="hidden" name="file" value="{h(asset["name"])}">
                                    <input class="control" name="new_name" value="{h(asset["name"])}" required>
                                    <button class="button subtle" type="submit"><i class="fa-solid fa-check"></i> Save</button>
                                </form>
                            </div>"""
    delete = ""
    if can_delete:
        delete = f"""
                            <form method="post" onsubmit="return confirm('Delete this asset?')">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="file" value="{h(asset["name"])}">
                                <button class="menu-action danger" type="submit"><i class="fa-solid fa-trash"></i> Delete</button>
                            </form>"""
    return f"""
                <article class="asset-card">
                    <a class="preview-box" href="{h(raw_url)}" target="_blank" rel="noopener">{preview}</a>
                    <div class="asset-info">
                        <div class="asset-icon"><i class="fa-solid fa-{icon}"></i></div>
                        <div class="asset-name-wrap">
                            <div class="file-name" title="{h(asset["name"])}">{h(asset["name"])}</div>
                            <div class="file-meta">{h(format_bytes(asset["size"]))} - {h(asset["modified"].strftime("%Y-%m-%d %H:%M"))}</div>
                        </div>
                        <button class="menu-button" type="button" onclick="toggleAssetMenu(event, this)" aria-label="Asset options"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                    </div>
                    <div class="asset-menu">
                        <a class="menu-link" href="{h(raw_url)}" target="_blank" rel="noopener"><i class="fa-solid fa-eye"></i> Open preview</a>
                        {rename}
                        {delete}
                    </div>
                </article>"""


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    is_admin, can_edit, can_delete = user_asset_permissions(user)
    ctx["is_admin"] = is_admin

    if request.args.get("raw"):
        try:
            path = safe_asset_path(request.args.get("raw"))
        except ValueError:
            abort(404)
        if not path.is_file():
            abort(404)
        kind = asset_kind(path, path.name)
        if kind not in {"image", "audio", "text"}:
            abort(415)
        return send_file(path, mimetype=asset_mime(kind, path.name), as_attachment=False, download_name=path.name)

    errors = []
    messages = []
    if request.method == "POST":
        action = request.form.get("action", "")
        if not can_edit:
            errors.append("You do not have permission to edit assets.")
        elif action == "upload":
            item = request.files.get("asset_file")
            if item is None or not item.filename:
                errors.append("Choose a file to upload.")
            else:
                name = clean_asset_name(item.filename)
                try:
                    target = safe_asset_path(name)
                    if target.exists():
                        errors.append("An asset with that name already exists.")
                    else:
                        item.save(target)
                        validation_error = validate_asset_file(target, name)
                        if validation_error:
                            target.unlink(missing_ok=True)
                            errors.append(validation_error)
                        else:
                            messages.append("Asset uploaded.")
                except Exception:
                    errors.append("Upload failed.")
        elif action == "rename":
            old_name = clean_asset_name(request.form.get("file"))
            new_name = clean_asset_name(request.form.get("new_name"))
            try:
                old_path = safe_asset_path(old_name)
                new_path = safe_asset_path(new_name)
                if not old_path.is_file() or not new_name:
                    errors.append("Rename failed because the asset path is invalid.")
                elif new_path.exists():
                    errors.append("Another asset already uses that name.")
                else:
                    validation_error = validate_asset_file(old_path, new_name)
                    if validation_error:
                        errors.append(validation_error)
                    else:
                        old_path.rename(new_path)
                        replace_message_references(old_name, new_name)
                        messages.append("Asset renamed.")
            except Exception as exc:
                errors.append(str(exc) or "Rename failed.")
        elif action == "delete":
            if not can_delete:
                errors.append("Only asset-edit in admin permissions can delete assets.")
            else:
                name = clean_asset_name(request.form.get("file"))
                try:
                    path = safe_asset_path(name)
                    if not path.is_file():
                        errors.append("Delete failed because the asset path is invalid.")
                    elif messages_using_asset(name):
                        errors.append("This asset must be removed from all messages that uses it before it can be deleted.")
                    else:
                        path.unlink()
                        messages.append("Asset deleted.")
                except Exception:
                    errors.append("Delete failed.")

    assets = []
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted([p for p in ASSET_DIR.iterdir() if p.is_file()], key=lambda p: p.name.lower()):
        assets.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime),
                "kind": asset_kind(path, path.name),
            }
        )

    alerts = "".join(f'<div class="success alert-msg">{h(message)}</div>' for message in messages)
    alerts += "".join(f'<div class="error alert-msg">{h(error)}</div>' for error in errors)
    upload_button = '<button class="button" type="button" onclick="openUploadModal()"><i class="fa-solid fa-plus"></i> Upload</button>' if can_edit else ""
    if assets:
        asset_grid = '<section class="asset-grid">' + "\n".join(render_asset_card(asset, can_edit, can_delete) for asset in assets) + "</section>"
    else:
        asset_grid = """<div class="empty-state">
            <i class="fa-solid fa-folder-open" style="font-size:2.2em;margin-bottom:12px;"></i>
            <div>No assets found.</div>
        </div>"""
    upload_modal = ""
    if can_edit:
        upload_modal = f"""
<div id="upload-modal" class="modal-backdrop" onclick="modalBackdropClick(event)">
    <div class="modal-card">
        <div class="modal-header">
            <h2>Upload asset</h2>
            <button class="modal-close" type="button" onclick="closeUploadModal()"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <form method="post" enctype="multipart/form-data">
            <div class="modal-body">
                <input type="hidden" name="action" value="upload">
                <input type="hidden" name="MAX_FILE_SIZE" value="{MAX_UPLOAD_BYTES}">
                <div class="upload-box">
                    <i class="fa-solid fa-cloud-arrow-up" style="font-size:2em;"></i>
                    <div style="margin-top:10px;">Choose an asset to upload</div>
                    <div class="muted" style="margin-top:6px;">Allowed: txt, jpg, png, bmp, wav, mp3. Limit: {h(format_bytes(MAX_UPLOAD_BYTES))}.</div>
                    <input class="control" type="file" name="asset_file" accept=".txt,.jpg,.png,.bmp,.wav,.mp3,text/plain,image/jpeg,image/png,image/bmp,audio/wav,audio/mpeg" required>
                </div>
            </div>
            <div class="modal-actions">
                <button class="button subtle" type="button" onclick="closeUploadModal()">Cancel</button>
                <button class="button" type="submit"><i class="fa-solid fa-upload"></i> Upload</button>
            </div>
        </form>
    </div>
</div>"""
    content = f"""    <div class="page-top">
        <div class="page-title">
            <h1>Assets</h1>
        </div>
        <div class="toolbar">
            {upload_button}
        </div>
    </div>

    {alerts}
    {asset_grid}
    {upload_modal}"""
    return legacy_page("Assets", ctx, "assets", ASSETS_STYLE, content, ASSETS_SCRIPT)
