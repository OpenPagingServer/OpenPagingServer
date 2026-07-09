from srv.web.app import *
import base64
import hashlib
import json
import mimetypes
import re

PRIORITY_ORDER = {"emergency": 0, "high": 1, "normal": 2, "low": 3}
ICON_DATA_URI_CACHE = {}


def _icon_data_uri(icon_name):
    name = str(icon_name or "").strip()
    if not name:
        return ""
    path = ASSET_DIR / name
    try:
        if not path.is_file():
            return ""
        stat = path.stat()
        cache_key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
        cached = ICON_DATA_URI_CACHE.get(name)
        if cached and cached.get("key") == cache_key:
            return cached.get("data") or ""
        raw = path.read_bytes()
    except Exception:
        return ""
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    data_uri = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    ICON_DATA_URI_CACHE[name] = {"key": cache_key, "data": data_uri}
    if len(ICON_DATA_URI_CACHE) > 256:
        ICON_DATA_URI_CACHE.pop(next(iter(ICON_DATA_URI_CACHE)), None)
    return data_uri


def _text_color_for(background):
    token = str(background or "").lstrip("#")
    try:
        r, g, b = int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16)
    except (ValueError, IndexError):
        return "#FFFFFF"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#1A1A1A" if luminance > 0.6 else "#FFFFFF"


def _sort_key(record):
    priority = str(record.get("priority") or "Normal").strip().lower()
    rank = PRIORITY_ORDER.get(priority, 2)
    name = (str(record.get("shortmessage") or "") + str(record.get("longmessage") or "")).strip().lower()
    if not name:
        name = str(record.get("name") or "").strip().lower()
    return (rank, name)


def _active_records_for(user_id):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for record in list_active_broadcasts(limit=500):
        if str(record.get("delivery") or "") == "failed":
            continue
        expires = str(record.get("expires") or "").strip()
        if expires and expires <= now_text:
            continue
        if not user_in_broadcast(user_id, record):
            continue
        records.append(record)
    records.sort(key=_sort_key)
    return records


def _has_audio(record):
    return bool(first_audio_name(record) or str(record.get("runtime_recording") or "").strip())


def _resolved_name(record):
    runtime_kind = str(record.get("runtime_kind") or "").strip().lower()
    msg_type = str(record.get("type") or "").strip().lower()
    if runtime_kind == "bell":
        return "Bell"
    if runtime_kind == "livepage" or msg_type == "page":
        return "Live Page"
    return str(record.get("name") or "").strip()


def _compact_message_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    return re.sub(r"\n{3,}", "\n\n", text)


def _message_card(record):
    color_raw = str(record.get("color") or "").strip()
    color = normalize_color(color_raw) if color_raw else ""
    text_color = _text_color_for(color) if color else ""
    icon_uri = _icon_data_uri(record.get("icon"))
    icon_html = f'<img class="msg-icon" src="{h(icon_uri)}" alt="">' if icon_uri else ""
    longmessage = _compact_message_text(record.get("longmessage"))
    shortmessage = _compact_message_text(record.get("shortmessage"))
    name = _compact_message_text(_resolved_name(record))
    body = ""
    if name:
        body += f'<div class="msg-name">{h(name)}</div>'
    if shortmessage:
        body += f'<div class="msg-short">{h(shortmessage)}</div>'
    if longmessage:
        body += f'<div class="msg-long">{h(longmessage)}</div>'
    broadcast_id = str(record.get("id") or "")
    audio_html = ""
    if _has_audio(record) and broadcast_id:
        audio_html = f"""<div class="msg-audio" data-bid="{h(broadcast_id)}">
            <button type="button" class="msg-audio-btn" onclick="toggleDashAudio(this)" data-state="stopped" aria-label="Play audio">
                <svg class="msg-audio-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                <svg class="msg-audio-stop" viewBox="0 0 24 24" style="display:none;"><path d="M6 6h12v12H6z"/></svg>
            </button>
        </div>"""
    issued = str(record.get("issued") or "").strip()
    expires = str(record.get("expires") or "").strip()
    sender = str(record.get("sender") or "").strip() or "Unknown"
    meta_parts = []
    if issued:
        meta_parts.append(f'Issued <span class="msg-ts" data-ts="{h(issued)}">{h(issued)}</span>')
    if expires:
        meta_parts.append(f'Expires <span class="msg-ts" data-ts="{h(expires)}">{h(expires)}</span>')
    meta_parts.append(f"Sent by {h(sender)}")
    meta = ' <span class="msg-dot">&middot;</span> '.join(meta_parts)
    style = f' style="background:{h(color)};color:{h(text_color)};"' if color else ""
    default_cls = " msg-card-default" if not color else ""
    return (
        f'<div class="msg-card{default_cls}" data-bid="{h(broadcast_id)}"{style}>'
        f'{icon_html}{body}<div class="msg-footer">{audio_html}<div class="msg-meta">{meta}</div></div></div>'
    )


