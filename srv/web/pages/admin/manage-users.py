
from srv.web.app import *

USERS_STYLE = r"""
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
.header-actions h1 { margin:0; }
.card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,220px)); gap:12px; margin-bottom:18px; }
.summary-item { border:1px solid #EEE; border-radius:8px; padding:12px; background:#FFF; box-shadow:0 2px 4px rgba(0,0,0,0.08); }
.summary-item strong { display:block; font-size:1.4em; font-weight:500; }
.field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input, .field select { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; box-sizing:border-box; background:#FFF; }
.hint { color:#777; font-size:0.88em; margin-top:-8px; margin-bottom:12px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:4px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; justify-content:center; background:#FFF; border:1px solid #1976D2; border-radius:4px; cursor:pointer; font:inherit; }
.user-list { list-style:none; margin:0; padding:0; }
.user-item { display:flex; justify-content:space-between; gap:14px; padding:14px 0; border-bottom:1px solid #EEE; }
.user-item:last-child { border-bottom:none; }
.user-main { flex:1; min-width:0; }
.user-name-row { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.user-name { font-weight:500; color:#202124; overflow-wrap:anywhere; }
.user-meta { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.user-stats { color:#777; font-size:0.88em; margin-top:6px; display:flex; flex-wrap:wrap; gap:10px; }
.group-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.role-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#E3F2FD; color:#1565C0; font-size:0.8em; font-weight:500; }
.admin-badge { background:#FFF3E0; color:#E65100; }
.flash, .error { padding:12px; border-radius:8px; margin-bottom:16px; }
.flash.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; }
.flash.error, .error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; }
.muted { color:#777; font-size:0.9em; }
.editor-card { margin-top:18px; }
.form-actions { display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap; }
.inline-note { font-size:0.9em; color:#666; }
.subtabs { display:flex; gap:10px; margin:0 0 18px 0; border-bottom:1px solid #EEE; flex-wrap:wrap; }
.subtab-link { padding:10px 16px; border:1px solid transparent; border-bottom:none; border-radius:6px 6px 0 0; background:#F5F5F5; color:#555; text-decoration:none; }
.subtab-link.active { background:#1976D2; color:#FFF; border-color:#1976D2; }
.permission-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin-top:12px; }
.permission-card { border:1px solid #E5E7EB; border-radius:10px; padding:14px; background:#FAFBFD; }
.permission-card h3 { margin:0 0 10px 0; font-size:1em; font-weight:500; color:#1976D2; }
.permission-card p { margin:0 0 12px 0; color:#666; font-size:0.9em; }
.checkbox-stack { display:flex; flex-direction:column; gap:8px; max-height:280px; overflow:auto; padding-right:4px; }
.scope-box { border:1px solid #E5E7EB; border-radius:10px; padding:14px; background:#FFF; margin-top:14px; }
.scope-box h3 { margin:0 0 10px 0; font-size:1em; font-weight:500; color:#1976D2; }
.scope-toggle { margin-bottom:12px; }
.scope-list { display:grid; gap:8px; max-height:260px; overflow:auto; padding-right:4px; }
.scope-empty { color:#777; font-size:0.9em; }
.checkbox-row { display:flex; align-items:flex-start; gap:10px; color:#444; }
.checkbox-row input { margin-top:3px; }
.provider-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#F1F3F4; color:#444; font-size:0.78em; font-weight:500; }
.token-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
.token-list { display:grid; gap:12px; }
.token-item { border:1px solid #E5E7EB; border-radius:12px; padding:14px; background:#FAFBFD; }
.token-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; flex-wrap:wrap; }
.token-name { font-weight:500; color:#202124; }
.token-meta { display:flex; flex-wrap:wrap; gap:12px; color:#666; font-size:0.9em; }
.token-create-form { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
.token-modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:2200; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
.token-modal-backdrop.active { display:flex; }
.token-modal { width:min(520px, 100%); background:#FFF; border-radius:18px; box-shadow:0 24px 60px rgba(0,0,0,0.25); padding:22px; }
.token-modal h3 { margin:0 0 8px 0; font-weight:500; color:#1976D2; }
.token-modal p { margin:0 0 16px 0; color:#666; }
.token-display { display:flex; align-items:center; gap:10px; margin:16px 0; }
.token-display input { flex:1; border:1px solid #CCC; border-radius:8px; padding:12px; font:inherit; background:#F8FAFC; }
.token-actions { display:flex; justify-content:flex-end; gap:10px; flex-wrap:wrap; margin-top:18px; }
.sip-sensitive-modal-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1600;align-items:center;justify-content:center;padding:18px;box-sizing:border-box;}
.sip-sensitive-modal-backdrop.active{display:flex;}
.sip-sensitive-modal{max-width:390px;width:min(92vw,390px);}
.sip-sensitive-login-box{background:#fff;padding:30px;border-radius:6px;box-shadow:0 4px 6px rgba(0,0,0,0.1),0 1px 3px rgba(0,0,0,0.08);max-width:390px;width:min(92vw,390px);text-align:center;animation:fadeInPage 1.5s ease-in-out;}
.sip-sensitive-login-box h2{margin:0 0 20px 0;color:#1976D2;font-weight:500;font-size:1.5em;}
.sip-sensitive-input-field{position:relative;margin-bottom:20px;}
.sip-sensitive-input-field input{width:100%;padding:8px 0;border:none;border-bottom:2px solid #ccc;font-size:16px;background:transparent;outline:none;color:#333;font-family:"Roboto", sans-serif;box-sizing:border-box;}
.sip-sensitive-input-field input:focus{border-bottom:2px solid #1976d2;}
.sip-sensitive-input-field input[disabled]{color:#999;border-bottom:2px solid #ddd;}
.sip-sensitive-input-field label{position:absolute;top:8px;left:0;color:#888;font-size:14px;pointer-events:none;transition:0.2s ease all;}
.sip-sensitive-input-field input:focus ~ label,.sip-sensitive-input-field input:not(:placeholder-shown) ~ label{top:-16px;left:0;font-size:12px;color:#1976d2;}
.sip-sensitive-actions{display:flex;flex-direction:column;align-items:center;gap:10px;margin-top:10px;}
.sip-sensitive-actions button{width:100%;padding:12px;background-color:#1976d2;border:none;color:#fff;font-size:16px;border-radius:4px;cursor:pointer;font-family:"Roboto", sans-serif;text-transform:uppercase;height:45px;display:inline-flex;align-items:center;justify-content:center;}
.sip-sensitive-actions button:disabled{opacity:0.7;cursor:default;}
.sip-sensitive-cancel{color:#1976d2;text-decoration:none;font-size:14px;line-height:1.4;}
.sip-sensitive-cancel:hover{text-decoration:underline;}
.sip-sensitive-error{min-height:1.2em;color:#d32f2f;font-size:0.9em;margin-top:10px;}
@keyframes fadeInPage{from{opacity:0;}to{opacity:1;}}
.editor-stack{display:flex;flex-direction:column;gap:14px;}
.editor-section{border:1px solid #DADCE0;border-radius:14px;background:#F3F4F6;padding:16px;}
.editor-section.compact{padding:14px 16px;}
.editor-meta-card{display:flex;flex-direction:column;gap:10px;}
.editor-meta{display:flex;flex-wrap:wrap;gap:12px;color:#5F6368;font-size:0.9em;}
.editor-meta-row{display:flex;flex-wrap:wrap;gap:10px 12px;align-items:center;}
.section-stack{display:flex;flex-direction:column;gap:14px;}
.toggle-card{display:flex;flex-direction:column;gap:10px;padding:0;}
.toggle-input{max-width:280px;margin-left:52px;}
.switch-row{display:flex;align-items:center;justify-content:space-between;gap:16px;}
.switch-copy{display:flex;flex-direction:column;gap:2px;color:#3C4043;font-size:0.95em;}
.switch { position: relative; display: inline-block; width: 36px; height: 14px; flex:0 0 auto; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #C9CCD1; transition: .2s; border-radius: 14px; }
.slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: -2px; bottom: -3px; background-color: #FFF; transition: .2s; border-radius: 50%; box-shadow: 0 2px 4px rgba(0,0,0,0.18); }
.switch input:checked + .slider { background-color: #C2C6CC; }
.switch input:checked + .slider:before { transform: translateX(20px); background-color: #5F6368; }
.md-checkbox-container { display:flex; align-items:flex-start; position:relative; cursor:pointer; user-select:none; gap:12px; color:#3C4043; }
.md-checkbox-container input { position:absolute; opacity:0; cursor:pointer; height:0; width:0; }
.md-checkmark { position:relative; display:inline-block; flex:0 0 auto; height:20px; width:20px; background:#FFF; border:2px solid #80868B; border-radius:3px; transition:all 0.2s; }
.md-checkbox-container:hover input ~ .md-checkmark { border-color:#5F6368; }
.md-checkbox-container input:checked ~ .md-checkmark { background:#5F6368; border-color:#5F6368; }
.md-checkmark:after { content:""; position:absolute; display:none; left:6px; top:2px; width:4px; height:10px; border:solid #FFF; border-width:0 2px 2px 0; transform:rotate(45deg); }
.md-checkbox-container input:checked ~ .md-checkmark:after { display:block; }
.md-checkbox-text { flex:1 1 auto; min-width:0; }
.permission-panel{background:#EEEFF1;}
.permission-disclosure{border:none;background:transparent;}
.permission-disclosure summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;color:#5F6368;font-size:1.02em;font-weight:500;}
.permission-disclosure summary::-webkit-details-marker{display:none;}
.permission-disclosure summary i{transition:transform .2s ease;color:#5F6368;}
.permission-disclosure[open] summary i{transform:rotate(180deg);}
.permission-disclosure-body{margin-top:14px;}
.permission-stack{display:flex;flex-direction:column;gap:12px;}
.permission-block{border:1px solid #D8DEE6;border-radius:12px;background:#F8F9FA;padding:12px;}
.permission-block-title{font-size:0.82em;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#5F6368;margin-bottom:10px;}
.permission-choice-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;}
.permission-choice{display:flex;align-items:flex-start;gap:12px;padding:10px 12px;border:1px solid #DADCE0;border-radius:10px;background:#FFF;color:#3C4043;}
.scope-panel{display:grid;grid-template-columns:1fr;gap:12px;margin-top:12px;}
.scope-box{border:1px solid #D8DEE6;border-radius:12px;padding:12px;background:#F8F9FA;margin-top:0;}
.scope-toggle{display:flex;align-items:center;justify-content:space-between;gap:16px;}
.scope-list{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:4px;margin-top:12px;}
.scope-empty{color:#777;font-size:0.9em;}
.scope-choice{display:flex;align-items:flex-start;gap:12px;color:#3C4043;padding:8px 10px;border:1px solid #DADCE0;border-radius:10px;background:#FFF;}
.field-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:2px;}
.neutral-action{color:#5F6368;text-decoration:none;padding:10px 0;display:inline-flex;align-items:center;gap:8px;background:transparent;border:none;border-radius:8px;cursor:pointer;font:inherit;}
.neutral-action:hover{color:#3C4043;background:transparent;}
.neutral-action:focus-visible{outline:2px solid #C7C9CC;outline-offset:2px;}
.password-inline-wrap{position:relative;display:flex;align-items:center;width:100%;overflow:hidden;border:1px solid #CCC;border-radius:4px;background:#FFF;box-sizing:border-box;}
.password-inline-wrap:focus-within{border-color:#1976D2;}
.password-inline-wrap input{width:100%;padding-right:52px;flex:1 1 auto;border:none !important;border-radius:0;background:transparent !important;box-shadow:none;}
.password-blurred{color:transparent !important;text-shadow:0 0 24px rgba(60,64,67,1),0 0 18px rgba(60,64,67,1),0 0 12px rgba(60,64,67,0.98),0 0 8px rgba(60,64,67,0.98);caret-color:#202124;filter:blur(10px);-webkit-filter:blur(10px);letter-spacing:0.18em;font-weight:600;}
.password-peek-button{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:32px;height:32px;border:none;background:transparent;color:#5F6368;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:0;}
.password-peek-button:hover{background:#F1F3F4;color:#3C4043;}
.password-peek-button:focus-visible{outline:2px solid #C7C9CC;outline-offset:2px;}
.password-peek-button .icon-hide{display:none;}
.password-peek-button.revealed .icon-show{display:none;}
.password-peek-button.revealed .icon-hide{display:inline-block;}
@media(max-width:767px){ .header-actions{ align-items:flex-start; flex-direction:column; } .user-item{ align-items:flex-start; flex-direction:column; } .group-actions{ margin-top:4px; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card,.summary-item{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.field label,.muted,.hint,.inline-note,.user-meta,.user-stats{ color:#BBB; }
.field input,.field select { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; border-color:#BB86FC; background:#1E1E1E; }
.user-item { border-bottom:1px solid #333; }
.user-name { color:#EDEDED; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.role-badge { background:#2D2340; color:#D8C2FF; }
.admin-badge { background:#3A2B1B; color:#FFCC80; }
.flash.success { background:#12301A; border-color:#2E7D32; color:#C8E6C9; }
.flash.error,.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.subtabs { border-bottom-color:#333; }
.subtab-link { background:#2A2A2A; color:#BBB; }
.subtab-link.active { background:#BB86FC; color:#000; border-color:#BB86FC; }
.token-item { border-color:#333; background:#171A1F; }
.token-name { color:#EDEDED; }
.token-meta,.token-modal p { color:#BBB; }
.token-modal { background:#1E1E1E; }
.token-modal h3 { color:#BB86FC; }
.token-display input { background:#121212; border-color:#444; color:#E0E0E0; }
.sip-sensitive-login-box{background:#1E1E1E;box-shadow:0 4px 6px rgba(0,0,0,0.6);}
.sip-sensitive-login-box h2{color:#fff;}
.sip-sensitive-input-field input{color:#fff;border-bottom:2px solid #555;}
.sip-sensitive-input-field input[disabled]{color:#777;border-bottom:2px solid #444;}
.sip-sensitive-input-field label{color:#BBB;}
.sip-sensitive-actions button{background-color:#90caf9;color:#121212;}
.sip-sensitive-cancel{color:#90caf9;}
.sip-sensitive-error{color:#ffcdd2;}
.permission-card,.scope-box { border-color:#333; background:#171A1F; }
.permission-card h3,.scope-box h3 { color:#BB86FC; }
.permission-card p,.scope-empty,.checkbox-row { color:#BBB; }
.provider-badge { background:#2A2A2A; color:#E0E0E0; }
.editor-section,.permission-panel,.permission-block,.scope-box{border-color:#333;background:#1B1D20;}
.editor-meta,.permission-block-title,.scope-empty,.scope-choice,.switch-copy,.neutral-action{color:#BBB;}
.permission-disclosure summary,.permission-disclosure summary i{color:#E0E0E0;}
.permission-choice,.scope-choice{border-color:#3C4043;background:#202124;color:#E0E0E0;}
.toggle-input{color:#E0E0E0;}
.password-blurred{color:transparent !important;text-shadow:0 0 24px rgba(232,234,237,1),0 0 18px rgba(232,234,237,1),0 0 12px rgba(232,234,237,0.98),0 0 8px rgba(232,234,237,0.98);caret-color:#E0E0E0;filter:blur(10px);-webkit-filter:blur(10px);}
.password-inline-wrap{border-color:#444;background:#121212;}
.password-inline-wrap:focus-within{border-color:#BB86FC;}
.password-inline-wrap input{color:#E0E0E0;}
.password-peek-button{color:#E0E0E0;}
.password-peek-button:hover{background:#303030;color:#FFF;}
.slider{background-color:#4B4F55;}
.switch input:checked + .slider{background-color:#4B4F55;}
.switch input:checked + .slider:before{background-color:#E8EAED;}
.md-checkmark{border-color:#9AA0A6;background:#1E1E1E;}
.md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED;}
.md-checkbox-container input:checked ~ .md-checkmark{background:#E8EAED;border-color:#E8EAED;}
.md-checkmark:after{border-color:#1E1E1E;}
.neutral-action:hover{color:#EDEDED;background:transparent;}
}
"""

