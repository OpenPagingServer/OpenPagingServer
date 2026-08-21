from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


INTERNAL_STREAMING_CODEC_PCM = "pcm_s16le_48k_stereo"
INTERNAL_STREAMING_CODEC_PCMU = "pcmu_8k_mono"
INTERNAL_STREAMING_CODEC_OPTIONS = {
    INTERNAL_STREAMING_CODEC_PCM: "PCM S16LE Stereo 48 kHz (Recommended, High quality)",
    INTERNAL_STREAMING_CODEC_PCMU: "PCMU Mono 8 kHz (Legacy, Less resources)",
}


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("Advanced Settings", legacy_user_context(user), "settings", "settings")
        selected = str(request.form.get("internal_streaming_codec") or "").strip().lower()
        if selected not in INTERNAL_STREAMING_CODEC_OPTIONS:
            message = "Select a valid internal streaming codec."
            return jsonify(status="error", message=message) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("Advanced Settings", h(message), "settings", user)
        save_setting(
            "internal_streaming_codec",
            selected,
            "This parameter defines which codec the paging server uses between internal processes",
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/advanced")

    selected = str(data.get("internal_streaming_codec") or INTERNAL_STREAMING_CODEC_PCM).strip().lower()
    if selected not in INTERNAL_STREAMING_CODEC_OPTIONS:
        selected = INTERNAL_STREAMING_CODEC_PCM
    options = "".join(
        f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
        for value, label in INTERNAL_STREAMING_CODEC_OPTIONS.items()
    )
    ctx = legacy_user_context(user)
    body = f"""
    <div id="advanced" class="tab-content active">
        <div class="info-card login-settings">
            <form id="advancedSettingsForm">
                <div class="info-row">
                    <span class="info-label">
                        Internal Streaming Codec
                        <span class="info-description">This parameter defines which codec the paging server uses between internal processes</span>
                    </span>
                    <span style="min-width:min(100%, 430px);">
                        <select name="internal_streaming_codec" aria-label="Internal Streaming Codec">{options}</select>
                    </span>
                </div>
                <div style="margin-top:16px;">
                    <button type="button" id="saveAdvancedBtn">Save Settings</button>
                    <span id="advanced-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>
    """
    script = "document.addEventListener('DOMContentLoaded', function(){ postSettings('advancedSettingsForm','saveAdvancedBtn','advanced-save-status','Advanced settings saved.', false); });"
    return settings_page("Advanced Settings", ctx, "advanced", body, script)