def _cards_html(records):
    if not records:
        return '<p class="no-messages">You have no messages</p>'
    return "\n".join(_message_card(record) for record in records)


def _records_digest(records):
    normalized = []
    for record in records or []:
        normalized.append(
            {
                "id": str(record.get("id") or ""),
                "issued": str(record.get("issued") or ""),
                "expires": str(record.get("expires") or ""),
                "delivery": str(record.get("delivery") or ""),
                "name": str(record.get("name") or ""),
                "shortmessage": str(record.get("shortmessage") or ""),
                "longmessage": str(record.get("longmessage") or ""),
                "priority": str(record.get("priority") or ""),
                "sender": str(record.get("sender") or ""),
                "color": str(record.get("color") or ""),
                "icon": str(record.get("icon") or ""),
                "audio": str(record.get("audio") or ""),
                "runtime_recording": str(record.get("runtime_recording") or ""),
                "runtime_kind": str(record.get("runtime_kind") or ""),
                "type": str(record.get("type") or ""),
            }
        )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


DASHBOARD_STYLE = (
    ".msg-card{position:relative;border-radius:10px;padding:18px 88px 16px 18px;margin:0 0 16px;"
    "box-shadow:0 1px 4px rgba(0,0,0,0.18);}"
    ".msg-card-default{background:#FFFFFF;color:#1A1A1A;}"
    "@media (prefers-color-scheme: dark){.msg-card-default{background:#3A3A3A;color:#F5F5F5;}}"
    ".msg-icon{position:absolute;top:16px;right:16px;width:48px;height:48px;object-fit:contain;}"
    ".msg-name{font-family:'Roboto',Arial,sans-serif;font-size:0.95em;opacity:0.9;margin-bottom:8px;"
    "white-space:pre-line;overflow-wrap:anywhere;word-break:break-word;}"
    ".msg-short{font-family:'Roboto',Arial,sans-serif;font-size:1.55em;line-height:1.3;font-weight:500;"
    "white-space:pre-line;overflow-wrap:anywhere;word-break:break-word;}"
    ".msg-long{font-family:'Roboto',Arial,sans-serif;font-size:1em;line-height:1.45;margin-top:6px;opacity:0.95;"
    "white-space:pre-line;overflow-wrap:anywhere;word-break:break-word;}"
    ".msg-footer{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin-top:12px;}"
    ".msg-audio{position:static;display:flex;align-items:center;justify-content:center;min-width:40px;flex:0 0 auto;order:2;margin-left:auto;}"
    ".msg-audio-btn{width:40px;height:40px;border:none;border-radius:999px;background:transparent;color:inherit;"
    "display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:0;transition:background 120ms ease;}"
    ".msg-audio-btn:hover{background:rgba(127,127,127,0.35);}"
    ".msg-audio-btn svg{width:28px;height:28px;fill:currentColor;}"
    ".msg-meta{position:static;font-size:0.82em;opacity:0.85;text-align:left;padding-right:0;flex:1 1 auto;min-width:0;order:1;}"
    ".msg-dot{margin:0 4px;}"
    ".no-messages{color:#666;font-size:1.05em;margin-top:8px;}"
)