ROLE_OPTIONS = {
    "admin": "Administrator",
    "tempadmin": "Temporary Administrator",
    "user": "User",
    "tempuser": "Temporary User",
    "receiver": "Receiver",
    "tempreceiver": "Temporary Receiver",
}
VISIBLE_ROLE_OPTIONS = (
    ("admin", "Administrator"),
    ("user", "User"),
    ("receiver", "Receiver"),
)
SELF_DELETE_SESSION_USER_KEY = "manage_users_self_delete_user_id"
SELF_DELETE_SESSION_STAGE_KEY = "manage_users_self_delete_stage"
SELF_DELETE_SESSION_EXPIRES_KEY = "manage_users_self_delete_expires_at"
SELF_DELETE_STAGE_TTL_SECONDS = 300
GENERATED_PASSWORD_LENGTH = 32
GENERATED_PASSWORD_SYMBOLS = "!@#$%^&*()-_=+[]{}:,.?/|"


def is_admin_role(role):
    return role in {"admin", "tempadmin"}


def role_label(role):
    return ROLE_OPTIONS.get(role, str(role or "").capitalize())


def display_role_label(user_row):
    if is_fixed_admin_account(user_row):
        return "Root Administrator"
    return role_label((user_row or {}).get("role"))


def role_supports_user_permissions(role):
    return role_permission_mode(role) == "user"


def current_role_select_options(selected_role):
    options = []
    selected_token = str(selected_role or "").strip()
    visible_values = {value for value, _label in VISIBLE_ROLE_OPTIONS}
    if selected_token and selected_token not in visible_values and selected_token in ROLE_OPTIONS:
        options.append((selected_token, ROLE_OPTIONS[selected_token], True))
    options.extend((value, label, False) for value, label in VISIBLE_ROLE_OPTIONS)
    return options


def user_id_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_id_text(value):
    if isinstance(value, dict):
        value = value.get("id")
    if value is None:
        return ""
    return str(value).strip()


def is_fixed_admin_account(user_row):
    return user_id_int((user_row or {}).get("id")) == 0


def is_root_admin_user(user_row):
    return is_fixed_admin_account(user_row) and is_admin_role((user_row or {}).get("role"))


def can_manage_target_user(editor_user, target_user):
    target_id = user_id_int((target_user or {}).get("id"))
    editor_id = user_id_int((editor_user or {}).get("id"))
    if target_id is None:
        return False
    if is_root_admin_user(editor_user):
        return True
    if editor_id is not None and target_id == editor_id:
        return True
    if target_id == 0:
        return False
    return not is_admin_role((target_user or {}).get("role"))


def allow_user_self_delete():
    return truthy(os.getenv("ALLOW_USER_SELFDELETE", "0"))


def clear_self_delete_state():
    session.pop(SELF_DELETE_SESSION_USER_KEY, None)
    session.pop(SELF_DELETE_SESSION_STAGE_KEY, None)
    session.pop(SELF_DELETE_SESSION_EXPIRES_KEY, None)


