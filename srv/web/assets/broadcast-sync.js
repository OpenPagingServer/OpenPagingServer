(function () {
  if (window.__opsGlobalBroadcastSync) return;
  if (window.pywebview || window.__OPS_DESKTOP_CLIENT__) return;
  var page = "";
  try {
    page = String((document.body && document.body.getAttribute("data-page")) || "").toLowerCase();
  } catch (_e) {}
  if (page === "dashboard") return;

  var sync = {
    ws: null,
    pingTimer: null,
    reconnectTimer: null,
    liveBid: "",
    livePaused: false,
    liveQueue: [],
    audioCtx: null,
    audioNode: null,
    notificationEnabled: false,
    seenNotifications: {},
    bootstrapped: false,
  };
  window.__opsGlobalBroadcastSync = sync;

  var SEEN_NOTIFY_KEY = "ops-web-notified-v1";
  try {
    var raw = localStorage.getItem(SEEN_NOTIFY_KEY);
    if (raw) {
      JSON.parse(raw).forEach(function (item) {
        var bid = String(item || "").trim();
        if (bid) sync.seenNotifications[bid] = true;
      });
    }
  } catch (_e) {}

  function rememberNotifiedBid(bid) {
    var token = String(bid || "").trim();
    if (!token || sync.seenNotifications[token]) return;
    sync.seenNotifications[token] = true;
    try {
      var keys = Object.keys(sync.seenNotifications);
      if (keys.length > 400) keys = keys.slice(keys.length - 400);
      localStorage.setItem(SEEN_NOTIFY_KEY, JSON.stringify(keys));
    } catch (_e) {}
  }

  function ulawByteToFloat(byte) {
    var u = (~byte) & 0xff;
    var sign = u & 0x80;
    var exponent = (u >> 4) & 0x07;
    var mantissa = u & 0x0f;
    var sample = ((mantissa << 3) + 0x84) << exponent;
    sample = sign ? 0x84 - sample : sample - 0x84;
    return Math.max(-1, Math.min(1, sample / 32768));
  }

  function decodeUlawFrame(payload) {
    var out = new Float32Array(payload.length);
    for (var i = 0; i < payload.length; i += 1) out[i] = ulawByteToFloat(payload[i]);
    return out;
  }

  function ensureLiveAudioContext() {
    if (sync.audioCtx && sync.audioNode) return;
    var AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return;
    sync.audioCtx = new AudioContextCtor();
    var node = sync.audioCtx.createScriptProcessor(1024, 0, 1);
    node.onaudioprocess = function (event) {
      var output = event.outputBuffer.getChannelData(0);
      output.fill(0);
      if (sync.livePaused || !sync.liveQueue.length) return;
      var frame = sync.liveQueue.shift();
      if (!frame) return;
      var len = Math.min(output.length, frame.length);
      for (var i = 0; i < len; i += 1) output[i] = frame[i];
    };
    node.connect(sync.audioCtx.destination);
    sync.audioNode = node;
  }

  function tryUnlockAudio() {
    ensureLiveAudioContext();
    if (sync.audioCtx && sync.audioCtx.state === "suspended") {
      sync.audioCtx.resume().catch(function () {});
    }
  }

  ["click", "keydown", "touchstart", "pointerdown"].forEach(function (name) {
    window.addEventListener(
      name,
      function () {
        tryUnlockAudio();
      },
      { passive: true }
    );
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") tryUnlockAudio();
  });

  function queueLiveFrame(bid, payload) {
    if (!bid) return;
    if (!sync.liveBid) sync.liveBid = bid;
    if (sync.liveBid !== bid || sync.livePaused) return;
    ensureLiveAudioContext();
    if (!sync.audioCtx) return;
    if (sync.audioCtx.state === "suspended") sync.audioCtx.resume().catch(function () {});
    sync.liveQueue.push(decodeUlawFrame(payload));
    if (sync.liveQueue.length > 120) sync.liveQueue.splice(0, sync.liveQueue.length - 120);
  }

  function notificationBody(payload) {
    var shortmessage = String(payload.shortmessage || "").trim();
    var longmessage = String(payload.longmessage || "").trim();
    if (shortmessage && longmessage) return shortmessage + "\n" + longmessage;
    return longmessage || shortmessage;
  }

  function maybeNotify(payload) {
    if (!sync.notificationEnabled || !payload) return;
    var bid = String(payload.broadcast_id || "").trim();
    if (bid) {
      if (sync.seenNotifications[bid]) return;
      rememberNotifiedBid(bid);
      if (payload.late) return;
    } else if (!sync.bootstrapped) {
      return;
    }
    var title = String(payload.shortmessage || payload.name || "Broadcast").trim() || "Broadcast";
    var body = notificationBody(payload);
    if (!body) return;
    var options = { body: body };
    if (bid) options.icon = "/dashboard/broadcast-icon?bid=" + encodeURIComponent(bid);
    try {
      new Notification(title, options);
    } catch (_e) {}
  }

  function maybeAutoPlayIncoming(payload) {
    if (!payload || !payload.has_audio || payload.late) return;
    var bid = String(payload.broadcast_id || "").trim();
    if (!bid) return;
    var mode = String(payload.audio_mode || "").toLowerCase();
    if (mode === "live" || mode === "websocket" || mode === "mulaw" || mode === "ulaw") {
      sync.liveBid = bid;
      sync.livePaused = false;
      tryUnlockAudio();
    }
  }

  function parseWsBinary(packet) {
    if (!packet || packet.length < 33) return null;
    var packetType = String.fromCharCode(packet[0] || 0);
    var bidBytes = packet.slice(1, 33);
    var bid = "";
    for (var i = 0; i < bidBytes.length; i += 1) {
      if (bidBytes[i] === 32) continue;
      bid += String.fromCharCode(bidBytes[i]);
    }
    return { type: packetType, bid: bid.trim(), payload: packet.slice(33) };
  }

  function onBroadcastMeta(payload) {
    maybeNotify(payload);
    maybeAutoPlayIncoming(payload);
  }

  function onBroadcastFrame(decoded) {
    if (!decoded || !decoded.bid) return;
    if (decoded.type === "A") {
      queueLiveFrame(decoded.bid, decoded.payload || new Uint8Array(0));
      return;
    }
    if (decoded.type === "E" && sync.liveBid === decoded.bid) {
      sync.liveQueue = [];
      sync.livePaused = true;
    }
  }

  function clearReconnectTimer() {
    if (!sync.reconnectTimer) return;
    clearTimeout(sync.reconnectTimer);
    sync.reconnectTimer = null;
  }

  function scheduleReconnect(delay) {
    if (sync.reconnectTimer) return;
    sync.reconnectTimer = setTimeout(function () {
      sync.reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    clearReconnectTimer();
    if (sync.ws) {
      try {
        sync.ws.close();
      } catch (_e) {}
      sync.ws = null;
    }
    if (sync.pingTimer) {
      clearInterval(sync.pingTimer);
      sync.pingTimer = null;
    }
    fetch("/dashboard/ws-session", { credentials: "same-origin" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (info) {
        if (!info || !info.token || !info.websocket_path) {
          scheduleReconnect(2500);
          return;
        }
        var proto = window.location.protocol === "https:" ? "wss://" : "ws://";
        var url = proto + window.location.host + info.websocket_path + "?token=" + encodeURIComponent(String(info.token)) + "&source=web";
        sync.ws = new WebSocket(url);
        sync.ws.binaryType = "arraybuffer";
        sync.ws.onopen = function () {
          sync.bootstrapped = true;
          if (sync.pingTimer) clearInterval(sync.pingTimer);
          sync.pingTimer = setInterval(function () {
            try {
              if (sync.ws && sync.ws.readyState === WebSocket.OPEN) sync.ws.send(JSON.stringify({ type: "ping" }));
            } catch (_e) {}
          }, 20000);
        };
        sync.ws.onmessage = function (event) {
          if (typeof event.data === "string") {
            try {
              var message = JSON.parse(event.data);
              if (message && message.type === "broadcast") onBroadcastMeta(message);
            } catch (_e) {}
            return;
          }
          var view = new Uint8Array(event.data || new ArrayBuffer(0));
          onBroadcastFrame(parseWsBinary(view));
        };
        sync.ws.onclose = function () {
          if (sync.pingTimer) {
            clearInterval(sync.pingTimer);
            sync.pingTimer = null;
          }
          sync.ws = null;
          scheduleReconnect(1500);
        };
        sync.ws.onerror = function () {};
      })
      .catch(function () {
        scheduleReconnect(2500);
      });
  }

  if ("Notification" in window) {
    if (Notification.permission === "granted") {
      sync.notificationEnabled = true;
    } else if (Notification.permission === "default") {
      Notification.requestPermission()
        .then(function (result) {
          sync.notificationEnabled = result === "granted";
        })
        .catch(function () {});
    }
  }

  connect();
})();