DASHBOARD_SCRIPT = r"""
function formatDashTimestamps(root) {
  (root || document).querySelectorAll('.msg-ts[data-ts]').forEach(function(el) {
    var raw = el.getAttribute('data-ts');
    if (!raw) return;
    var iso = raw.replace(' ', 'T');
    var date = new Date(iso);
    if (isNaN(date.getTime())) return;
    el.textContent = date.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });
  });
}
var dashHash = window.__DASH_HASH__;
var dashAudio = null;
var dashAudioButton = null;
var dashLiveBid = '';
var dashLivePaused = false;
var dashLiveQueue = [];
var dashAudioCtx = null;
var dashAudioNode = null;
var dashWs = null;
var dashWsToken = '';
var dashWsPingTimer = null;
var dashBroadcastMeta = {};
var dashNotificationEnabled = false;
var dashLastPollAt = 0;
var dashPollInFlight = false;
var dashAutoAudioUnlocked = false;
var dashAutoPlayAttempted = {};
var dashLiveManuallyPaused = {};
var DASH_NOTIFY_SEEN_KEY = 'ops-dashboard-notified-v1';
var dashSeenNotificationBids = {};
try {
  var dashSeenRaw = localStorage.getItem(DASH_NOTIFY_SEEN_KEY);
  if (dashSeenRaw) {
    JSON.parse(dashSeenRaw).forEach(function(item) {
      var bid = String(item || '').trim();
      if (bid) dashSeenNotificationBids[bid] = true;
    });
  }
} catch (_e) {}
function rememberDashNotifiedBid(bid) {
  var token = String(bid || '').trim();
  if (!token || dashSeenNotificationBids[token]) return;
  dashSeenNotificationBids[token] = true;
  try {
    var keys = Object.keys(dashSeenNotificationBids);
    if (keys.length > 400) keys = keys.slice(keys.length - 400);
    localStorage.setItem(DASH_NOTIFY_SEEN_KEY, JSON.stringify(keys));
  } catch (_e) {}
}
function setDashButton(button, playing) {
  if (!button) return;
  button.setAttribute('data-state', playing ? 'playing' : 'stopped');
  var play = button.querySelector('.msg-audio-play');
  var stop = button.querySelector('.msg-audio-stop');
  if (play) play.style.display = playing ? 'none' : '';
  if (stop) stop.style.display = playing ? '' : 'none';
}
function stopDashRecordingAudio() {
  if (dashAudio) {
    try { dashAudio.pause(); } catch (_e) {}
    dashAudio = null;
  }
  if (dashAudioButton) setDashButton(dashAudioButton, false);
  dashAudioButton = null;
}
function stopDashLiveAudio() {
  if (dashLiveBid) dashLiveManuallyPaused[dashLiveBid] = true;
  dashLivePaused = true;
  dashLiveQueue = [];
  updateAllDashButtons();
}
function ulawByteToFloat(byte) {
  var u = (~byte) & 0xff;
  var sign = u & 0x80;
  var exponent = (u >> 4) & 0x07;
  var mantissa = u & 0x0f;
  var sample = ((mantissa << 3) + 0x84) << exponent;
  sample = sign ? (0x84 - sample) : (sample - 0x84);
  return Math.max(-1, Math.min(1, sample / 32768));
}
function decodeUlawFrame(payload) {
  var out = new Float32Array(payload.length);
  for (var i = 0; i < payload.length; i += 1) out[i] = ulawByteToFloat(payload[i]);
  return out;
}
function ensureLiveAudioContext() {
  if (dashAudioCtx || !window.AudioContext) return;
  try {
    dashAudioCtx = new AudioContext({sampleRate: 8000});
    dashAudioNode = dashAudioCtx.createScriptProcessor(1024, 0, 1);
    dashAudioNode.onaudioprocess = function(event) {
      var output = event.outputBuffer.getChannelData(0);
      output.fill(0);
      if (!dashLiveBid || dashLivePaused) return;
      var offset = 0;
      while (offset < output.length && dashLiveQueue.length > 0) {
        var frame = dashLiveQueue[0];
        var take = Math.min(frame.length, output.length - offset);
        for (var i = 0; i < take; i += 1) output[offset + i] = frame[i];
        offset += take;
        if (take >= frame.length) {
          dashLiveQueue.shift();
        } else {
          dashLiveQueue[0] = frame.slice(take);
        }
      }
    };
    dashAudioNode.connect(dashAudioCtx.destination);
  } catch (_e) {
    dashAudioCtx = null;
    dashAudioNode = null;
  }
}
function tryUnlockAudio(reason) {
  ensureLiveAudioContext();
  if (!dashAudioCtx) return;
  if (dashAudioCtx.state === 'running') {
    dashAutoAudioUnlocked = true;
    return;
  }
  dashAudioCtx.resume().then(function() {
    dashAutoAudioUnlocked = true;
  }).catch(function() {});
}
function installAudioUnlockHooks() {
  ['click', 'keydown', 'touchstart', 'pointerdown'].forEach(function(name) {
    window.addEventListener(name, function() { tryUnlockAudio(name); }, {passive: true});
  });
  document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible') tryUnlockAudio('visible');
  });
}
function queueLiveFrame(bid, payload) {
  if (!bid) return;
  if (!dashLiveBid && !dashLivePaused) dashLiveBid = bid;
  if (dashLiveBid !== bid || dashLivePaused) return;
  ensureLiveAudioContext();
  if (!dashAudioCtx) return;
  if (dashAudioCtx.state === 'suspended') dashAudioCtx.resume().catch(function(){});
  dashLiveQueue.push(decodeUlawFrame(payload));
  if (dashLiveQueue.length > 120) dashLiveQueue.splice(0, dashLiveQueue.length - 120);
}
function isDesktopBridge() {
  return !!(window.__OPS_DESKTOP_CLIENT__ || window.pywebview);
}
function desktopApi() {
  if (!window.pywebview || !window.pywebview.api) return null;
  return window.pywebview.api;
}
function isLiveAudioMode(mode) {
  var token = String(mode || '').trim().toLowerCase();
  return token === 'live' || token === 'websocket' || token === 'mulaw' || token === 'ulaw' || token === 'rtp';
}
function updateAllDashButtons() {
  if (isDesktopBridge()) return;
  document.querySelectorAll('.msg-audio').forEach(function(wrapper) {
    var bid = wrapper.getAttribute('data-bid');
    var button = wrapper.querySelector('.msg-audio-btn');
    var playing = (!!bid && bid === dashLiveBid && !dashLivePaused) || (button && button === dashAudioButton && button.getAttribute('data-state') === 'playing');
    setDashButton(button, playing);
  });
}
window.__opsUpdateAudioState = function(bid, playing) {
  document.querySelectorAll('.msg-audio-btn').forEach(function(btn) {
    var wrapper = btn.closest('.msg-audio');
    var btnBid = wrapper ? wrapper.getAttribute('data-bid') : '';
    setDashButton(btn, !!(playing && btnBid && btnBid === bid));
  });
};
function playDashboardRecording(button, bid) {
  if (!bid) return;
  if (dashAudio) stopDashRecordingAudio();
  dashAudio = new Audio('/dashboard/broadcast-audio?bid=' + encodeURIComponent(bid));
  dashAudioButton = button;
  dashAudio.addEventListener('ended', function() { setDashButton(button, false); dashAudio = null; dashAudioButton = null; });
  dashAudio.addEventListener('error', function() { setDashButton(button, false); dashAudio = null; dashAudioButton = null; });
  dashAudio.play().then(function() {
    setDashButton(button, true);
  }).catch(function() {
    setDashButton(button, false);
  });
}
function toggleBrowserDashAudio(button, bid) {
  if (!bid) return;
  var isLive = !!(dashBroadcastMeta[bid] && dashBroadcastMeta[bid].live);
  if (isLive) {
    stopDashRecordingAudio();
    if (dashLiveBid === bid && !dashLivePaused) {
      stopDashLiveAudio();
      setDashButton(button, false);
      return;
    }
    dashLiveBid = bid;
    dashLiveManuallyPaused[bid] = false;
    dashLivePaused = false;
    tryUnlockAudio('manual-live');
    setDashButton(button, true);
    return;
  }
  stopDashLiveAudio();
  playDashboardRecording(button, bid);
}
function toggleDashAudio(button) {
  if (!button) return;
  var wrapper = button.closest('.msg-audio');
  var bid = wrapper ? wrapper.getAttribute('data-bid') : '';
  if (!bid) return;
  if (isDesktopBridge()) {
    var api = desktopApi();
    if (!api) return;
    var requestedState = button.getAttribute('data-state') !== 'playing';
    document.querySelectorAll('.msg-audio-btn').forEach(function(item) { setDashButton(item, false); });
    setDashButton(button, requestedState);
    api.dashboard_toggle_audio(bid).then(function(state) {
      var playing = !!(state && state.playing);
      document.querySelectorAll('.msg-audio-btn').forEach(function(item) { setDashButton(item, false); });
      setDashButton(button, playing);
    }).catch(function() {
      setDashButton(button, false);
    });
    return;
  }
  toggleBrowserDashAudio(button, bid);
  updateAllDashButtons();
}
function scheduleDashboardPoll() {
  var now = Date.now();
  if (now - dashLastPollAt < 300) return;
  dashLastPollAt = now;
  pollDashboard();
}
function pollDashboard() {
  if (dashPollInFlight) return;
  dashPollInFlight = true;
  var url = '/dashboard?poll=1&hash=' + encodeURIComponent(String(dashHash || ''));
  fetch(url, {credentials: 'same-origin'})
    .then(function(resp) { return resp.json(); })
    .then(function(data) {
      if (data && data.hash && data.hash !== dashHash) {
        dashHash = data.hash;
        var container = document.getElementById('dash-messages');
        container.innerHTML = data.html;
        formatDashTimestamps(container);
        updateAllDashButtons();
      }
    })
    .catch(function() {})
    .then(function() {
      dashPollInFlight = false;
    });
}
function notificationBody(payload) {
  var shortmessage = String(payload.shortmessage || '').trim();
  var longmessage = String(payload.longmessage || '').trim();
  if (shortmessage && longmessage) return shortmessage + '\n' + longmessage;
  return longmessage || shortmessage;
}
function notifyBroadcast(payload) {
  if (!dashNotificationEnabled || !payload) return;
  var bid = String(payload.broadcast_id || '').trim();
  if (bid) {
    if (dashSeenNotificationBids[bid]) return;
    rememberDashNotifiedBid(bid);
    if (payload.late) return;
  }
  var title = String(payload.shortmessage || payload.name || 'Broadcast').trim() || 'Broadcast';
  var body = notificationBody(payload);
  if (!body) return;
  var options = { body: body };
  if (bid) options.icon = '/dashboard/broadcast-icon?bid=' + encodeURIComponent(bid);
  try {
    new Notification(title, options);
  } catch (_e) {}
}
function parseWsBinary(packet) {
  if (!packet || packet.length < 33) return null;
  var packetType = String.fromCharCode(packet[0] || 0);
  var bidBytes = packet.slice(1, 33);
  var bid = '';
  for (var i = 0; i < bidBytes.length; i += 1) {
    if (bidBytes[i] === 32) continue;
    bid += String.fromCharCode(bidBytes[i]);
  }
  return { type: packetType, bid: bid.trim(), payload: packet.slice(33) };
}
function maybeAutoPlayIncoming(payload) {
  if (!payload || !payload.has_audio) return;
  var bid = String(payload.broadcast_id || '').trim();
  if (!bid || dashAutoPlayAttempted[bid]) return;
  dashAutoPlayAttempted[bid] = true;
  if (isDesktopBridge()) return;
  if (!isLiveAudioMode(payload.audio_mode)) return;
  tryUnlockAudio('incoming');
  if (dashBroadcastMeta[bid] && dashBroadcastMeta[bid].live) {
    dashLiveBid = bid;
    dashLivePaused = false;
    updateAllDashButtons();
  }
}
function onBroadcastMeta(payload) {
  var bid = String(payload.broadcast_id || '').trim();
  if (!bid) return;
  dashBroadcastMeta[bid] = dashBroadcastMeta[bid] || {};
  dashBroadcastMeta[bid].live = isLiveAudioMode(payload.audio_mode);
  notifyBroadcast(payload);
  scheduleDashboardPoll();
  maybeAutoPlayIncoming(payload);
}
function onRtpStreamControl(message) {
  if (!message) return;
  var bid = String(message.broadcast_id || '').trim();
  if (!bid) return;
  var command = String(message.command || '').trim().toLowerCase();
  dashBroadcastMeta[bid] = dashBroadcastMeta[bid] || {};
  if (command === 'start') {
    dashBroadcastMeta[bid].live = true;
    if (!dashLiveBid || dashLiveBid === bid) {
      dashLiveBid = bid;
      dashLivePaused = false;
    }
    tryUnlockAudio('rtp-start');
    updateAllDashButtons();
    return;
  }
  if (command === 'end') {
    dashBroadcastMeta[bid].live = false;
    delete dashLiveManuallyPaused[bid];
    if (dashLiveBid === bid) {
      dashLiveQueue = [];
      dashLivePaused = true;
    }
    updateAllDashButtons();
  }
}
function onBroadcastFrame(decoded) {
  if (!decoded || !decoded.bid) return;
  if (!dashBroadcastMeta[decoded.bid]) dashBroadcastMeta[decoded.bid] = {};
  if (decoded.type === 'A') {
    dashBroadcastMeta[decoded.bid].live = true;
    if (!dashLiveBid) {
      dashLiveBid = decoded.bid;
      dashLivePaused = false;
    } else if (dashLiveBid === decoded.bid && dashLivePaused && !dashLiveManuallyPaused[decoded.bid]) {
      dashLivePaused = false;
    }
    queueLiveFrame(decoded.bid, decoded.payload || new Uint8Array(0));
    updateAllDashButtons();
    return;
  }
  if (decoded.type === 'E') {
    dashBroadcastMeta[decoded.bid].live = false;
    delete dashLiveManuallyPaused[decoded.bid];
    if (dashLiveBid === decoded.bid) {
      dashLiveQueue = [];
      dashLivePaused = true;
    }
    updateAllDashButtons();
    scheduleDashboardPoll();
  }
}
function connectDashboardWebSocket() {
  if (dashWs) {
    try { dashWs.close(); } catch (_e) {}
    dashWs = null;
  }
  if (dashWsPingTimer) {
    clearInterval(dashWsPingTimer);
    dashWsPingTimer = null;
  }
  fetch('/dashboard/ws-session', {credentials: 'same-origin'})
    .then(function(resp) { return resp.ok ? resp.json() : null; })
    .then(function(info) {
      if (!info || !info.token || !info.websocket_path) return;
      dashWsToken = String(info.token);
      var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      var url = proto + window.location.host + info.websocket_path + '?token=' + encodeURIComponent(dashWsToken) + '&source=web';
      dashWs = new WebSocket(url);
      dashWs.binaryType = 'arraybuffer';
      dashWs.onopen = function() {
        if (dashWsPingTimer) clearInterval(dashWsPingTimer);
        dashWsPingTimer = setInterval(function() {
          try {
            if (dashWs && dashWs.readyState === WebSocket.OPEN) {
              dashWs.send(JSON.stringify({type: 'ping'}));
            }
          } catch (_e) {}
        }, 20000);
      };
      dashWs.onmessage = function(event) {
        if (typeof event.data === 'string') {
          try {
            var message = JSON.parse(event.data);
            if (message && message.type === 'broadcast') onBroadcastMeta(message);
            if (message && message.type === 'rtp_stream') onRtpStreamControl(message);
          } catch (_e) {}
          return;
        }
        var view = new Uint8Array(event.data || new ArrayBuffer(0));
        onBroadcastFrame(parseWsBinary(view));
      };
      dashWs.onclose = function() {
        if (dashWsPingTimer) {
          clearInterval(dashWsPingTimer);
          dashWsPingTimer = null;
        }
        setTimeout(connectDashboardWebSocket, 1500);
      };
      dashWs.onerror = function() {};
    })
    .catch(function() {
      setTimeout(connectDashboardWebSocket, 2500);
    });
}
function setupNotifications() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    dashNotificationEnabled = true;
    return;
  }
  if (Notification.permission !== 'default') return;
  Notification.requestPermission().then(function(result) {
    dashNotificationEnabled = (result === 'granted');
  }).catch(function() {});
}
formatDashTimestamps(document);
updateAllDashButtons();
if (isDesktopBridge()) {
  scheduleDashboardPoll();
  setInterval(scheduleDashboardPoll, 2000);
} else {
  setupNotifications();
  installAudioUnlockHooks();
  tryUnlockAudio('load');
  connectDashboardWebSocket();
}
"""