def active_self_delete_stage(user_id):
    target_id = user_id_text(user_id)
    if not target_id:
        clear_self_delete_state()
        return ""
    stored_user_id = user_id_text(session.get(SELF_DELETE_SESSION_USER_KEY, ""))
    stored_stage = str(session.get(SELF_DELETE_SESSION_STAGE_KEY, "") or "").strip()
    try:
        expires_at = float(session.get(SELF_DELETE_SESSION_EXPIRES_KEY, "0") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if stored_user_id != target_id or not stored_stage or expires_at <= time.time():
        clear_self_delete_state()
        return ""
    return stored_stage


def set_self_delete_stage(user_id, stage):
    session[SELF_DELETE_SESSION_USER_KEY] = user_id_text(user_id)
    session[SELF_DELETE_SESSION_STAGE_KEY] = str(stage or "")
    session[SELF_DELETE_SESSION_EXPIRES_KEY] = str(time.time() + SELF_DELETE_STAGE_TTL_SECONDS)


def create_generated_password(length=GENERATED_PASSWORD_LENGTH):
    length = max(4, int(length or GENERATED_PASSWORD_LENGTH))
    alphabet = string.ascii_lowercase + string.ascii_uppercase + string.digits + GENERATED_PASSWORD_SYMBOLS
    required_sets = [
        string.ascii_lowercase,
        string.ascii_uppercase,
        string.digits,
        GENERATED_PASSWORD_SYMBOLS,
    ]
    characters = [secrets.choice(pool) for pool in required_sets]
    while len(characters) < length:
        characters.append(secrets.choice(alphabet))
    shuffled = []
    while characters:
        shuffled.append(characters.pop(secrets.randbelow(len(characters))))
    return "".join(shuffled)


def effective_editor_permission_tokens(user_row):
    tokens = set(user_permission_tokens(user_row or {}))
    raw_tokens = normalize_permission_tokens((user_row or {}).get("userperm"))
    if "messages-manage" in raw_tokens or "all" in raw_tokens:
        tokens.update({"messages-add", "messages-edit", "messages-delete"})
    return tokens


def format_date(value):
    if not value or str(value) in {"0000-00-00", "None"}:
        return "Never"
    if hasattr(value, "strftime"):
        return f"{value.strftime('%b')} {value.day}, {value.year}"
    return str(value)


def format_datetime(value):
    if not value or str(value) in {"0000-00-00 00:00:00", "None"}:
        return "Never"
    if hasattr(value, "strftime"):
        return f"{value.strftime('%b')} {value.day}, {value.year} {value.strftime('%I:%M %p').lstrip('0')}"
    return str(value)


def parse_local_account_expiration(value, date_only_end_of_day=True):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            clock = datetime.max.time().replace(microsecond=0) if date_only_end_of_day else datetime.min.time()
            return datetime.combine(value, clock)
        except Exception:
            pass
    text = str(value or "").strip()
    if text in {"0000-00-00", "0000-00-00 00:00:00", "None"}:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        parsed_date = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    if date_only_end_of_day:
        return parsed_date.replace(hour=23, minute=59, second=59)
    return parsed_date.replace(hour=0, minute=0, second=0)


def valid_date_string(value):
    if value == "":
        return True
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or "") is not None


def valid_datetime_local_string(value):
    if value == "":
        return True
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return True
    except ValueError:
        return False


def hash_password_value(password):
    salt = secrets.token_hex(16)
    return hashlib.sha256((password + salt).encode()).hexdigest(), salt


def admin_count():
    row = query_one("SELECT COUNT(*) AS c FROM users WHERE role IN ('admin', 'tempadmin')")
    return int(row.get("c") or 0)


def fetch_users():
    return query_all(
        """
        SELECT
            u.id, u.username, u.email, u.role, u.loginsleft, u.accountexpire, u.accountcreated,
            u.display_name, u.auth_provider, u.userperm, u.restrict_groups, u.restrict_messages, u.restrict_bell_schedules,
            u.require_password_change,
            COALESCE(ls.logincount, 0) AS logincount, ls.lastlogin
        FROM users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS logincount, MAX(login_time) AS lastlogin
            FROM loginhistory
            WHERE user_id IS NOT NULL
            GROUP BY user_id
        ) ls ON ls.user_id = u.id
        ORDER BY u.username ASC
        """
    )


def fetch_user(user_id):
    for item in fetch_users():
        if str(item.get("id")) == str(user_id):
            return item
    return None


def flash_message(message, category):
    session["manage_users_flash"] = {"message": message, "type": category}


