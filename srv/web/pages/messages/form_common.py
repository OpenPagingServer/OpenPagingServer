import inspect
from pathlib import Path
from urllib.parse import urlencode

from broadcasts import (
    message_expiration_state,
    parse_vendor_specific,
    safe_module_key,
    serialize_message_expiration,
    serialize_vendor_specific,
)
from srv.web.app import ASSET_DIR, asset_filename, asset_path, h


MESSAGE_ICON_MAX_DIMENSION = 1080


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
.md-radio-group{display:flex;flex-direction:column;gap:10px;}
.md-radio-option{display:flex;align-items:center;gap:12px;cursor:pointer;font-weight:400;color:#202124;user-select:none;}
.md-radio-option input{position:absolute;opacity:0;pointer-events:none;}
.md-radio-indicator{width:20px;height:20px;border:2px solid #5f6368;border-radius:50%;position:relative;box-sizing:border-box;flex:0 0 auto;transition:border-color 0.2s, box-shadow 0.2s;}
.md-radio-indicator:after{content:"";position:absolute;top:50%;left:50%;width:10px;height:10px;border-radius:50%;background:#1976D2;transform:translate(-50%,-50%) scale(0);transition:transform 0.2s;}
.md-radio-option input:checked + .md-radio-indicator{border-color:#1976D2;}
.md-radio-option input:checked + .md-radio-indicator:after{transform:translate(-50%,-50%) scale(1);}
.md-radio-option:hover .md-radio-indicator{border-color:#202124;}
.md-radio-text{display:flex;align-items:center;min-width:0;}
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
.vendor-specific-card { border:1px solid #E0E0E0; border-radius:8px; background:#FAFAFA; padding:14px; }
.vendor-specific-card h2 { font-size:1.15em; font-weight:500; margin:0 0 12px; }
.vendor-module { border:1px solid #E6E6E6; border-radius:6px; background:#FFF; margin-bottom:10px; overflow:hidden; }
.vendor-module:last-child { margin-bottom:0; }
.vendor-module summary { cursor:pointer; padding:12px 14px; font-weight:500; list-style:none; display:flex; align-items:center; justify-content:space-between; gap:12px; }
.vendor-module summary::-webkit-details-marker { display:none; }
.vendor-module summary:after { content:"+"; color:#777; font-weight:700; }
.vendor-module[open] summary:after { content:"-"; }
.vendor-module-body { border-top:1px solid #EEE; padding:14px; }
.message-variable-wrap { position: relative; }
.message-variable-wrap .form-control { padding-right: 42px; }
.message-variable-badge { position: absolute; top: 12px; right: 10px; width: 24px; height: 24px; border: none; border-radius: 0; background: transparent; color: rgba(25, 118, 210, 0.78); font-size: 0.95em; font-weight: 400; font-family: "Times New Roman", serif; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0; }
.message-variable-badge:hover { background: transparent; color: rgba(25, 118, 210, 1); }
.message-variable-wrap-short .message-variable-badge { top: 50%; right: 10px; transform: translateY(-50%); font-weight: 700; }
.message-variable-wrap-long .message-variable-badge { top: auto; bottom: 10px; right: 10px; transform: none; font-weight: 700; }
.message-variable-modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 1300; }
.message-variable-modal-backdrop.open { display: block; }
.message-variable-modal { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(760px, calc(100vw - 32px)); max-height: calc(100vh - 40px); overflow-y: auto; background: #FFF; border-radius: 14px; box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28); z-index: 1400; font-family: "Tahoma", sans-serif; }
.message-variable-modal.open { display: block; }
.message-variable-modal-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid #EEE; }
.message-variable-modal-header h2 { margin: 0; font-size: 1.2em; font-weight: 500; }
.message-variable-modal-header-actions { display: flex; align-items: center; gap: 8px; }
.message-variable-modal-close, .message-variable-modal-back { border: none; background: transparent; color: #666; font-size: 1.4em; cursor: pointer; line-height: 1; padding: 4px 6px; }
.message-variable-modal-back { display: none; font-size: 0.95em; font-weight: 600; }
.message-variable-modal-back.visible { display: inline-flex; align-items: center; }
.message-variable-modal-body { padding: 20px; }
.message-variable-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.message-variable-choice { width: 100%; border: 1px solid #DDE6F1; border-radius: 12px; background: #F8FBFF; color: #0F3F77; padding: 18px 16px; text-align: left; font-size: 1em; font-weight: 600; cursor: pointer; font-family: "Tahoma", sans-serif; }
.message-variable-choice:hover { background: #EDF5FF; border-color: #BCD2EC; }
.message-variable-wizard { display: none; }
.message-variable-wizard.open { display: block; }
.message-variable-row { margin-bottom: 16px; }
.message-variable-row:last-child { margin-bottom: 0; }
.message-variable-row label { display: block; margin-bottom: 6px; font-weight: 500; }
.message-variable-row .hint { font-size: 0.9em; color: #666; margin-top: 6px; }
.message-variable-option-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.message-variable-option { width: 100%; border: 1px solid #DDE6F1; border-radius: 12px; background: #FAFCFF; color: #1F2937; padding: 14px; text-align: left; cursor: pointer; font-family: "Tahoma", sans-serif; }
.message-variable-option:hover { background: #F0F7FF; border-color: #BCD2EC; }
.message-variable-option strong { display: block; font-size: 1em; font-weight: 600; margin-bottom: 4px; color: #0F3F77; font-family: "Tahoma", sans-serif; }
.message-variable-option span { display: block; font-size: 0.92em; color: #5B6470; font-family: "Tahoma", sans-serif; }
.message-variable-preview { display: block; margin-top: 8px; font-family: "Consolas", monospace; font-size: 0.88em; color: #334155; }
.message-variable-actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
.message-variable-secondary { background: #F3F4F6; color: #374151; border: 1px solid #D1D5DB; border-radius: 8px; padding: 9px 12px; cursor: pointer; }
.message-variable-secondary:hover { background: #E5E7EB; }
.message-variable-primary { background: #1976D2; color: #FFF; border: none; border-radius: 8px; padding: 9px 14px; cursor: pointer; }
.message-variable-primary:hover { background: #1565C0; }
.message-variable-status { display: none; margin-top: 12px; font-size: 0.92em; color: #4B5563; }
.message-variable-status.open { display: block; }
.message-variable-test-result { display: none; margin-top: 14px; padding: 12px; border: 1px solid #DDE6F1; border-radius: 10px; background: #FAFCFF; color: #1F2937; white-space: pre-wrap; word-break: break-word; font-family: "Consolas", monospace; font-size: 0.9em; max-height: 220px; overflow-y: auto; }
.message-variable-test-result.open { display: block; }
.message-variable-modal button,
.message-variable-modal input,
.message-variable-modal textarea,
.message-variable-modal select {
    font-family: "Tahoma", sans-serif;
}
.md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:500;color:#555;user-select:none;width:100%;padding:5px 0;gap:12px;}
.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0;}
.md-checkmark{position:relative;display:inline-block;height:20px;width:20px;background:#fff;border:2px solid #5f6368;border-radius:2px;transition:all 0.2s;flex:0 0 auto;}
.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124;}
.md-checkbox-container input:checked ~ .md-checkmark{background:#1976D2;border-color:#1976D2;}
.md-checkmark:after{content:"";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid white;border-width:0 2px 2px 0;transform:rotate(45deg);}
.md-checkbox-container input:checked ~ .md-checkmark:after{display:block;}
.md-checkbox-container input:disabled ~ .md-checkmark{border-color:#dadce0;background:#f1f3f4;cursor:not-allowed;}
.md-checkbox-container input:disabled ~ .message-expiration-text{color:#9aa0a6;cursor:not-allowed;}
.message-expiration-list{display:flex;flex-direction:column;gap:8px;}
.message-expiration-text{display:flex;flex-direction:column;gap:2px;min-width:0;}
.message-expiration-title{font-weight:500;color:#202124;}
.message-expiration-note{font-size:0.88em;font-weight:400;color:#6b7280;}
.message-expiration-detail{margin:4px 0 0 32px;}
.message-expiration-inline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.message-expiration-inline input[type="number"]{width:110px;}
.message-expiration-panel{margin:8px 0 0 32px;padding:12px;border:1px solid #E0E0E0;border-radius:8px;background:#FAFAFA;max-height:260px;overflow-y:auto;}
.message-expiration-panel-disabled{opacity:0.6;}
.message-expiration-message-list{display:flex;flex-direction:column;gap:8px;}
.message-expiration-message-list .md-checkbox-container{padding:2px 0;}
.message-expiration-message-meta{font-size:0.82em;font-weight:400;color:#6b7280;}
.message-expiration-any-locked .md-checkmark{background:#d7dde3;border-color:#b0bec5;}
.message-expiration-any-locked .md-checkmark:after{display:block;border-color:#5f6368;}
.message-expiration-any-locked .message-expiration-text{color:#9aa0a6;}
.message-icon-selection{display:flex;flex-direction:column;align-items:flex-start;gap:16px;}
.message-icon-summary{display:flex;align-items:center;gap:14px;min-width:0;width:100%;flex:0 0 auto;}
.message-icon-preview{width:72px;height:72px;border-radius:14px;border:1px solid #E0E0E0;background:#F8FAFD;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#5F6368;font-size:1.4em;flex:0 0 auto;}
.message-icon-preview img{width:100%;height:100%;object-fit:cover;}
.message-icon-text{min-width:0;}
.message-icon-name{font-weight:500;font-size:1em;word-break:break-word;}
.message-icon-meta{font-size:0.9em;color:#666;margin-top:4px;}
.message-icon-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.message-icon-clear{border:1px solid #DADCE0;background:#FFF;color:#374151;border-radius:8px;padding:9px 12px;cursor:pointer;font:inherit;}
.message-icon-clear:hover{background:#F9FAFB;}
.message-icon-clear:disabled{opacity:0.6;cursor:not-allowed;}
.message-icon-note{display:none;margin-top:12px;padding:12px 14px;border:1px solid #FFE08A;border-radius:10px;background:#FFF8E1;color:#8A5A00;}
.message-icon-note.open{display:block;}
.message-icon-picker-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1450;}
.message-icon-picker-backdrop.open{display:block;}
.message-icon-picker-modal{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(980px,calc(100vw - 32px));max-height:calc(100vh - 40px);overflow:hidden;background:#FFF;border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,0.28);z-index:1500;}
.message-icon-picker-modal.open{display:flex;flex-direction:column;}
.message-icon-picker-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid #EEE;}
.message-icon-picker-header h2{margin:0;font-size:1.2em;font-weight:500;}
.message-icon-picker-close{border:none;background:transparent;color:#666;font-size:1.4em;cursor:pointer;line-height:1;padding:4px 6px;}
.message-icon-picker-close:hover{color:#111;}
.message-icon-picker-body{padding:18px 20px 20px;overflow-y:auto;}
.message-icon-picker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;}
.message-icon-asset-card{border:1px solid #DADCE0;border-radius:16px;background:#FFF;padding:0;cursor:pointer;text-align:left;color:inherit;overflow:hidden;box-shadow:0 1px 2px rgba(60,64,67,.08);}
.message-icon-asset-card:hover{border-color:#B6C8E1;box-shadow:0 4px 10px rgba(60,64,67,.12);}
.message-icon-asset-card.selected{border-color:#1976D2;box-shadow:0 0 0 2px rgba(25,118,210,0.18);}
.message-icon-asset-card.unsupported{opacity:0.48;}
.message-icon-asset-card.unsupported:hover{border-color:#DADCE0;box-shadow:0 1px 2px rgba(60,64,67,.08);}
.message-icon-asset-preview{height:132px;background:#F1F3F4;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#5F6368;font-size:2em;}
.message-icon-asset-preview img{width:100%;height:100%;object-fit:cover;}
.message-icon-asset-info{padding:12px 14px 14px;}
.message-icon-asset-name{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.message-icon-asset-meta{font-size:0.84em;color:#666;margin-top:4px;}
.message-icon-asset-status{font-size:0.83em;margin-top:8px;color:#1976D2;}
.message-icon-asset-card.unsupported .message-icon-asset-status{color:#A50E0E;}
.message-icon-picker-empty{padding:28px;border:1px dashed #DADCE0;border-radius:16px;text-align:center;color:#5F6368;background:#F8FAFD;}
@media(max-width:767px){
    .message-icon-selection{align-items:flex-start;}
    .message-icon-summary{flex-basis:100%;}
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
    .info-card{ border:1px solid #333; background-color:#1E1E1E; }
    .form-control { background: #333; border: 1px solid #444; color: #FFF; }
    .md-radio-option{color:#E0E0E0;}
    .md-radio-indicator{border-color:#9AA0A6;}
    .md-radio-option:hover .md-radio-indicator{border-color:#E8EAED;}
    .md-radio-option input:checked + .md-radio-indicator{border-color:#8AB4F8;}
    .md-radio-indicator:after{background:#8AB4F8;}
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
    .vendor-specific-card { background:#202020; border-color:#333; }
    .vendor-module { background:#252525; border-color:#3A3A3A; }
    .vendor-module-body { border-top-color:#333; }
    .vendor-module summary:after { color:#AAA; }
    .message-variable-badge { background: transparent; color: rgba(138, 180, 248, 0.82); }
    .message-variable-badge:hover { background: transparent; color: rgba(138, 180, 248, 1); }
    .message-variable-modal { background: #1E1E1E; }
    .message-variable-modal-header { border-bottom-color: #333; }
    .message-variable-modal-close, .message-variable-modal-back { color: #AAA; }
    .message-variable-choice { background: #252525; border-color: #3A3A3A; color: #E5E7EB; }
    .message-variable-choice:hover { background: #2E2E2E; border-color: #4A4A4A; }
    .message-variable-row .hint { color: #AAA; }
    .message-variable-option { background: #252525; border-color: #3A3A3A; color: #E5E7EB; }
    .message-variable-option:hover { background: #2E2E2E; border-color: #4A4A4A; }
    .message-variable-option strong { color: #EAF2FF; }
    .message-variable-option span { color: #BFC6CF; }
    .message-variable-preview { color: #AFC7E8; }
    .message-variable-secondary { background: #303030; border-color: #444; color: #E5E7EB; }
    .message-variable-secondary:hover { background: #3A3A3A; }
    .message-variable-primary { background: #8AB4F8; color: #121212; }
    .message-variable-primary:hover { background: #9CC0FA; }
    .message-variable-status { color: #C7C7C7; }
    .message-variable-test-result { background: #1A1A1A; border-color: #333; color: #E0E0E0; }
    .md-checkbox-container{color:#BBB;}
    .md-checkmark{border-color:#9AA0A6;background:#1E1E1E;}
    .md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED;}
    .md-checkbox-container input:checked ~ .md-checkmark{background:#8AB4F8;border-color:#8AB4F8;}
    .md-checkmark:after{border-color:#1E1E1E;}
    .md-checkbox-container input:disabled ~ .md-checkmark{border-color:#5F6368;background:#3C4043;}
    .message-expiration-title{color:#E5E7EB;}
    .message-expiration-note,.message-expiration-message-meta{color:#9E9E9E;}
    .message-expiration-panel{background:#202020;border-color:#333;}
    .message-expiration-any-locked .md-checkmark{background:#4B5563;border-color:#6B7280;}
    .message-expiration-any-locked .md-checkmark:after{border-color:#E5E7EB;}
    .message-expiration-any-locked .message-expiration-text{color:#9E9E9E;}
    .message-icon-preview{background:#252525;border-color:#333;color:#BBB;}
    .message-icon-meta{color:#AAA;}
    .message-icon-clear{background:#252525;border-color:#444;color:#E5E7EB;}
    .message-icon-clear:hover{background:#303030;}
    .message-icon-note{background:#3A2B10;border-color:#6B4F12;color:#FFD54F;}
    .message-icon-picker-modal{background:#1E1E1E;}
    .message-icon-picker-header{border-bottom-color:#333;}
    .message-icon-picker-close{color:#AAA;}
    .message-icon-asset-card{background:#252525;border-color:#3A3A3A;box-shadow:none;}
    .message-icon-asset-card:hover{border-color:#4A4A4A;box-shadow:none;}
    .message-icon-asset-card.selected{border-color:#8AB4F8;box-shadow:0 0 0 2px rgba(138,180,248,0.18);}
    .message-icon-asset-preview{background:#2A2A2A;color:#BBB;}
    .message-icon-asset-meta{color:#AAA;}
    .message-icon-asset-status{color:#8AB4F8;}
    .message-icon-asset-card.unsupported .message-icon-asset-status{color:#EF9A9A;}
    .message-icon-picker-empty{background:#202020;border-color:#333;color:#BBB;}
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
function messageExpirationSpecificCheckboxes() {
    return Array.from(document.querySelectorAll('.message-expiration-specific'));
}
function buildMessageExpirationValue() {
    const immediate = document.getElementById('messageExpirationImmediate');
    const manual = document.getElementById('messageExpirationManual');
    const afterEnabled = document.getElementById('messageExpirationAfterEnabled');
    const afterMinutes = document.getElementById('messageExpirationAfterMinutes');
    const whenMessage = document.getElementById('messageExpirationWhenMessage');
    const anyMessage = document.getElementById('messageExpirationAnyMessage');
    const specificIds = messageExpirationSpecificCheckboxes().filter(cb => cb.checked).map(cb => cb.value);
    if (!immediate) return '';
    if (immediate.checked) return '0m';
    const tokens = [];
    if (manual && manual.checked) tokens.push('manual');
    if (afterEnabled && afterEnabled.checked) {
        const minutes = Number(afterMinutes ? afterMinutes.value : '');
        if (Number.isFinite(minutes) && minutes >= 1) tokens.push(String(Math.floor(minutes)) + 'm');
    }
    if (whenMessage && whenMessage.checked) {
        if (anyMessage && anyMessage.checked) tokens.push('msg=*');
        else if (specificIds.length) tokens.push('msg=' + specificIds.join('.'));
    }
    return tokens.length ? tokens.join('|') : 'manual';
}
function syncMessageExpiration() {
    const hidden = document.getElementById('expires');
    const immediate = document.getElementById('messageExpirationImmediate');
    const manual = document.getElementById('messageExpirationManual');
    const afterEnabled = document.getElementById('messageExpirationAfterEnabled');
    const afterMinutes = document.getElementById('messageExpirationAfterMinutes');
    const whenMessage = document.getElementById('messageExpirationWhenMessage');
    const anyMessage = document.getElementById('messageExpirationAnyMessage');
    const panel = document.getElementById('messageExpirationMessagesPanel');
    const specifics = messageExpirationSpecificCheckboxes();
    if (!hidden || !immediate) return true;
    if (immediate.checked) {
        if (manual) manual.checked = false;
        if (afterEnabled) afterEnabled.checked = false;
        if (whenMessage) whenMessage.checked = false;
        if (anyMessage) anyMessage.checked = false;
        specifics.forEach(cb => { cb.checked = false; });
    } else if (manual && afterEnabled && whenMessage && !manual.checked && !afterEnabled.checked && !whenMessage.checked) {
        manual.checked = true;
    }
    if (manual) manual.disabled = immediate.checked;
    if (afterEnabled) afterEnabled.disabled = immediate.checked;
    if (whenMessage) whenMessage.disabled = immediate.checked;
    if (afterMinutes) {
        if (afterEnabled && afterEnabled.checked) {
            const minutes = Number(afterMinutes.value);
            if (!Number.isFinite(minutes) || minutes < 1) afterMinutes.value = '1';
        }
        afterMinutes.disabled = immediate.checked || !afterEnabled || !afterEnabled.checked;
    }
    if (anyMessage) anyMessage.disabled = immediate.checked || !whenMessage || !whenMessage.checked;
    specifics.forEach(cb => {
        const locked = !!(immediate.checked || !whenMessage || !whenMessage.checked || (anyMessage && anyMessage.checked));
        cb.disabled = locked;
        const label = cb.closest('.md-checkbox-container');
        if (label) label.classList.toggle('message-expiration-any-locked', !!(anyMessage && anyMessage.checked && whenMessage && whenMessage.checked && !immediate.checked));
    });
    if (panel) panel.classList.toggle('message-expiration-panel-disabled', !!(immediate.checked || !whenMessage || !whenMessage.checked));
    hidden.value = buildMessageExpirationValue();
    return true;
}
function toggleMessageExpirationWhenMessage() {
    const whenMessage = document.getElementById('messageExpirationWhenMessage');
    const anyMessage = document.getElementById('messageExpirationAnyMessage');
    if (whenMessage && whenMessage.checked && anyMessage) {
        const specifics = messageExpirationSpecificCheckboxes();
        if (!anyMessage.checked && specifics.every(cb => !cb.checked)) anyMessage.checked = true;
    }
    return syncMessageExpiration();
}
function messageExpirationSelectSpecific(input) {
    const anyMessage = document.getElementById('messageExpirationAnyMessage');
    if (anyMessage && anyMessage.checked) anyMessage.checked = false;
    if (input) input.disabled = false;
    return syncMessageExpiration();
}
function validateMessageExpiration() {
    const hidden = document.getElementById('expires');
    const immediate = document.getElementById('messageExpirationImmediate');
    const afterEnabled = document.getElementById('messageExpirationAfterEnabled');
    const afterMinutes = document.getElementById('messageExpirationAfterMinutes');
    const whenMessage = document.getElementById('messageExpirationWhenMessage');
    const anyMessage = document.getElementById('messageExpirationAnyMessage');
    if (!hidden || !immediate || immediate.checked) return true;
    if (afterEnabled && afterEnabled.checked) {
        const minutes = Number(afterMinutes ? afterMinutes.value : '');
        if (!Number.isFinite(minutes) || minutes < 1) {
            alert('After minutes must be 1 or greater.');
            if (afterMinutes) afterMinutes.focus();
            return false;
        }
    }
    if (whenMessage && whenMessage.checked && !(anyMessage && anyMessage.checked) && messageExpirationSpecificCheckboxes().every(cb => !cb.checked)) {
        alert('Select Any message or at least one message.');
        return false;
    }
    return true;
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
let activeVariableFieldId = '';
let activeVariableWizardKey = '';
const variableWizardTitles = {
    'date': 'Date',
    'date+time': 'Date + Time',
    'time': 'Time',
    'sender': 'Sender',
    'api': 'API'
};
function openVariableGuide(fieldId) {
    activeVariableFieldId = fieldId || '';
    const modal = document.getElementById('messageVariableModal');
    const backdrop = document.getElementById('messageVariableBackdrop');
    if (modal) modal.classList.add('open');
    if (backdrop) backdrop.classList.add('open');
    showVariableList();
}
function closeVariableGuide() {
    const modal = document.getElementById('messageVariableModal');
    const backdrop = document.getElementById('messageVariableBackdrop');
    if (modal) modal.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
    activeVariableWizardKey = '';
}
function insertVariableSnippet(snippet) {
    const field = document.getElementById(activeVariableFieldId);
    if (!field) {
        closeVariableGuide();
        return;
    }
    const currentValue = field.value || '';
    const start = typeof field.selectionStart === 'number' ? field.selectionStart : currentValue.length;
    const end = typeof field.selectionEnd === 'number' ? field.selectionEnd : currentValue.length;
    field.value = currentValue.slice(0, start) + snippet + currentValue.slice(end);
    const caret = start + snippet.length;
    if (typeof field.setSelectionRange === 'function') field.setSelectionRange(caret, caret);
    field.focus();
    closeVariableGuide();
}
function setVariableWizardTitle(text) {
    const title = document.getElementById('messageVariableTitle');
    if (title) title.textContent = text || 'Insert Variable';
}
function showVariableList() {
    activeVariableWizardKey = '';
    const listView = document.getElementById('messageVariableListView');
    if (listView) listView.style.display = 'grid';
    document.querySelectorAll('.message-variable-wizard').forEach(el => el.classList.remove('open'));
    const back = document.getElementById('messageVariableBack');
    if (back) back.classList.remove('visible');
    setVariableWizardTitle('Insert Variable');
}
function openVariableWizard(key) {
    if (key === 'productname') {
        insertVariableSnippet('${productname}');
        return;
    }
    activeVariableWizardKey = key || '';
    const listView = document.getElementById('messageVariableListView');
    if (listView) listView.style.display = 'none';
    document.querySelectorAll('.message-variable-wizard').forEach(el => {
        el.classList.toggle('open', el.getAttribute('data-variable-key') === key);
    });
    const back = document.getElementById('messageVariableBack');
    if (back) back.classList.add('visible');
    setVariableWizardTitle(variableWizardTitles[key] || 'Insert Variable');
}
function variableFieldValue(id) {
    const field = document.getElementById(id);
    return field ? String(field.value || '').trim() : '';
}
function setVariableFieldValue(id, value) {
    const field = document.getElementById(id);
    if (!field) return;
    field.value = value;
    field.focus();
}
function insertVariableWithOption(baseKey, optionValue) {
    const key = String(baseKey || '').trim();
    const option = String(optionValue || '').trim();
    if (!key) return;
    if (!option) {
        insertVariableSnippet('${' + key + '}');
        return;
    }
    insertVariableSnippet('${' + key + ':' + option + '}');
}
function insertApiVariable() {
    const url = variableFieldValue('messageVariableApiUrl');
    if (!url) {
        alert('Enter an API URL first.');
        return;
    }
    insertVariableSnippet('${api:' + url + '}');
}
async function testVariableApi() {
    const url = variableFieldValue('messageVariableApiUrl');
    const progress = document.getElementById('messageVariableApiProgress');
    const statusLine = document.getElementById('messageVariableApiStatus');
    const resultBox = document.getElementById('messageVariableApiResult');
    if (!resultBox || !progress || !statusLine) return;
    progress.textContent = '';
    progress.classList.remove('open');
    statusLine.textContent = '';
    statusLine.classList.remove('open');
    resultBox.textContent = '';
    resultBox.classList.remove('open');
    if (!url) {
        statusLine.textContent = 'Enter an API URL first.';
        statusLine.classList.add('open');
        return;
    }
    progress.textContent = 'Testing...';
    progress.classList.add('open');
    try {
        const response = await fetch('/messages/variable-api-test', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ url: url })
        });
        const payload = await response.json();
        progress.textContent = '';
        progress.classList.remove('open');
        const statusCode = Number(payload.status_code || 0);
        statusLine.textContent = statusCode > 0 ? 'HTTP ' + statusCode : (payload.error || 'API test failed.');
        statusLine.classList.add('open');
        if (!response.ok || !payload.ok) {
            return;
        }
        if (payload.result) {
            resultBox.textContent = payload.result;
            resultBox.classList.add('open');
        }
    } catch (_error) {
        progress.textContent = '';
        progress.classList.remove('open');
        statusLine.textContent = 'API test failed.';
        statusLine.classList.add('open');
    }
}
function messageIconElements() {
    return {
        input: document.getElementById('messageIconValue'),
        preview: document.getElementById('messageIconPreview'),
        name: document.getElementById('messageIconName'),
        meta: document.getElementById('messageIconMeta'),
        note: document.getElementById('messageIconTransparencyNote'),
        clear: document.getElementById('messageIconClear'),
        modal: document.getElementById('messageIconPickerModal'),
        backdrop: document.getElementById('messageIconPickerBackdrop')
    };
}
function messageIconPreviewHtml(kind, url, name) {
    if (kind === 'image' && url) {
        return '<img src="' + url + '" alt="' + (name || 'Selected icon') + '">';
    }
    if (kind === 'audio') return '<i class="fa-solid fa-music"></i>';
    if (kind === 'text') return '<i class="fa-solid fa-file-lines"></i>';
    if (kind === 'missing') return '<i class="fa-solid fa-triangle-exclamation"></i>';
    return '<i class="fa-solid fa-image"></i>';
}
function setMessageIconSelectedCard(name) {
    document.querySelectorAll('.message-icon-asset-card.selected').forEach(function(card) {
        card.classList.remove('selected');
    });
    if (!name) return;
    document.querySelectorAll('.message-icon-asset-card').forEach(function(card) {
        if (card.getAttribute('data-name') === name) {
            card.classList.add('selected');
        }
    });
}
function updateMessageIconWarning(name, transparent, previewKind) {
    const note = messageIconElements().note;
    if (!note) return;
    const show = !!name && previewKind === 'image' && !transparent;
    note.classList.toggle('open', show);
}
function clearMessageIconPickerMessage() {
    return;
}
function setMessageIconSelection(name, url, transparent, previewKind, metaText) {
    const elements = messageIconElements();
    if (!elements.input || !elements.preview || !elements.name || !elements.meta) return;
    const hasValue = !!name;
    elements.input.value = hasValue ? name : '';
    elements.input.dataset.url = hasValue ? (url || '') : '';
    elements.input.dataset.transparent = transparent ? '1' : '0';
    elements.input.dataset.previewKind = hasValue ? (previewKind || 'image') : 'empty';
    elements.input.dataset.meta = hasValue ? (metaText || '') : '';
    elements.preview.innerHTML = hasValue ? messageIconPreviewHtml(previewKind || 'image', url || '', name) : '<i class="fa-solid fa-image"></i>';
    elements.name.textContent = hasValue ? name : 'No icon selected';
    elements.meta.textContent = hasValue ? (metaText || '') : '';
    if (elements.clear) elements.clear.disabled = !hasValue;
    setMessageIconSelectedCard(hasValue ? name : '');
    updateMessageIconWarning(hasValue ? name : '', !!transparent, hasValue ? (previewKind || 'image') : 'empty');
}
function openMessageIconPicker() {
    const elements = messageIconElements();
    if (!elements.modal || !elements.backdrop) return;
    clearMessageIconPickerMessage();
    elements.modal.classList.add('open');
    elements.backdrop.classList.add('open');
}
function closeMessageIconPicker() {
    const elements = messageIconElements();
    if (!elements.modal || !elements.backdrop) return;
    elements.modal.classList.remove('open');
    elements.backdrop.classList.remove('open');
}
function clearMessageIconSelection() {
    setMessageIconSelection('', '', false, 'empty', '');
}
function chooseMessageIconAsset(button) {
    if (!button) return;
    const supported = button.getAttribute('data-supported') === '1';
    if (!supported) return;
    setMessageIconSelection(
        button.getAttribute('data-name') || '',
        button.getAttribute('data-url') || '',
        button.getAttribute('data-transparent') === '1',
        button.getAttribute('data-preview-kind') || 'image',
        button.getAttribute('data-meta') || ''
    );
    closeMessageIconPicker();
}
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        closeVariableGuide();
        closeMessageIconPicker();
    }
});
document.addEventListener('DOMContentLoaded', function() {
    syncMessageExpiration();
});
document.addEventListener('submit', function(event) {
    if (!event.target || !event.target.querySelector || !event.target.querySelector('#expires')) return;
    syncMessageExpiration();
    if (!validateMessageExpiration()) event.preventDefault();
});
"""


def message_multiline_text(value):
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def message_variable_field_html(field_id, label, control_html, help_text=""):
    help_html = f'<p class="help-text">{help_text}</p>' if help_text else ""
    wrap_class = "message-variable-wrap"
    if field_id == "shortmessage":
        wrap_class += " message-variable-wrap-short"
    elif field_id == "longmessage":
        wrap_class += " message-variable-wrap-long"
    return f"""            <div class="form-group">
                <label class="main-label" for="{h(field_id)}">{h(label)}</label>
                {help_html}
                <div class="{wrap_class}">
                    {control_html}
                    <button type="button" class="message-variable-badge" onclick="openVariableGuide('{h(field_id)}')" title="Insert Variable">$&#40;x&#125;</button>
                </div>
            </div>
"""


def material_radio_group_html(name, options, selected="", onchange="", required=False):
    rows = []
    for index, (value, label) in enumerate(options):
        checked = ' checked' if str(selected) == str(value) else ""
        onchange_attr = f' onchange="{h(onchange)}"' if onchange else ""
        required_attr = " required" if required and index == 0 else ""
        rows.append(
            f"""                    <label class="md-radio-option">
                        <input type="radio" name="{h(name)}" value="{h(value)}"{checked}{required_attr}{onchange_attr}>
                        <span class="md-radio-indicator"></span>
                        <span class="md-radio-text">{h(label)}</span>
                    </label>"""
        )
    return '<div class="md-radio-group">\n' + "\n".join(rows) + "\n                </div>"


def message_variable_guide_html():
    return f"""
    <div id="messageVariableBackdrop" class="message-variable-modal-backdrop" onclick="closeVariableGuide()"></div>
    <div id="messageVariableModal" class="message-variable-modal" role="dialog" aria-modal="true" aria-labelledby="messageVariableTitle">
        <div class="message-variable-modal-header">
            <h2 id="messageVariableTitle">Insert Variable</h2>
            <div class="message-variable-modal-header-actions">
                <button type="button" id="messageVariableBack" class="message-variable-modal-back" onclick="showVariableList()">Back</button>
                <button type="button" class="message-variable-modal-close" onclick="closeVariableGuide()" aria-label="Close">&times;</button>
            </div>
        </div>
        <div class="message-variable-modal-body">
            <div id="messageVariableListView" class="message-variable-list">
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('date')">Date</button>
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('date+time')">Date + Time</button>
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('time')">Time</button>
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('sender')">Sender</button>
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('api')">API</button>
                <button type="button" class="message-variable-choice" onclick="openVariableWizard('productname')">Product Name</button>
            </div>

            <div class="message-variable-wizard" data-variable-key="date">
                <div class="message-variable-row">
                    <label>Choose a date format</label>
                    <div class="message-variable-option-list">
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date', '')">
                            <strong>Default</strong>
                            <span>06/22/2026</span>
                            <span class="message-variable-preview">${{date}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date', 'MM/DD/YYYY')">
                            <strong>US Long</strong>
                            <span>06/22/2026</span>
                            <span class="message-variable-preview">${{date:MM/DD/YYYY}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date', 'MM/DD/YY')">
                            <strong>US Short</strong>
                            <span>06/22/26</span>
                            <span class="message-variable-preview">${{date:MM/DD/YY}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date', 'YYYY-MM-DD')">
                            <strong>ISO</strong>
                            <span>2026-06-22</span>
                            <span class="message-variable-preview">${{date:YYYY-MM-DD}}</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="message-variable-wizard" data-variable-key="date+time">
                <div class="message-variable-row">
                    <label>Choose a date and time format</label>
                    <div class="message-variable-option-list">
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date+time', '')">
                            <strong>Default</strong>
                            <span>06/22/2026 03:04 PM</span>
                            <span class="message-variable-preview">${{date+time}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date+time', 'MM/DD/YYYY hh:mm A')">
                            <strong>US 12-Hour</strong>
                            <span>06/22/2026 03:04 PM</span>
                            <span class="message-variable-preview">${{date+time:MM/DD/YYYY hh:mm A}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date+time', 'MM/DD/YYYY HH:mm:ss')">
                            <strong>US 24-Hour With Seconds</strong>
                            <span>06/22/2026 15:04:05</span>
                            <span class="message-variable-preview">${{date+time:MM/DD/YYYY HH:mm:ss}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('date+time', 'YYYY-MM-DD HH:mm:ss')">
                            <strong>ISO</strong>
                            <span>2026-06-22 15:04:05</span>
                            <span class="message-variable-preview">${{date+time:YYYY-MM-DD HH:mm:ss}}</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="message-variable-wizard" data-variable-key="time">
                <div class="message-variable-row">
                    <label>Choose a time format</label>
                    <div class="message-variable-option-list">
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('time', '')">
                            <strong>Default</strong>
                            <span>03:04 PM</span>
                            <span class="message-variable-preview">${{time}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('time', 'hh:mm A')">
                            <strong>12-Hour</strong>
                            <span>03:04 PM</span>
                            <span class="message-variable-preview">${{time:hh:mm A}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('time', 'hh:mm:ss A')">
                            <strong>12-Hour With Seconds</strong>
                            <span>03:04:05 PM</span>
                            <span class="message-variable-preview">${{time:hh:mm:ss A}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('time', 'HH:mm')">
                            <strong>24-Hour</strong>
                            <span>15:04</span>
                            <span class="message-variable-preview">${{time:HH:mm}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('time', 'HH:mm:ss')">
                            <strong>24-Hour With Seconds</strong>
                            <span>15:04:05</span>
                            <span class="message-variable-preview">${{time:HH:mm:ss}}</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="message-variable-wizard" data-variable-key="sender">
                <div class="message-variable-row">
                    <label>Choose sender information</label>
                    <div class="message-variable-option-list">
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('sender', '')">
                            <strong>Default</strong>
                            <span>Name and number when available</span>
                            <span class="message-variable-preview">${{sender}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('sender', '[CNAM] [CID]')">
                            <strong>Name + Number</strong>
                            <span>Caller name followed by caller ID number</span>
                            <span class="message-variable-preview">${{sender:[CNAM] [CID]}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('sender', '[CNAM]')">
                            <strong>Name Only</strong>
                            <span>Caller or sender name only</span>
                            <span class="message-variable-preview">${{sender:[CNAM]}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('sender', '[CID]')">
                            <strong>Number Only</strong>
                            <span>Caller ID number only</span>
                            <span class="message-variable-preview">${{sender:[CID]}}</span>
                        </button>
                        <button type="button" class="message-variable-option" onclick="insertVariableWithOption('sender', '[USERNAME]')">
                            <strong>Username Only</strong>
                            <span>Web or API username only</span>
                            <span class="message-variable-preview">${{sender:[USERNAME]}}</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="message-variable-wizard" data-variable-key="api">
                <div class="message-variable-row">
                    <label for="messageVariableApiUrl">API URL</label>
                    <input type="text" id="messageVariableApiUrl" class="form-control" placeholder="https://www.example.com/something">
                    <div class="hint">Enter an HTTP or HTTPS URL to fetch when the message is sent.</div>
                </div>
                <div class="message-variable-actions">
                    <button type="button" class="message-variable-secondary" onclick="testVariableApi()">Test</button>
                    <button type="button" class="message-variable-primary" onclick="insertApiVariable()">Insert Variable</button>
                </div>
                <div id="messageVariableApiProgress" class="message-variable-status"></div>
                <div id="messageVariableApiStatus" class="message-variable-status"></div>
                <div id="messageVariableApiResult" class="message-variable-test-result"></div>
            </div>
        </div>
    </div>"""


def message_expiration_field_html(available_messages, current_value="manual"):
    state = message_expiration_state(current_value)
    immediate_checked = " checked" if state["immediate"] else ""
    manual_checked = " checked" if state["manual"] else ""
    after_checked = " checked" if state["after_enabled"] else ""
    when_checked = " checked" if state["when_message"] else ""
    any_checked = " checked" if state["any_message"] else ""
    message_rows = []
    for row in available_messages or []:
        message_id = str(row.get("messageid") or "").strip()
        if not message_id:
            continue
        label = str(row.get("name") or "").strip() or f"Message {message_id}"
        checked = " checked" if message_id in state["message_ids"] else ""
        message_rows.append(
            f"""                        <label class="md-checkbox-container">
                            <input type="checkbox" class="message-expiration-specific" name="expiration_message_ids[]" value="{h(message_id)}" onclick="messageExpirationSelectSpecific(this)" onchange="syncMessageExpiration()"{checked}>
                            <span class="md-checkmark"></span>
                            <span class="message-expiration-text">
                                <span class="message-expiration-title">{h(label)}</span>
                                <span class="message-expiration-message-meta">Message {h(message_id)}</span>
                            </span>
                        </label>"""
        )
    if not message_rows:
        message_rows.append('<div class="help-text" style="margin:0;">No other messages are available yet.</div>')
    return f"""            <div class="form-group">
                <label class="main-label" for="messageExpirationImmediate">Expiration</label>
                <input type="hidden" name="expires" id="expires" value="{h(serialize_message_expiration(
                    immediate=state["immediate"],
                    manual=state["manual"],
                    after_enabled=state["after_enabled"],
                    after_minutes=state["after_minutes"],
                    when_message=state["when_message"],
                    any_message=state["any_message"],
                    message_ids=sorted(state["message_ids"]),
                ))}">
                <div class="message-expiration-list">
                    <label class="md-checkbox-container">
                        <input type="checkbox" id="messageExpirationImmediate" name="expiration_immediately" value="1" onchange="syncMessageExpiration()"{immediate_checked}>
                        <span class="md-checkmark"></span>
                        <span class="message-expiration-text">
                            <span class="message-expiration-title">Immediately</span>
                        </span>
                    </label>
                    <label class="md-checkbox-container">
                        <input type="checkbox" id="messageExpirationManual" name="expiration_manual" value="1" onchange="syncMessageExpiration()"{manual_checked}>
                        <span class="md-checkmark"></span>
                        <span class="message-expiration-text">
                            <span class="message-expiration-title">Manually</span>
                        </span>
                    </label>
                    <div>
                        <label class="md-checkbox-container">
                            <input type="checkbox" id="messageExpirationAfterEnabled" name="expiration_after_enabled" value="1" onchange="syncMessageExpiration()"{after_checked}>
                            <span class="md-checkmark"></span>
                            <span class="message-expiration-text">
                                <span class="message-expiration-title">After</span>
                            </span>
                        </label>
                        <div class="message-expiration-detail">
                            <div class="message-expiration-inline">
                                <input type="number" id="messageExpirationAfterMinutes" name="expiration_after_minutes" min="1" step="1" class="form-control" value="{h(state['after_minutes'])}" oninput="syncMessageExpiration()">
                                <span>minutes</span>
                            </div>
                        </div>
                    </div>
                    <div>
                        <label class="md-checkbox-container">
                            <input type="checkbox" id="messageExpirationWhenMessage" name="expiration_when_message" value="1" onchange="toggleMessageExpirationWhenMessage()"{when_checked}>
                            <span class="md-checkmark"></span>
                            <span class="message-expiration-text">
                                <span class="message-expiration-title">When another message is sent</span>
                            </span>
                        </label>
                        <div id="messageExpirationMessagesPanel" class="message-expiration-panel">
                            <div class="message-expiration-message-list">
                                <label class="md-checkbox-container">
                                    <input type="checkbox" id="messageExpirationAnyMessage" name="expiration_any_message" value="1" onchange="syncMessageExpiration()"{any_checked}>
                                    <span class="md-checkmark"></span>
                                    <span class="message-expiration-text">
                                        <span class="message-expiration-title">Any message</span>
                                    </span>
                                </label>
{chr(10).join(message_rows)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
"""


def message_expiration_from_form(form):
    return serialize_message_expiration(
        immediate=bool(form.get("expiration_immediately")),
        manual=bool(form.get("expiration_manual")),
        after_enabled=bool(form.get("expiration_after_enabled")),
        after_minutes=form.get("expiration_after_minutes", ""),
        when_message=bool(form.get("expiration_when_message")),
        any_message=bool(form.get("expiration_any_message")),
        message_ids=form.getlist("expiration_message_ids[]"),
    )


def _png_image_metadata(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    color_type = data[25]
    transparent = color_type in {4, 6}
    offset = 8
    while offset + 8 <= len(data):
        chunk_length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 8 + chunk_length
        if chunk_end + 4 > len(data):
            break
        if chunk_type == b"tRNS":
            transparent = True
        if chunk_type == b"IDAT":
            break
        offset = chunk_end + 4
    return {"width": width, "height": height, "transparent": transparent, "format": "PNG"}


def _jpeg_image_metadata(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 3 < len(data):
        while offset < len(data) and data[offset] != 0xFF:
            offset += 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return {"width": width, "height": height, "transparent": False, "format": "JPG"}
        offset += segment_length
    return None


def _bmp_image_metadata(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 26 or not data.startswith(b"BM"):
        return None
    dib_header_size = int.from_bytes(data[14:18], "little")
    if dib_header_size == 12 and len(data) >= 26:
        width = int.from_bytes(data[18:20], "little")
        height = int.from_bytes(data[20:22], "little")
    elif dib_header_size >= 40 and len(data) >= 26:
        width = abs(int.from_bytes(data[18:22], "little", signed=True))
        height = abs(int.from_bytes(data[22:26], "little", signed=True))
    else:
        return None
    return {"width": width, "height": height, "transparent": False, "format": "BMP"}


def message_icon_asset_metadata(path):
    name = path.name
    ext = path.suffix.lower().lstrip(".")
    preview_kind = "file"
    metadata = None
    if ext == "png":
        metadata = _png_image_metadata(path)
        preview_kind = "image"
    elif ext == "jpg":
        metadata = _jpeg_image_metadata(path)
        preview_kind = "image"
    elif ext == "bmp":
        metadata = _bmp_image_metadata(path)
        preview_kind = "image"
    elif ext in {"wav", "mp3"}:
        preview_kind = "audio"
    elif ext == "txt":
        preview_kind = "text"
    supported = False
    reason = "Unsupported format"
    transparent = False
    meta_text = ""
    if metadata is not None:
        width = int(metadata.get("width") or 0)
        height = int(metadata.get("height") or 0)
        transparent = bool(metadata.get("transparent"))
        format_name = metadata.get("format") or ext.upper()
        meta_text = f"{width}x{height} - {format_name}"
        if width <= MESSAGE_ICON_MAX_DIMENSION and height <= MESSAGE_ICON_MAX_DIMENSION:
            supported = True
            reason = ""
        else:
            reason = "Image is more than 1080x1080 pixels"
    elif preview_kind == "audio":
        meta_text = "Audio asset"
    elif preview_kind == "text":
        meta_text = "Text asset"
    else:
        meta_text = "Unsupported asset"
    return {
        "name": name,
        "preview_kind": preview_kind,
        "preview_url": "/assets/?raw=" + urlencode({"": name})[1:],
        "supported": supported,
        "reason": reason,
        "transparent": transparent,
        "meta_text": meta_text,
        "status_text": "Selectable" if supported else reason,
    }


def resolve_message_icon_value(raw_value, current_value=""):
    selected = asset_filename(raw_value)
    if not selected:
        return ""
    current = asset_filename(current_value)
    try:
        path = asset_path(selected)
    except Exception:
        if current and selected.lower() == current.lower():
            return current_value or selected
        raise RuntimeError("Unsupported format")
    if not path.is_file():
        if current and path.name.lower() == current.lower():
            return current_value or path.name
        raise RuntimeError("Unsupported format")
    metadata = message_icon_asset_metadata(Path(path))
    if metadata.get("supported"):
        return path.name
    if current and path.name.lower() == current.lower():
        return path.name
    raise RuntimeError(metadata.get("reason") or "Unsupported format")


def _message_icon_summary(current_value):
    selected_name = asset_filename(current_value)
    selected_meta = None
    options = []
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    try:
        selected_path = asset_path(selected_name) if selected_name else None
    except Exception:
        selected_path = None
    canonical_selected = selected_path.name if selected_path and selected_path.is_file() else selected_name
    try:
        files = sorted([item for item in ASSET_DIR.iterdir() if item.is_file()], key=lambda item: item.name.lower())
    except OSError:
        files = []
    for path in files:
        item = message_icon_asset_metadata(path)
        item["selected"] = bool(canonical_selected) and path.name.lower() == canonical_selected.lower()
        if item["selected"]:
            selected_meta = item
        options.append(item)
    if selected_meta is None and selected_name:
        selected_meta = {
            "name": selected_name,
            "preview_kind": "missing",
            "preview_url": "",
            "supported": False,
            "reason": "Unsupported format",
            "transparent": False,
            "meta_text": "Asset not found",
            "status_text": "Unsupported format",
            "selected": True,
        }
    return canonical_selected, selected_meta, options


def _message_icon_preview_html(meta):
    if meta and meta.get("preview_kind") == "image" and meta.get("preview_url"):
        return f'<img src="{h(meta["preview_url"])}" alt="{h(meta.get("name") or "Selected icon")}">'
    if meta and meta.get("preview_kind") == "audio":
        return '<i class="fa-solid fa-music"></i>'
    if meta and meta.get("preview_kind") == "text":
        return '<i class="fa-solid fa-file-lines"></i>'
    if meta and meta.get("preview_kind") == "missing":
        return '<i class="fa-solid fa-triangle-exclamation"></i>'
    return '<i class="fa-solid fa-image"></i>'


def message_icon_field_html(current_value=""):
    selected_name, selected_meta, options = _message_icon_summary(current_value)
    note_open = bool(selected_meta and selected_name and selected_meta.get("preview_kind") == "image" and not selected_meta.get("transparent"))
    preview_kind = selected_meta.get("preview_kind") if selected_meta else "empty"
    preview_url = selected_meta.get("preview_url") if selected_meta else ""
    meta_text = selected_meta.get("meta_text") if selected_meta else ""
    asset_cards = []
    for item in options:
        classes = ["message-icon-asset-card"]
        if item.get("selected"):
            classes.append("selected")
        if not item.get("supported"):
            classes.append("unsupported")
        asset_cards.append(
            f"""                    <button type="button" class="{' '.join(classes)}" data-name="{h(item['name'])}" data-url="{h(item.get('preview_url') or '')}" data-supported="{'1' if item.get('supported') else '0'}" data-reason="{h(item.get('reason') or '')}" data-transparent="{'1' if item.get('transparent') else '0'}" data-preview-kind="{h(item.get('preview_kind') or 'file')}" data-meta="{h(item.get('meta_text') or '')}" onclick="chooseMessageIconAsset(this)">
                        <div class="message-icon-asset-preview">{_message_icon_preview_html(item)}</div>
                        <div class="message-icon-asset-info">
                            <div class="message-icon-asset-name" title="{h(item['name'])}">{h(item['name'])}</div>
                            <div class="message-icon-asset-meta">{h(item.get('meta_text') or '')}</div>
                            <div class="message-icon-asset-status">{h(item.get('status_text') or '')}</div>
                        </div>
                    </button>"""
        )
    picker_body = (
        "\n".join(asset_cards)
        if asset_cards
        else '<div class="message-icon-picker-empty">No assets are stored on the server yet.</div>'
    )
    return f"""            <div class="form-group">
                <label class="main-label" for="messageIconValue">Icon</label>
                <p class="help-text">Max: 1080x1080px</p>
                <input type="hidden" name="icon" id="messageIconValue" value="{h(selected_name or '')}" data-url="{h(preview_url or '')}" data-transparent="{'1' if selected_meta and selected_meta.get('transparent') else '0'}" data-preview-kind="{h(preview_kind)}" data-meta="{h(meta_text)}">
                <div class="message-icon-selection">
                    <div class="message-icon-summary">
                        <div id="messageIconPreview" class="message-icon-preview">{_message_icon_preview_html(selected_meta)}</div>
                        <div class="message-icon-text">
                            <div id="messageIconName" class="message-icon-name">{h(selected_meta.get("name") if selected_meta else "No icon selected")}</div>
                            <div id="messageIconMeta" class="message-icon-meta">{h(meta_text)}</div>
                        </div>
                    </div>
                    <div class="message-icon-actions">
                        <button type="button" class="btn-primary" onclick="openMessageIconPicker()">Select Icon</button>
                        <button type="button" class="message-icon-clear" id="messageIconClear" onclick="clearMessageIconSelection()"{" disabled" if not selected_name else ""}>Clear</button>
                    </div>
                </div>
                <div id="messageIconTransparencyNote" class="message-icon-note{' open' if note_open else ''}">This icon is not transparent. It is recommended to use transparent icons.</div>
                <div id="messageIconPickerBackdrop" class="message-icon-picker-backdrop" onclick="closeMessageIconPicker()"></div>
                <div id="messageIconPickerModal" class="message-icon-picker-modal" role="dialog" aria-modal="true" aria-labelledby="messageIconPickerTitle">
                    <div class="message-icon-picker-header">
                        <h2 id="messageIconPickerTitle">Select Icon</h2>
                        <button type="button" class="message-icon-picker-close" onclick="closeMessageIconPicker()" aria-label="Close">&times;</button>
                    </div>
                    <div class="message-icon-picker-body">
                        <div class="message-icon-picker-grid">
{picker_body}
                        </div>
                    </div>
                </div>
            </div>
"""


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


VENDOR_FIELD_PREFIX = "vendor_specific__"
VENDOR_RENDERER_NAMES = (
    "render_message_vendor_specific",
    "message_vendor_specific",
    "render_vendor_specific_message",
    "vendor_specific_fields",
)


def output_module_supports_vendor(module_info):
    if not module_info.get("enabled", True) or not module_info.get("can_load", True):
        return False
    if module_info.get("output_capable") is True:
        return True
    input_type = str(module_info.get("input_type") or "").lower()
    return "output" in input_type


def vendor_renderer_result(renderer, module_id, value, context):
    field_name = f"{VENDOR_FIELD_PREFIX}{module_id}"
    call_context = dict(context or {})
    call_context.update({"module": module_id, "value": value, "field_name": field_name})
    try:
        signature = inspect.signature(renderer)
    except (TypeError, ValueError):
        return renderer(value, field_name, call_context)
    parameters = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return renderer(value=value, field_name=field_name, module=module_id, context=call_context)
    if len(parameters) >= 3:
        return renderer(value, field_name, call_context)
    if len(parameters) == 2:
        return renderer(value, field_name)
    if len(parameters) == 1:
        return renderer(call_context)
    return renderer()


def normalize_vendor_renderer_payload(result, module_id):
    if result in (None, "", False):
        return None
    if isinstance(result, dict):
        html = result.get("html") or result.get("body") or ""
        if result.get("script"):
            html += f"\n<script>{result.get('script')}</script>"
        title = result.get("title") or result.get("label") or module_id
        return {"title": title, "html": html}
    return {"title": module_id, "html": str(result)}


def vendor_specific_editor_html(current_vendor_specific="", message=None, context=None):
    from srv.web.app import endpoint_module_catalog
    import endpoints

    sections = []
    base_context = dict(context or {})
    if message is not None:
        base_context["message"] = message
    modules = endpoint_module_catalog(include_system=True)
    for module_id, module_info in modules.items():
        module_id = safe_module_key(module_id)
        if not module_id or not output_module_supports_vendor(module_info):
            continue
        try:
            web_mod = endpoints.load_endpoint_web_module(module_id, missing_ok=True)
        except Exception:
            web_mod = None
        if web_mod is None:
            continue
        renderer = next((getattr(web_mod, name, None) for name in VENDOR_RENDERER_NAMES if callable(getattr(web_mod, name, None))), None)
        if renderer is None:
            continue
        value = parse_vendor_specific(current_vendor_specific).get(module_id, "")
        result = normalize_vendor_renderer_payload(vendor_renderer_result(renderer, module_id, value, base_context), module_id)
        if not result or not result["html"]:
            continue
        title = result["title"] or module_info.get("name") or module_id
        sections.append(
            f"""<details class="vendor-module">
                    <summary>{h(title)}</summary>
                    <div class="vendor-module-body">{result["html"]}</div>
                </details>"""
        )
    if not sections:
        return ""
    return f"""
            <div class="form-group">
                <div class="vendor-specific-card">
                    <h2>Vendor Specific</h2>
                    {''.join(sections)}
                </div>
            </div>"""


def vendor_specific_from_form(form, existing=""):
    values = parse_vendor_specific(existing)
    touched = False
    nested = {}
    for key in form.keys():
        if not str(key).startswith(VENDOR_FIELD_PREFIX):
            continue
        suffix = str(key)[len(VENDOR_FIELD_PREFIX):]
        if "__" in suffix:
            module_id, field_name = suffix.split("__", 1)
            module_id = safe_module_key(module_id)
            if not module_id or not field_name:
                continue
            touched = True
            nested.setdefault(module_id, {})[field_name] = form.get(key, "")
            continue
        module_id = safe_module_key(suffix)
        if not module_id:
            continue
        touched = True
        value = form.get(key, "")
        if value in (None, ""):
            values.pop(module_id, None)
        else:
            values[module_id] = value
    for module_id, module_values in nested.items():
        clean = {name: value for name, value in module_values.items() if value not in (None, "")}
        if clean:
            values[module_id] = clean
        else:
            values.pop(module_id, None)
    return serialize_vendor_specific(values) if touched else (existing or "")