def handle_request():
    user = current_user()
    if isinstance(user, dict) and user:
        guarded = require_user()
        if not isinstance(guarded, dict):
            return guarded
        ctx = legacy_user_context(guarded)
        user_id = guarded.get("id")
        heading = f'Hey there, <span id="extension-name">{h(ctx.get("username") or "User")}</span>'
    else:
        if not guest_receiver_enabled():
            return redirect("/login")
        ctx = legacy_guest_context()
        user_id = GUEST_MEMBER_TOKEN
        heading = "Welcome"
    records = _active_records_for(user_id)
    digest = _records_digest(records)
    if request.args.get("poll") == "1":
        if str(request.args.get("hash") or "").strip() == digest:
            return jsonify(hash=digest)
        return jsonify(hash=digest, html=_cards_html(records))
    cards = _cards_html(records)
    content = f'<h1>{heading}</h1>\n<div id="dash-messages">{cards}</div>'
    desktop_mode = (
        str(request.args.get("desktop_client") or "").strip().lower() in {"1", "true", "yes", "on"}
        or bool(session.get("desktop_client"))
    )
    script = ("window.__OPS_DESKTOP_CLIENT__ = true;" if desktop_mode else "") + f'window.__DASH_HASH__ = "{digest}";' + DASHBOARD_SCRIPT
    return legacy_page("Dashboard", ctx, "dashboard", DASHBOARD_STYLE, content, script)