def fetch_api_tokens(user_id):
    ensure_api_token_schema()
    return query_all(
        """
        SELECT id, token_label, expires_at, created_at, last_used_at
        FROM api_tokens
        WHERE user_id=%s
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    )


def delete_user_related_records(user_id):
    target_id = user_id_text(user_id)
    if not target_id:
        return
    revoke_all_user_session_records(target_id)
    execute("DELETE FROM api_tokens WHERE user_id=%s", (target_id,))
    execute("DELETE FROM user_group_access WHERE user_id=%s", (target_id,))
    execute("DELETE FROM user_message_access WHERE user_id=%s", (target_id,))
    execute("DELETE FROM user_bell_schedule_access WHERE user_id=%s", (target_id,))
    remove_desktop_member_from_all_groups(target_id)


def format_datetime_local_value(value):
    if not value or str(value) in {"0000-00-00", "0000-00-00 00:00:00", "None"}:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%dT%H:%M")
    parsed = parse_local_account_expiration(value, date_only_end_of_day=True)
    if parsed:
        return parsed.strftime("%Y-%m-%dT%H:%M")
    try:
        return datetime.strptime(str(value).split(".", 1)[0], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def format_account_expiration_display(value):
    parsed = parse_local_account_expiration(value, date_only_end_of_day=True)
    return format_datetime(parsed or value)


def fetch_group_options():
    ensure_identity_access_schema()
    return query_all("SELECT id, name FROM `groups` ORDER BY name ASC, id ASC")


def fetch_message_options():
    ensure_identity_access_schema()
    return query_all("SELECT messageid, name FROM messages ORDER BY name ASC, messageid ASC")


def fetch_bell_schedule_options():
    ensure_bell_schema()
    return query_all("SELECT id, name FROM bell_schedules ORDER BY name ASC, id ASC")


def permission_value_from_form(form):
    valid = {key for key, _label in USER_PERMISSION_LABELS}
    selected = []
    seen = set()
    for token in form.getlist("userperm[]"):
        normalized = str(token or "").strip().lower()
        if normalized in valid and normalized not in seen:
            seen.add(normalized)
            selected.append(normalized)
    return ",".join(selected)


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ensure_api_token_schema()
    ctx = legacy_user_context(user)
    api_enabled = str(ctx["settings"].get("api_http_enable", "0")) == "1"
    form_error = ""
    edit_user = None
    show_editor = False
    demo = demo_mode_enabled()
    editor_tab = "account"
    open_password_modal = request.args.get("open", "").strip() == "password"

    if request.method == "POST":
        action = request.form.get("action", "")
        requested_tab = request.form.get("editor_tab", "").strip()
        if requested_tab in {"account", "api-keys"}:
            editor_tab = requested_tab
        if demo and action in {"delete", "save", "create_api_token", "change_password", "self_delete_reauth", "self_delete_ack_warning", "self_delete_verify_username", "self_delete_finalize"}:
            if action.startswith("self_delete_"):
                return jsonify(status="error", message="Demo Mode is enabled."), 403
            return demo_mode_iframe_html("manage-users")
        if action in {"self_delete_reauth", "self_delete_ack_warning", "self_delete_verify_username", "self_delete_finalize"}:
            current_user_id = user_id_text(user)
            target_user_id = user_id_text(request.form.get("user_id"))
            target = fetch_user(target_user_id) if target_user_id else None
            if not allow_user_self_delete():
                clear_self_delete_state()
                return jsonify(status="error", message="User self-delete is disabled."), 403
            if not target or target_user_id != current_user_id:
                clear_self_delete_state()
                return jsonify(status="error", message="User not found."), 404
            if is_fixed_admin_account(target):
                clear_self_delete_state()
                return jsonify(status="error", message="The Root Administrator cannot delete this account."), 403
            if action == "self_delete_reauth":
                clear_self_delete_state()
                username = str(request.form.get("username") or "").strip()
                password = str(request.form.get("password") or "")
                if not username or not password:
                    return jsonify(status="error", message="Username and password are required."), 400
                result = authenticate_user_credentials(username, password)
                verified_user = result.get("user") if result.get("ok") else None
                if not verified_user or user_id_text(verified_user) != current_user_id:
                    return jsonify(status="error", message="Invalid username or password."), 401
                if is_admin_role(target.get("role")) and admin_count() <= 1:
                    return jsonify(status="error", message="At least one administrator must remain on the server."), 400
                set_self_delete_stage(current_user_id, "reauth")
                return jsonify(status="success")
            if action == "self_delete_ack_warning":
                if active_self_delete_stage(current_user_id) != "reauth":
                    clear_self_delete_state()
                    return jsonify(status="error", message="Please verify your account again before deleting it."), 400
                set_self_delete_stage(current_user_id, "warned")
                return jsonify(status="success")
            if action == "self_delete_verify_username":
                if active_self_delete_stage(current_user_id) != "warned":
                    clear_self_delete_state()
                    return jsonify(status="error", message="Please start the delete confirmation again."), 400
                confirmed_username = str(request.form.get("username_confirmation") or "")
                if confirmed_username != str(target.get("username") or ""):
                    clear_self_delete_state()
                    return jsonify(status="ignored")
                set_self_delete_stage(current_user_id, "confirmed")
                return jsonify(status="success")
            if active_self_delete_stage(current_user_id) != "confirmed":
                clear_self_delete_state()
                return jsonify(status="error", message="Please complete the delete confirmation again."), 400
            if is_admin_role(target.get("role")) and admin_count() <= 1:
                clear_self_delete_state()
                return jsonify(status="error", message="At least one administrator must remain on the server."), 400
            delete_user_related_records(current_user_id)
            execute("DELETE FROM users WHERE id=%s", (current_user_id,))
            clear_self_delete_state()
            session.clear()
            return jsonify(status="success", redirect="/index")
        if action == "delete":
            user_id = request.form.get("user_id", "")
            target = query_one("SELECT id, username, role FROM users WHERE id=%s LIMIT 1", (user_id,))
            if not target:
                flash_message("User not found.", "error")
            elif int(target.get("id") or 0) == 0:
                flash_message("User ID 0 cannot be deleted.", "error")
            elif str(target.get("id")) == str(user.get("id")):
                if allow_user_self_delete() and not is_fixed_admin_account(target):
                    flash_message("Please use the self-delete confirmation flow for your own account.", "error")
                else:
                    flash_message("You cannot delete the account you are currently signed in with.", "error")
            elif not can_manage_target_user(user, target):
                flash_message("You do not have permission to delete that user.", "error")
            elif is_admin_role(target.get("role")) and admin_count() <= 1:
                flash_message("At least one administrator must remain on the server.", "error")
            else:
                delete_user_related_records(user_id)
                execute("DELETE FROM users WHERE id=%s", (user_id,))
                flash_message("User deleted.", "success")
            return redirect("/admin/manage-users")

        if action == "create_api_token":
            editor_tab = "api-keys"
            user_id = request.form.get("user_id", "").strip()
            token_label = str(request.form.get("api_token_label") or "").strip()[:API_TOKEN_LABEL_LENGTH]
            expires_at = request.form.get("api_token_expires_at", "").strip()
            if not user_id:
                form_error = "User not found."
            elif not valid_datetime_local_string(expires_at):
                form_error = "API token expiration must use the local date and time picker format."
            else:
                target = fetch_user(user_id)
                if not target:
                    form_error = "User not found."
                elif str(user_id) == str(user.get("id")):
                    abort(403)
                elif not can_manage_target_user(user, target):
                    abort(403)
                else:
                    try:
                        token_value = create_api_token_value()
                        expires_value = None
                        if expires_at:
                            expires_value = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
                        execute(
                            "INSERT INTO api_tokens (user_id, token_hash, token_label, expires_at) VALUES (%s,%s,%s,%s)",
                            (user_id, hash_api_token_value(token_value), token_label or None, expires_value),
                        )
                        session["manage_users_new_api_token"] = {"value": token_value, "label": token_label}
                        flash_message("API key created.", "success")
                        return redirect(f"/admin/manage-users?edit={user_id}&tab=api-keys")
                    except RuntimeError as exc:
                        form_error = str(exc)

        if action == "change_password":
            editor_tab = "account"
            user_id = request.form.get("user_id", "").strip()
            new_password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            target = fetch_user(user_id) if user_id else None
            edit_user = target or {"id": user_id}
            show_editor = True
            if not target:
                form_error = "User not found."
            elif str(target.get("auth_provider") or "local").strip().lower() != "local":
                form_error = "This user must change their password through the configured identity provider."
            elif str(user_id) == str(user.get("id")):
                return redirect("/user/settings?open=password")
            elif not can_manage_target_user(user, target):
                abort(403)
            elif not new_password:
                form_error = "New password is required."
            elif new_password != confirm:
                form_error = "Password confirmation does not match."
            else:
                password_hash, salt = hash_password_value(new_password)
                execute("UPDATE users SET password=%s, salt=%s WHERE id=%s", (password_hash, salt, user_id))
                flash_message("Password updated.", "success")
                return redirect(f"/admin/manage-users?edit={user_id}")

        if action == "save":
            editor_tab = "account"
            user_id_raw = request.form.get("user_id", "").strip()
            user_id = user_id_raw or None
            existing = fetch_user(user_id) if user_id else None
            editing_self = bool(existing and str(user_id) == str(user.get("id")))
            fixed_admin = bool(existing and is_fixed_admin_account(existing))
            basic_identity_only = bool(existing and (editing_self or fixed_admin))
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            requested_role = request.form.get("role", "").strip()
            password = request.form.get("password", "")
            if basic_identity_only:
                role = "admin" if fixed_admin else str(existing.get("role") or requested_role or "receiver")
                expire = str(existing.get("accountexpire") or "").strip()
                try:
                    logins_left = max(0, int(existing.get("loginsleft") or 0))
                except (TypeError, ValueError):
                    logins_left = 0
                account_expiration_enabled = bool(expire)
                limited_logins_enabled = logins_left > 0
                userperm_value = str(existing.get("userperm") or "")
                restrict_groups = "1" if str(existing.get("restrict_groups") or "0") == "1" else "0"
                restrict_messages = "1" if str(existing.get("restrict_messages") or "0") == "1" else "0"
                restrict_bell_schedules = "1" if str(existing.get("restrict_bell_schedules") or "0") == "1" else "0"
                allowed_group_ids = list(fetch_user_group_access_ids(user_id))
                allowed_message_ids = list(fetch_user_message_access_ids(user_id))
                allowed_bell_schedule_ids = list(fetch_user_bell_schedule_access_ids(user_id))
                if fixed_admin and editing_self:
                    require_password_change = "1" if request.form.get("require_password_change") else "0"
                else:
                    require_password_change = "1" if truthy(existing.get("require_password_change", "0")) else "0"
            else:
                role = requested_role
                account_expiration_enabled = bool(request.form.get("account_expiration_enabled"))
                limited_logins_enabled = bool(request.form.get("limited_logins_enabled"))
                expire = request.form.get("accountexpire", "").strip() if account_expiration_enabled else ""
                try:
                    logins_left = max(0, int(request.form.get("loginsleft") or 0)) if limited_logins_enabled else 0
                except ValueError:
                    logins_left = 0
                role_mode = role_permission_mode(role)
                userperm_value = permission_value_from_form(request.form) if role_mode == "user" else ""
                restrict_groups = "1" if role_mode == "user" and request.form.get("restrict_groups") else "0"
                restrict_messages = "1" if role_mode == "user" and request.form.get("restrict_messages") else "0"
                restrict_bell_schedules = "1" if role_mode == "user" and request.form.get("restrict_bell_schedules") else "0"
                valid_group_ids = {str(row.get("id") or "").strip() for row in fetch_group_options() if str(row.get("id") or "").strip()}
                valid_message_ids = {str(row.get("messageid") or "").strip() for row in fetch_message_options() if str(row.get("messageid") or "").strip()}
                valid_schedule_ids = {str(row.get("id") or "").strip() for row in fetch_bell_schedule_options() if str(row.get("id") or "").strip()}
                allowed_group_ids = [token for token in request.form.getlist("allowed_groups[]") if str(token or "").strip() in valid_group_ids] if role_mode == "user" else []
                allowed_message_ids = [token for token in request.form.getlist("allowed_messages[]") if str(token or "").strip() in valid_message_ids] if role_mode == "user" else []
                allowed_bell_schedule_ids = [token for token in request.form.getlist("allowed_bell_schedules[]") if str(token or "").strip() in valid_schedule_ids] if role_mode == "user" else []
                require_password_change = "1" if request.form.get("require_password_change") else "0"
            edit_user = {
                "id": user_id or "",
                "username": username,
                "email": email,
                "role": role,
                "accountexpire": expire,
                "loginsleft": logins_left,
                "account_expiration_enabled": "1" if account_expiration_enabled else "0",
                "limited_logins_enabled": "1" if limited_logins_enabled else "0",
                "userperm": userperm_value,
                "restrict_groups": restrict_groups,
                "restrict_messages": restrict_messages,
                "restrict_bell_schedules": restrict_bell_schedules,
                "allowed_groups": allowed_group_ids,
                "allowed_messages": allowed_message_ids,
                "allowed_bell_schedules": allowed_bell_schedule_ids,
                "require_password_change": require_password_change,
                "password": password,
            }
            show_editor = True
            if not username:
                form_error = "Username is required."
            elif role not in ROLE_OPTIONS:
                form_error = "Please choose a valid role."
            elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                form_error = "Email must be blank or a valid address."
            elif account_expiration_enabled and not expire:
                form_error = "Account expiration date is required."
            elif expire and not valid_datetime_local_string(expire):
                form_error = "Account expiration must use the local date and time picker format."
            elif limited_logins_enabled and logins_left <= 0:
                form_error = "Limited logins must be at least 1."
            elif user_id is None and not password:
                form_error = "Password is required when creating a user."
            elif user_id and not existing:
                form_error = "User not found."
            elif user_id and existing and not can_manage_target_user(user, existing):
                form_error = "You do not have permission to edit that user."
            elif is_admin_role(role) and not is_root_admin_user(user) and not (existing and str(existing.get("id")) == str(user.get("id"))):
                form_error = "Only the Root Administrator can create or modify other administrator accounts."
            elif user_id and str(user_id) == str(user.get("id")) and not is_admin_role(role):
                form_error = "You cannot remove admin access from the account you are currently using."
            elif user_id and existing and is_admin_role(existing.get("role")) and not is_admin_role(role) and admin_count() <= 1:
                form_error = "At least one administrator must remain on the server."
            if not form_error:
                email_value = email or None
                expire_value = datetime.strptime(expire, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S") if expire else None
                try:
                    if user_id is None:
                        password_hash, salt = hash_password_value(password)
                        new_user_id = execute(
                            """
                            INSERT INTO users (
                                username, email, password, salt, role, loginsleft, accountexpire,
                                auth_provider, userperm, restrict_groups, restrict_messages, restrict_bell_schedules, require_password_change
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'local',%s,%s,%s,%s,%s)
                            """,
                            (username, email_value, password_hash, salt, role, logins_left, expire_value, userperm_value, restrict_groups, restrict_messages, restrict_bell_schedules, require_password_change),
                        )
                        set_user_group_access_ids(new_user_id, allowed_group_ids)
                        set_user_message_access_ids(new_user_id, allowed_message_ids)
                        set_user_bell_schedule_access_ids(new_user_id, allowed_bell_schedule_ids)
                        flash_message("User created.", "success")
                    else:
                        execute(
                            """
                            UPDATE users
                            SET username=%s, email=%s, role=%s, loginsleft=%s, accountexpire=%s,
                                userperm=%s, restrict_groups=%s, restrict_messages=%s, restrict_bell_schedules=%s, require_password_change=%s
                            WHERE id=%s
                            """,
                            (username, email_value, role, logins_left, expire_value, userperm_value, restrict_groups, restrict_messages, restrict_bell_schedules, require_password_change, user_id),
                        )
                        set_user_group_access_ids(user_id, allowed_group_ids)
                        set_user_message_access_ids(user_id, allowed_message_ids)
                        set_user_bell_schedule_access_ids(user_id, allowed_bell_schedule_ids)
                        if editing_self:
                            session["username"] = username
                            session["auth_provider"] = str((existing or {}).get("auth_provider") or "local")
                        flash_message("User updated.", "success")
                    return redirect("/admin/manage-users")
                except Exception:
                    form_error = "That username or email address is already in use."

    users = fetch_users()
    admin_users = sum(1 for row in users if is_admin_role(row.get("role")))
    if not show_editor:
        if request.args.get("edit"):
            edit_user = fetch_user(request.args.get("edit"))
            if edit_user and not can_manage_target_user(user, edit_user):
                abort(403)
            show_editor = bool(edit_user)
        elif "new" in request.args:
            edit_user = {
                "id": "",
                "username": "",
                "email": "",
                "role": "receiver",
                "loginsleft": 0,
                "accountexpire": "",
                "accountcreated": datetime.now(),
                "userperm": "",
                "restrict_groups": "0",
                "restrict_messages": "0",
                "restrict_bell_schedules": "0",
                "require_password_change": "1",
                "allowed_groups": [],
                "allowed_messages": [],
                "allowed_bell_schedules": [],
                "password": create_generated_password(),
            }
            show_editor = True
    query_tab = request.args.get("tab", "").strip()
    if query_tab in {"account", "api-keys"}:
        editor_tab = query_tab
    if show_editor and isinstance(edit_user, dict):
        existing_user_id = user_id_text(edit_user)
        if existing_user_id:
            persisted_user = fetch_user(existing_user_id) or {}
            for key in ("accountcreated", "lastlogin", "logincount", "auth_provider", "display_name", "require_password_change"):
                edit_user.setdefault(key, persisted_user.get(key))
            edit_user["allowed_groups"] = list(fetch_user_group_access_ids(existing_user_id))
            edit_user["allowed_messages"] = list(fetch_user_message_access_ids(existing_user_id))
            edit_user["allowed_bell_schedules"] = list(fetch_user_bell_schedule_access_ids(existing_user_id))
        else:
            edit_user.setdefault("allowed_groups", [])
            edit_user.setdefault("allowed_messages", [])
            edit_user.setdefault("allowed_bell_schedules", [])
            edit_user.setdefault("password", create_generated_password())
        edit_user.setdefault("userperm", "")
        edit_user.setdefault("restrict_groups", "0")
        edit_user.setdefault("restrict_messages", "0")
        edit_user.setdefault("restrict_bell_schedules", "0")
        edit_user.setdefault("require_password_change", "0")
        edit_user.setdefault("account_expiration_enabled", "1" if str(edit_user.get("accountexpire") or "").strip() else "0")
        try:
            limited_logins_enabled = int(edit_user.get("loginsleft") or 0) > 0
        except (TypeError, ValueError):
            limited_logins_enabled = False
        edit_user.setdefault("limited_logins_enabled", "1" if limited_logins_enabled else "0")
    group_options = fetch_group_options() if show_editor else []
    message_options = fetch_message_options() if show_editor else []
    bell_schedule_options = fetch_bell_schedule_options() if show_editor else []

    flash = session.pop("manage_users_flash", None)
    new_api_token = session.pop("manage_users_new_api_token", {})
    flash_html = f'<div class="flash {h(flash.get("type"))}">{h(flash.get("message"))}</div>' if isinstance(flash, dict) else ""
    error_html = f'<div class="error">{h(form_error)}</div>' if form_error else ""
    if show_editor:
        if demo:
            return demo_mode_iframe_html("manage-users")
        existing_user_id = user_id_text(edit_user)
        current_user_id = user_id_text(user)
        editing_self = existing_user_id == current_user_id
        fixed_admin = is_fixed_admin_account(edit_user)
        basic_identity_only = bool(existing_user_id and (editing_self or fixed_admin))
        allow_api_keys = bool(existing_user_id and api_enabled and not basic_identity_only)
        if not allow_api_keys:
            editor_tab = "account"
        role_options = "".join(
            f'<option value="{h(value)}"{" selected" if (edit_user or {}).get("role") == value else ""}{" hidden" if hidden else ""}>{h(label)}</option>'
            for value, label, hidden in current_role_select_options((edit_user or {}).get("role"))
        )
        password_required = " required" if not existing_user_id else ""
        selected_permission_tokens = effective_editor_permission_tokens(edit_user or {"role": "receiver"})
        if not existing_user_id and not str((edit_user or {}).get("userperm") or "").strip():
            selected_permission_tokens = set(DEFAULT_USER_PAGE_PERMISSIONS.get("user", set()))
        permission_label_map = dict(USER_PERMISSION_LABELS)
        permission_sections = [
            ("Features", ["paging", "messages", "history", "bells", "assets"]),
            ("Message Administration", ["messages-add", "messages-edit", "messages-delete"]),
            ("Management", ["asset-edit", "groups-manage", "broadcasts-manage"]),
        ]
        permission_rows = "".join(
            '<div class="permission-block"><div class="permission-block-title">'
            + h(title)
            + '</div><div class="permission-choice-grid">'
            + "".join(
                f"""<label class="permission-choice md-checkbox-container">
                    <input type="checkbox" name="userperm[]" value="{h(value)}"{" checked" if ("all" in selected_permission_tokens or value in selected_permission_tokens) else ""}>
                    <span class="md-checkmark"></span>
                    <span class="md-checkbox-text">{h(permission_label_map.get(value) or value)}</span>
                </label>"""
                for value in values
            )
            + "</div></div>"
            for title, values in permission_sections
        )
        selected_group_access_ids = {str(token) for token in (edit_user or {}).get("allowed_groups") or []}
        selected_message_access_ids = {str(token) for token in (edit_user or {}).get("allowed_messages") or []}
        selected_bell_schedule_access_ids = {str(token) for token in (edit_user or {}).get("allowed_bell_schedules") or []}
        group_rows_html = "".join(
            f"""<label class="scope-choice md-checkbox-container">
                <input type="checkbox" name="allowed_groups[]" value="{h(row.get('id'))}"{" checked" if str(row.get('id')) in selected_group_access_ids else ""}>
                <span class="md-checkmark"></span>
                <span class="md-checkbox-text">{h(row.get('name') or row.get('id'))}</span>
            </label>"""
            for row in group_options
        ) or '<div class="scope-empty">No groups yet.</div>'
        message_rows_html = "".join(
            f"""<label class="scope-choice md-checkbox-container">
                <input type="checkbox" name="allowed_messages[]" value="{h(row.get('messageid'))}"{" checked" if str(row.get('messageid')) in selected_message_access_ids else ""}>
                <span class="md-checkmark"></span>
                <span class="md-checkbox-text">{h(row.get('name') or row.get('messageid'))}</span>
            </label>"""
            for row in message_options
        ) or '<div class="scope-empty">No messages yet.</div>'
        bell_schedule_rows_html = "".join(
            f"""<label class="scope-choice md-checkbox-container">
                <input type="checkbox" name="allowed_bell_schedules[]" value="{h(row.get('id'))}"{" checked" if str(row.get('id')) in selected_bell_schedule_access_ids else ""}>
                <span class="md-checkmark"></span>
                <span class="md-checkbox-text">{h(row.get('name') or row.get('id'))}</span>
            </label>"""
            for row in bell_schedule_options
        ) or '<div class="scope-empty">No bell schedules yet.</div>'
        provider_note = ""
        if existing_user_id:
            provider_label = {
                "ldap": "LDAP",
                "oidc": "OIDC",
                "saml": "SAML",
            }.get(str((edit_user or {}).get("auth_provider") or "local").strip().lower(), "Local")
            display_name = str((edit_user or {}).get("display_name") or "").strip()
            display_line = f'<span>Display name: {h(display_name)}</span>' if display_name else ""
            provider_note = f"""<section class="editor-section compact editor-meta-card">
                <div class="editor-meta-row">
                    <span>Created: {h(format_date((edit_user or {}).get("accountcreated")))}</span>
                    <span>Last login: {h(format_datetime((edit_user or {}).get("lastlogin")))}</span>
                    <span>Login count: {h((edit_user or {}).get("logincount") or 0)}</span>
                </div>
                <div class="editor-meta-row">
                    <span class="provider-badge">{h(provider_label)}</span>
                    {display_line}
                </div>
            </section>"""
        account_expiration_checked = str((edit_user or {}).get("account_expiration_enabled") or "0") == "1"
        limited_logins_checked = str((edit_user or {}).get("limited_logins_enabled") or "0") == "1"
        require_password_change_checked = str((edit_user or {}).get("require_password_change") or "0") == "1"
        create_password_value = str((edit_user or {}).get("password") or "")
        auth_provider_value = str((edit_user or {}).get("auth_provider") or "local").strip().lower()
        synced_user = bool(existing_user_id and auth_provider_value in {"ldap", "oidc", "saml"})
        password_controls = ""
        password_modal = ""
        if existing_user_id and not synced_user:
            if editing_self:
                password_controls = '<a class="neutral-action" href="/user/settings?open=password"><i class="fa-solid fa-key"></i> Change Password</a>'
            else:
                password_controls = '<button class="neutral-action" type="button" onclick="openUserPasswordModal()"><i class="fa-solid fa-key"></i> Change Password</button>'
                password_modal = f"""
    <div id="userPasswordModal" class="token-modal-backdrop">
        <div class="token-modal">
            <h3>Change Password</h3>
            <form method="POST" action="/admin/manage-users">
                <input type="hidden" name="action" value="change_password">
                <input type="hidden" name="user_id" value="{h(existing_user_id)}">
                <input type="hidden" name="editor_tab" value="account">
                <div class="field">
                    <label for="change_password_new">New Password</label>
                    <div class="password-inline-wrap">
                        <input id="change_password_new" name="new_password" type="password" required>
                        <button class="password-peek-button" type="button" id="toggleChangePasswordNewButton" aria-label="Press and hold to reveal password" title="Press and hold to reveal password">
                            <i class="fa-solid fa-eye icon-show" aria-hidden="true"></i>
                            <i class="fa-solid fa-eye-slash icon-hide" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
                <div class="field">
                    <label for="change_password_confirm">Confirm New Password</label>
                    <div class="password-inline-wrap">
                        <input id="change_password_confirm" name="confirm_password" type="password" required>
                        <button class="password-peek-button" type="button" id="toggleChangePasswordConfirmButton" aria-label="Press and hold to reveal password" title="Press and hold to reveal password">
                            <i class="fa-solid fa-eye icon-show" aria-hidden="true"></i>
                            <i class="fa-solid fa-eye-slash icon-hide" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
                <div class="token-actions">
                    <button class="btn-secondary" type="button" onclick="closeUserPasswordModal()">Close</button>
                    <button class="btn-primary" type="submit"><i class="fa-solid fa-key"></i> Update Password</button>
                </div>
            </form>
        </div>
    </div>"""
        tabs_html = ""
        if allow_api_keys:
            edit_id = h(existing_user_id)
            account_class = "subtab-link active" if editor_tab == "account" else "subtab-link"
            api_class = "subtab-link active" if editor_tab == "api-keys" else "subtab-link"
            tabs_html = f"""<div class="subtabs">
                <a class="{account_class}" href="/admin/manage-users?edit={edit_id}&tab=account">Account</a>
                <a class="{api_class}" href="/admin/manage-users?edit={edit_id}&tab=api-keys">API Keys</a>
            </div>"""
        api_token_panel = ""
        if allow_api_keys:
            token_rows = fetch_api_tokens((edit_user or {}).get("id"))
            create_modal = f"""
    <div id="apiKeyCreateModal" class="token-modal-backdrop">
        <div class="token-modal">
            <h3>Create API Key</h3>
            <p>Create a one-time key for this user. You can add an optional label and expiration date.</p>
            <form method="POST" action="/admin/manage-users">
                <input type="hidden" name="action" value="create_api_token">
                <input type="hidden" name="user_id" value="{h(existing_user_id)}">
                <input type="hidden" name="editor_tab" value="api-keys">
                <div class="token-create-form">
                    <div class="field">
                        <label for="api_token_label">Label</label>
                        <input id="api_token_label" name="api_token_label" type="text" maxlength="{API_TOKEN_LABEL_LENGTH}" placeholder="Optional">
                    </div>
                    <div class="field">
                        <label for="api_token_expires_at">Expiration</label>
                        <input id="api_token_expires_at" name="api_token_expires_at" type="datetime-local">
                    </div>
                </div>
                <div class="token-actions">
                    <button class="btn-secondary" type="button" onclick="closeApiKeyCreateModal()">Close</button>
                    <button class="btn-primary" type="submit"><i class="fa-solid fa-key"></i> Create</button>
                </div>
            </form>
        </div>
    </div>"""
            reveal_modal = ""
            if isinstance(new_api_token, dict) and new_api_token.get("value"):
                reveal_modal = f"""
    <div id="apiKeyRevealModal" class="token-modal-backdrop active">
        <div class="token-modal">
            <h3>API Key Created</h3>
            <p>You will not be able to retrieve this key again.</p>
            <div class="token-display">
                <input id="new-api-key-value" type="password" value="{h(new_api_token.get("value"))}" readonly>
                <button class="btn-secondary" type="button" onclick="toggleNewApiKeyVisibility()">View</button>
            </div>
            <div class="token-actions">
                <button class="btn-primary" type="button" onclick="copyNewApiKey()">Copy</button>
                <button class="btn-secondary" type="button" onclick="closeApiKeyRevealModal()">Close</button>
            </div>
        </div>
    </div>"""
            token_items = "".join(
                f"""<div class="token-item">
                    <div class="token-head">
                        <div>
                            <div class="token-name">{h(row.get("token_label") or "Untitled key")}</div>
                        </div>
                    </div>
                    <div class="token-meta">
                        <span>Created: {h(format_datetime(row.get("created_at")))}</span>
                        <span>Last used: {h(format_datetime(row.get("last_used_at")))}</span>
                        <span>Expires: {h(format_datetime(row.get("expires_at")))}</span>
                    </div>
                </div>"""
                for row in token_rows
            ) or '<div class="muted">No API keys yet.</div>'
            api_token_panel = f"""
    <div class="card editor-card">
        <h2>API Keys</h2>
        <div class="token-toolbar">
            <button class="btn-primary" type="button" onclick="openApiKeyCreateModal()"><i class="fa-solid fa-plus"></i> Create</button>
        </div>
        <div class="token-list">{token_items}</div>
    </div>
    {create_modal}
    {reveal_modal}"""
        new_password_fields = ""
        if not existing_user_id:
            new_password_fields = f"""
        <section class="editor-section">
            <div class="field">
                <label for="password">Password</label>
                <div class="password-inline-wrap">
                    <input id="password" name="password" type="text" class="password-blurred" value="{h(create_password_value)}"{password_required}>
                    <button class="password-peek-button" type="button" id="toggleCreatePasswordButton" data-peek-mode="blur" aria-label="Press and hold to reveal password" title="Press and hold to reveal password">
                        <i class="fa-solid fa-eye icon-show" aria-hidden="true"></i>
                        <i class="fa-solid fa-eye-slash icon-hide" aria-hidden="true"></i>
                    </button>
                </div>
            </div>
        </section>"""
        role_field_html = ""
        if not basic_identity_only:
            role_field_html = f'<div class="field"><label for="role">Role</label><select id="role" name="role" required>{role_options}</select></div>'
        account_settings_section = ""
        if not basic_identity_only:
            password_action_html = f'<div class="field-actions">{password_controls}</div>' if password_controls else ""
            require_password_change_html = ""
            if not synced_user:
                require_password_change_html = f"""
                <div class="toggle-card">
                    <div class="switch-row">
                        <div class="switch-copy">Require password change at next login</div>
                        <label class="switch"><input type="checkbox" id="require-password-change-toggle" name="require_password_change" value="1"{" checked" if require_password_change_checked else ""}><span class="slider"></span></label>
                    </div>
                </div>"""
            account_settings_section = f"""
        <section class="editor-section">
            <div class="section-stack">
                <div class="toggle-card">
                    <div class="switch-row">
                        <div class="switch-copy">Account Expiration</div>
                        <label class="switch"><input type="checkbox" id="account-expiration-toggle" name="account_expiration_enabled" value="1"{" checked" if account_expiration_checked else ""}><span class="slider"></span></label>
                    </div>
                    <div id="account-expiration-field" class="field toggle-input">
                        <label for="accountexpire">Expiration</label>
                        <input id="accountexpire" name="accountexpire" type="datetime-local" value="{h(format_datetime_local_value((edit_user or {}).get('accountexpire')))}">
                    </div>
                </div>
                <div class="toggle-card">
                    <div class="switch-row">
                        <div class="switch-copy">Limited Logins</div>
                        <label class="switch"><input type="checkbox" id="limited-logins-toggle" name="limited_logins_enabled" value="1"{" checked" if limited_logins_checked else ""}><span class="slider"></span></label>
                    </div>
                    <div id="limited-logins-field" class="field toggle-input">
                        <label for="loginsleft">Logins Left</label>
                        <input id="loginsleft" name="loginsleft" type="number" min="1" value="{h((edit_user or {}).get("loginsleft") or 1)}">
                    </div>
                </div>
                {require_password_change_html}
                {password_action_html}
            </div>
        </section>"""
        elif fixed_admin and editing_self:
            account_settings_section = f"""
        <section class="editor-section compact">
            <div class="section-stack">
                <div class="toggle-card">
                    <div class="switch-row">
                        <div class="switch-copy">Require password change at next login</div>
                        <label class="switch"><input type="checkbox" id="require-password-change-toggle" name="require_password_change" value="1"{" checked" if require_password_change_checked else ""}><span class="slider"></span></label>
                    </div>
                </div>
                <div class="field-actions">{password_controls}</div>
            </div>
        </section>"""
        elif password_controls:
            account_settings_section = f"""
        <section class="editor-section compact">
            <div class="field-actions">{password_controls}</div>
        </section>"""
        permissions_panel_visible = role_supports_user_permissions((edit_user or {}).get("role")) and not basic_identity_only
        permissions_panel_html = ""
        if not basic_identity_only:
            permissions_panel_html = f"""
        <section id="user-permissions-panel" class="editor-section permission-panel"{'' if permissions_panel_visible else ' style="display:none;"'}>
            <details id="user-permissions-details" class="permission-disclosure">
                <summary><span>User Permissions</span><i class="fa-solid fa-chevron-down"></i></summary>
                <div class="permission-disclosure-body">
                    <div class="permission-stack">{permission_rows}</div>
                    <div class="scope-panel">
                        <div class="scope-box">
                            <div class="scope-toggle">
                                <span>Restrict Groups</span>
                                <label class="switch"><input type="checkbox" id="restrict-groups-toggle" name="restrict_groups" value="1"{" checked" if str((edit_user or {}).get("restrict_groups") or "0") == "1" else ""}><span class="slider"></span></label>
                            </div>
                            <div id="restrict-groups-list" class="scope-list">{group_rows_html}</div>
                        </div>
                        <div class="scope-box">
                            <div class="scope-toggle">
                                <span>Restrict Messages</span>
                                <label class="switch"><input type="checkbox" id="restrict-messages-toggle" name="restrict_messages" value="1"{" checked" if str((edit_user or {}).get("restrict_messages") or "0") == "1" else ""}><span class="slider"></span></label>
                            </div>
                            <div id="restrict-messages-list" class="scope-list">{message_rows_html}</div>
                        </div>
                        <div class="scope-box">
                            <div class="scope-toggle">
                                <span>Restrict Bell Schedules</span>
                                <label class="switch"><input type="checkbox" id="restrict-bell-schedules-toggle" name="restrict_bell_schedules" value="1"{" checked" if str((edit_user or {}).get("restrict_bell_schedules") or "0") == "1" else ""}><span class="slider"></span></label>
                            </div>
                            <div id="restrict-bell-schedules-list" class="scope-list">{bell_schedule_rows_html}</div>
                        </div>
                    </div>
                </div>
            </details>
        </section>"""
        account_panel = f"""<form class="card editor-card editor-stack" method="POST" action="/admin/manage-users">
        <h2>{"Edit User" if existing_user_id else "New User"}</h2>
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="user_id" value="{h(existing_user_id)}">
        <input type="hidden" name="editor_tab" value="account">
        {provider_note}
        <section class="editor-section">
            <div class="field-grid">
                <div class="field"><label for="username">Username</label><input id="username" name="username" value="{h((edit_user or {}).get("username") or "")}" required></div>
                <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" value="{h((edit_user or {}).get("email") or "")}" placeholder="Optional"></div>
                {role_field_html}
            </div>
        </section>
        {account_settings_section}
        {new_password_fields}
        {permissions_panel_html}
        <div class="form-actions">
            <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save User</button>
            <a class="btn-secondary" href="/admin/manage-users">Cancel</a>
        </div>
    </form>
    {password_modal}"""
        auto_open_password_script = "openUserPasswordModal();" if open_password_modal and bool(password_modal) else ""
        modal_script = """
    <script>
    function openApiKeyCreateModal() {
      const modal = document.getElementById('apiKeyCreateModal');
      if (modal) modal.classList.add('active');
    }
    function closeApiKeyCreateModal() {
      const modal = document.getElementById('apiKeyCreateModal');
      if (modal) modal.classList.remove('active');
    }
    function closeApiKeyRevealModal() {
      const modal = document.getElementById('apiKeyRevealModal');
      if (modal) modal.classList.remove('active');
    }
    function openUserPasswordModal() {
      const modal = document.getElementById('userPasswordModal');
      if (modal) modal.classList.add('active');
    }
    function closeUserPasswordModal() {
      const modal = document.getElementById('userPasswordModal');
      if (modal) modal.classList.remove('active');
    }
    function setPasswordPeekState(button, input, revealed) {
      if (!button || !input) return;
      const mode = button.getAttribute('data-peek-mode') || 'mask';
      if (mode === 'blur') {
        input.type = 'text';
        input.classList.toggle('password-blurred', !revealed);
      } else {
        input.type = revealed ? 'text' : 'password';
      }
      button.classList.toggle('revealed', revealed);
      button.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    }
    function bindPasswordPeek(buttonId, inputId) {
      const button = document.getElementById(buttonId);
      const input = document.getElementById(inputId);
      if (!button || !input) return;
      const reveal = function(event) {
        if (event) event.preventDefault();
        setPasswordPeekState(button, input, true);
      };
      const hide = function() {
        setPasswordPeekState(button, input, false);
      };
      button.addEventListener('pointerdown', reveal);
      button.addEventListener('pointerup', hide);
      button.addEventListener('pointerleave', hide);
      button.addEventListener('pointercancel', hide);
      button.addEventListener('blur', hide);
      button.addEventListener('keydown', function(event) {
        if (event.key === ' ' || event.key === 'Enter') {
          reveal(event);
        }
      });
      button.addEventListener('keyup', hide);
      button.addEventListener('contextmenu', function(event) {
        event.preventDefault();
      });
      if (input.form) {
        input.form.addEventListener('submit', hide);
      }
      setPasswordPeekState(button, input, false);
    }
    function syncRestrictionPanel(toggleId, listId) {
      const toggle = document.getElementById(toggleId);
      const list = document.getElementById(listId);
      if (!toggle || !list) return;
      list.style.display = toggle.checked ? 'grid' : 'none';
    }
    function syncToggleField(toggleId, fieldId) {
      const toggle = document.getElementById(toggleId);
      const field = document.getElementById(fieldId);
      if (!toggle || !field) return;
      field.style.display = toggle.checked ? 'flex' : 'none';
    }
    function syncRolePanels(expandOnUser) {
      const role = document.getElementById('role');
      const panel = document.getElementById('user-permissions-panel');
      const disclosure = document.getElementById('user-permissions-details');
      if (!role || !panel) return;
      const value = String(role.value || '').toLowerCase();
      const isUser = value === 'user' || value === 'tempuser';
      panel.style.display = isUser ? 'block' : 'none';
      if (!disclosure) return;
      if (!isUser) {
        disclosure.open = false;
      } else if (expandOnUser) {
        disclosure.open = true;
      }
    }
    function toggleNewApiKeyVisibility() {
      const input = document.getElementById('new-api-key-value');
      if (!input) return;
      input.type = input.type === 'password' ? 'text' : 'password';
    }
    async function copyNewApiKey() {
      const input = document.getElementById('new-api-key-value');
      if (!input) return;
      try {
        await navigator.clipboard.writeText(input.value);
      } catch (_error) {
        input.type = 'text';
        input.select();
        document.execCommand('copy');
        input.type = 'password';
      }
    }
    document.addEventListener('click', function(event) {
      if (event.target && event.target.classList && event.target.classList.contains('token-modal-backdrop')) {
        event.target.classList.remove('active');
      }
    });
    document.addEventListener('DOMContentLoaded', function() {
      const groupToggle = document.getElementById('restrict-groups-toggle');
      const messageToggle = document.getElementById('restrict-messages-toggle');
      const bellScheduleToggle = document.getElementById('restrict-bell-schedules-toggle');
      const roleSelect = document.getElementById('role');
      const accountExpirationToggle = document.getElementById('account-expiration-toggle');
      const limitedLoginsToggle = document.getElementById('limited-logins-toggle');
      if (groupToggle) {
        groupToggle.addEventListener('change', function() { syncRestrictionPanel('restrict-groups-toggle', 'restrict-groups-list'); });
      }
      if (messageToggle) {
        messageToggle.addEventListener('change', function() { syncRestrictionPanel('restrict-messages-toggle', 'restrict-messages-list'); });
      }
      if (bellScheduleToggle) {
        bellScheduleToggle.addEventListener('change', function() { syncRestrictionPanel('restrict-bell-schedules-toggle', 'restrict-bell-schedules-list'); });
      }
      if (roleSelect) {
        roleSelect.addEventListener('change', function() { syncRolePanels(true); });
      }
      if (accountExpirationToggle) {
        accountExpirationToggle.addEventListener('change', function() { syncToggleField('account-expiration-toggle', 'account-expiration-field'); });
      }
      if (limitedLoginsToggle) {
        limitedLoginsToggle.addEventListener('change', function() { syncToggleField('limited-logins-toggle', 'limited-logins-field'); });
      }
      syncRestrictionPanel('restrict-groups-toggle', 'restrict-groups-list');
      syncRestrictionPanel('restrict-messages-toggle', 'restrict-messages-list');
      syncRestrictionPanel('restrict-bell-schedules-toggle', 'restrict-bell-schedules-list');
      syncToggleField('account-expiration-toggle', 'account-expiration-field');
      syncToggleField('limited-logins-toggle', 'limited-logins-field');
      syncRolePanels(false);
      bindPasswordPeek('toggleCreatePasswordButton', 'password');
      bindPasswordPeek('toggleChangePasswordNewButton', 'change_password_new');
      bindPasswordPeek('toggleChangePasswordConfirmButton', 'change_password_confirm');
      __AUTO_OPEN_PASSWORD__
    });
    </script>"""
        modal_script = modal_script.replace("__AUTO_OPEN_PASSWORD__", auto_open_password_script)
        content = f"""    <div class="header-actions">
        <h1>{"Edit User" if existing_user_id else "New User"}</h1>
        <a class="btn-secondary" href="/admin/manage-users"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>
    {flash_html}{error_html}
    {tabs_html}
    {account_panel if editor_tab == "account" else ""}
    {api_token_panel if editor_tab == "api-keys" and api_enabled else ""}"""
        content += modal_script
    else:
        self_delete_enabled = allow_user_self_delete()
        current_user_is_root_admin = is_root_admin_user(user)
        current_user_id = user_id_text(user)
        current_username = str(user.get("username") or "").strip()
        self_delete_modal = ""
        self_delete_script = ""
        if self_delete_enabled and not is_fixed_admin_account(user):
            self_delete_modal = f"""
    <div id="selfDeleteModal" class="token-modal-backdrop sip-sensitive-modal-backdrop" aria-hidden="true">
        <div class="sip-sensitive-modal">
            <div class="sip-sensitive-login-box">
            <h2>Please verify your identity to make sensitive changes</h2>
            <form id="selfDeleteForm" onsubmit="submitSelfDeleteReauth(event)">
                <input type="hidden" id="selfDeleteUserId" value="{h(current_user_id)}">
                <div class="sip-sensitive-input-field">
                    <input id="selfDeleteUsername" type="text" value="{h(current_username)}" autocomplete="username" placeholder=" " disabled>
                    <label for="selfDeleteUsername">Username</label>
                </div>
                <div class="sip-sensitive-input-field">
                    <input id="selfDeletePassword" type="password" autocomplete="current-password" placeholder=" " required>
                    <label for="selfDeletePassword">Password</label>
                </div>
                <div id="selfDeleteError" class="sip-sensitive-error"></div>
                <div class="sip-sensitive-actions">
                    <button type="submit" id="confirmSelfDeleteModal">LOGIN</button>
                    <a href="#" class="sip-sensitive-cancel" id="closeSelfDeleteModal">Cancel</a>
                </div>
            </form>
            </div>
        </div>
    </div>"""
            self_delete_script = """
    <script>
    function openSelfDeleteModal() {
      const modal = document.getElementById('selfDeleteModal');
      const error = document.getElementById('selfDeleteError');
      const password = document.getElementById('selfDeletePassword');
      if (error) {
        error.textContent = '';
      }
      if (password) password.value = '';
      if (modal) {
        modal.classList.add('active');
        modal.setAttribute('aria-hidden', 'false');
      }
    }
    function closeSelfDeleteModal() {
      const modal = document.getElementById('selfDeleteModal');
      if (modal) {
        modal.classList.remove('active');
        modal.setAttribute('aria-hidden', 'true');
      }
    }
    async function selfDeletePost(params) {
      const response = await fetch('/admin/manage-users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: new URLSearchParams(params)
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok || payload.status === 'error') {
        throw new Error(payload.message || 'Request failed.');
      }
      return payload;
    }
    async function submitSelfDeleteReauth(event) {
      event.preventDefault();
      const modal = document.getElementById('selfDeleteModal');
      const confirmButton = document.getElementById('confirmSelfDeleteModal');
      const userId = document.getElementById('selfDeleteUserId');
      const username = document.getElementById('selfDeleteUsername');
      const password = document.getElementById('selfDeletePassword');
      const error = document.getElementById('selfDeleteError');
      if (error) {
        error.textContent = '';
      }
      if (confirmButton) confirmButton.disabled = true;
      closeSelfDeleteModal();
      try {
        await selfDeletePost({
          action: 'self_delete_reauth',
          user_id: userId ? userId.value : '',
          username: username ? username.value : '',
          password: password ? password.value : ''
        });
        const firstWarning = 'WARNING! YOU ARE ABOUT TO DELETE YOUR OWN USER ACCOUNT OFF OF THIS SERVER!!! YOU WILL BE IMMEDIATELY LOGGED OUT AND WILL LOOSE ACCESS TO THIS SYSTEM!!!!';
        if (!window.confirm(firstWarning)) return;
        await selfDeletePost({
          action: 'self_delete_ack_warning',
          user_id: userId ? userId.value : ''
        });
        const usernameConfirmation = window.prompt('Please type your username, the account you are deleting, to confirm.', '');
        if (usernameConfirmation === null) return;
        const usernameResult = await selfDeletePost({
          action: 'self_delete_verify_username',
          user_id: userId ? userId.value : '',
          username_confirmation: usernameConfirmation
        });
        if (usernameResult.status !== 'success') return;
        const finalWarning = 'THIS IS YOUR FINAL WARNING! IF YOU CLICK OK, YOU WILL BE LOGGED OFF AND WILL NOT BE ABLE TO LOGIN USING THIS ACCOUNT AGAIN!';
        if (!window.confirm(finalWarning)) return;
        const finalResult = await selfDeletePost({
          action: 'self_delete_finalize',
          user_id: userId ? userId.value : ''
        });
        if (finalResult.redirect) {
          window.location.href = finalResult.redirect;
        } else {
          window.location.reload();
        }
      } catch (errorMessage) {
        if (modal) {
          modal.classList.add('active');
          modal.setAttribute('aria-hidden', 'false');
        }
        if (error) {
          error.textContent = errorMessage && errorMessage.message ? errorMessage.message : 'Unable to confirm your account.';
        }
      } finally {
        if (confirmButton) confirmButton.disabled = false;
      }
    }
    document.addEventListener('DOMContentLoaded', function() {
      const closeLink = document.getElementById('closeSelfDeleteModal');
      if (closeLink) {
        closeLink.addEventListener('click', function(event) {
          event.preventDefault();
          closeSelfDeleteModal();
        });
      }
    });
    document.addEventListener('click', function(event) {
      if (event.target && event.target.classList && event.target.classList.contains('token-modal-backdrop')) {
        event.target.classList.remove('active');
        event.target.setAttribute('aria-hidden', 'true');
      }
    });
    </script>"""
        user_items = []
        for row in users:
            role = row.get("role") or ""
            badge = "role-badge admin-badge" if is_admin_role(role) else "role-badge"
            email = row.get("email") or "No email address"
            provider_label = {
                "ldap": "LDAP",
                "oidc": "OIDC",
                "saml": "SAML",
            }.get(str(row.get("auth_provider") or "local").strip().lower(), "Local")
            display_name = str(row.get("display_name") or "").strip()
            display_name_html = f'<div class="user-meta">{h(display_name)}</div>' if display_name else ""
            row_id = user_id_text(row)
            is_self_row = row_id == current_user_id
            can_manage_row = can_manage_target_user(user, row)
            row_is_synced = str(row.get("auth_provider") or "local").strip().lower() in {"ldap", "oidc", "saml"}
            show_self_delete_button = is_self_row and (current_user_is_root_admin or self_delete_enabled)
            can_self_delete = self_delete_enabled and is_self_row and not is_fixed_admin_account(row)
            can_show_root_self_delete_button = is_self_row and current_user_is_root_admin and is_fixed_admin_account(row)
            can_delete = bool(
                row_id
                and not is_self_row
                and can_manage_row
                and int(row.get("id") or 0) != 0
                and not (is_admin_role(role) and admin_users <= 1)
            )
            delete_form = ""
            delete_onsubmit = "openDemoModePopup('manage-users'); return false;" if demo else "return confirm('Delete this user?')"
            if can_self_delete:
                if demo:
                    delete_form = '<button class="icon-action delete" type="button" onclick="openDemoModePopup(\'manage-users\')" title="Delete"><i class="fa-solid fa-trash"></i></button>'
                else:
                    delete_form = '<button class="icon-action delete" type="button" onclick="openSelfDeleteModal()" title="Delete"><i class="fa-solid fa-trash"></i></button>'
            elif can_show_root_self_delete_button and show_self_delete_button:
                delete_form = f"""<form method="POST" action="/admin/manage-users" onsubmit="{delete_onsubmit}">
                                        <input type="hidden" name="action" value="delete">
                                        <input type="hidden" name="user_id" value="{h(row.get("id"))}">
                                        <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                    </form>"""
            elif can_delete:
                delete_form = f"""<form method="POST" action="/admin/manage-users" onsubmit="{delete_onsubmit}">
                                        <input type="hidden" name="action" value="delete">
                                        <input type="hidden" name="user_id" value="{h(row.get("id"))}">
                                        <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                    </form>"""
            edit_href = "javascript:openDemoModePopup('manage-users')" if demo else f"/admin/manage-users?edit={h(row.get('id'))}"
            edit_link = f'<a class="icon-action" href="{edit_href}" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>' if can_manage_row else ""
            if demo:
                change_password_href = "javascript:openDemoModePopup('manage-users')"
            else:
                change_password_href = "/user/settings?open=password" if is_self_row else edit_href + "&open=password"
            change_password_button = ""
            if (is_self_row or can_manage_row) and not row_is_synced:
                change_password_button = f'<a class="icon-action" href="{change_password_href}" title="Change Password"><i class="fa-solid fa-key"></i></a>'
            user_items.append(
                f"""<li class="user-item">
                            <div class="user-main">
                                <div class="user-name-row">
                                    <div class="user-name">{h(row.get("username"))}</div>
                                    <span class="{badge}">{h(display_role_label(row))}</span>
                                    <span class="provider-badge">{h(provider_label)}</span>
                                </div>
                                {display_name_html}
                                <div class="user-meta">{h(email)}</div>
                                <div class="user-stats">
                                    <span>Created: {h(format_date(row.get("accountcreated")))}</span>
                                    <span>Last login: {h(format_datetime(row.get("lastlogin")))}</span>
                                    <span>Uses left: {h(row.get("loginsleft") or 0)}</span>
                                    <span>Login count: {h(row.get("logincount") or 0)}</span>
                                    <span>Expires: {h(format_account_expiration_display(row.get("accountexpire")))}</span>
                                </div>
                            </div>
                            <div class="group-actions">
                                {edit_link}
                                {change_password_button}
                                {delete_form}
                            </div>
                        </li>"""
            )
        new_href = "javascript:openDemoModePopup('manage-users')" if demo else "/admin/manage-users?new=1"
        content = f"""    <div class="header-actions">
        <h1>Manage Users</h1>
        <a class="btn-primary" href="{new_href}"><i class="fa-solid fa-plus"></i> New User</a>
    </div>
    {flash_html}{error_html}
    <div class="summary-grid">
        <div class="summary-item"><strong>{h(len(users))}</strong><span class="muted">Users</span></div>
        <div class="summary-item"><strong>{h(admin_users)}</strong><span class="muted">Administrators</span></div>
    </div>
    <div class="card">
        <h2>Users</h2>
        {'<ul class="user-list">' + ''.join(user_items) + '</ul>' if user_items else '<p class="muted">No users found.</p>'}
    </div>
    {self_delete_modal}
    {self_delete_script}"""
    return legacy_page("Manage Users", ctx, "users", USERS_STYLE, content)
