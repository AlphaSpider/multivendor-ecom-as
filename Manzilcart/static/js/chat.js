/*
 * Conversation socket client.
 *
 * - The server is the source of truth: messages are only rendered after the socket
 *   echoes the committed database row.
 * - Every send carries a UUID. Retries reuse it, so a reconnect cannot duplicate a
 *   message (the server returns the original row instead of creating another).
 * - If the socket is down, messages stay queued in memory and are flushed on reconnect.
 */
(function () {
  "use strict";

  var log = document.getElementById("chat-log");
  if (!log) return;

  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var submit = document.getElementById("chat-submit");
  var statusEl = document.getElementById("chat-status");
  var mobileStatusEl = document.getElementById("chat-mobile-status");
  var hint = document.getElementById("chat-hint");

  var conversationId = log.dataset.conversationId;
  var userId = parseInt(log.dataset.userId, 10);
  var maxLength = parseInt(log.dataset.messageLength, 10) || 2000;
  var timeZone = log.dataset.timezone || "";

  var socket = null;
  var attempts = 0;
  var reconnectTimer = null;
  var leaving = false;
  var pending = new Map(); // client_message_id -> body

  function setStatus(text, state) {
    [statusEl, mobileStatusEl].forEach(function (el) {
      if (!el) return;
      el.textContent = text;
      el.dataset.state = state || "";
    });
  }

  function socketUrl() {
    var scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    return scheme + "//" + window.location.host + "/ws/conversations/" + conversationId + "/";
  }

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = Array.prototype.map.call(bytes, function (b) {
      return b.toString(16).padStart(2, "0");
    });
    return (
      hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
      hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
      hex.slice(10, 16).join("")
    );
  }

  function scrollToLatest() {
    log.scrollTop = log.scrollHeight;
  }

  function formatTime(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat([], {
        timeZone: timeZone || undefined,
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
    } catch (err) {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
  }

  function dayKey(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: timeZone || undefined,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(date);
    } catch (err) {
      return date.toISOString().slice(0, 10);
    }
  }

  function dayLabel(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    if (dayKey(iso) === dayKey(new Date().toISOString())) return "Today";
    try {
      return new Intl.DateTimeFormat("en-GB", {
        timeZone: timeZone || undefined,
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(date);
    } catch (err) {
      return date.toDateString();
    }
  }

  function appendMessage(message) {
    var id = String(message.id);
    if (document.getElementById("message-" + id)) return;

    var empty = log.querySelector(".chat-empty");
    if (empty) empty.remove();

    var key = dayKey(message.created_at);
    var lastBubble = log.querySelector(".chat-bubble:last-of-type");
    if (!lastBubble || lastBubble.dataset.chatDay !== key) {
      var day = document.createElement("li");
      day.className = "chat-day";
      day.dataset.chatDay = key;
      var dayText = document.createElement("span");
      dayText.textContent = dayLabel(message.created_at);
      day.appendChild(dayText);
      log.appendChild(day);
    }

    var item = document.createElement("li");
    item.id = "message-" + id;
    item.dataset.messageId = id;
    item.dataset.senderId = String(message.sender_id);
    item.dataset.chatDay = key;
    item.className = "chat-bubble " + (message.sender_id === userId ? "is-mine" : "is-theirs");

    var author = document.createElement("span");
    author.className = "chat-author";
    author.textContent = message.sender_id === userId ? "You" : (message.sender_name || "ManzilCart member");

    var body = document.createElement("div");
    body.className = "chat-body";
    body.textContent = message.body;

    var time = document.createElement("time");
    time.className = "chat-time";
    time.dateTime = message.created_at || "";

    var relative = document.createElement("span");
    relative.className = "chat-time-relative";
    relative.textContent = "just now";

    var clock = document.createElement("span");
    clock.className = "chat-time-clock";
    clock.textContent = formatTime(message.created_at);

    time.appendChild(relative);
    time.appendChild(clock);

    if (message.sender_id === userId) {
      time.insertAdjacentHTML(
        "beforeend",
        '<svg class="chat-tick" width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
          'stroke="currentColor" stroke-width="3" stroke-linecap="round" ' +
          'stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>'
      );
    }

    item.appendChild(author);
    item.appendChild(body);
    item.appendChild(time);
    log.appendChild(item);

    var nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 160;
    if (nearBottom || message.sender_id === userId) scrollToLatest();
  }

  function sendOne(clientId, body) {
    socket.send(
      JSON.stringify({ type: "message.send", body: body, client_message_id: clientId })
    );
  }

  function flush() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    pending.forEach(function (body, clientId) {
      sendOne(clientId, body);
    });
    setStatus(pending.size ? "Sending…" : "Connected", pending.size ? "sending" : "open");
  }

  function handle(data) {
    if (!data || typeof data !== "object") return;

    if (data.type === "ready") {
      attempts = 0;
      setStatus(pending.size ? "Sending…" : "Connected", pending.size ? "sending" : "open");
      flush();
      return;
    }

    if (data.type === "message" && data.message) {
      appendMessage(data.message);
      return;
    }

    if (data.type === "message.ack") {
      if (data.client_message_id) pending.delete(data.client_message_id);
      if (!pending.size) setStatus("Connected", "open");
      if (submit) submit.disabled = false;
      return;
    }

    if (data.type === "error") {
      if (data.client_message_id) pending.delete(data.client_message_id);
      if (hint && data.error) hint.textContent = data.error;
      setStatus(pending.size ? "Retrying…" : "Connected", pending.size ? "sending" : "open");
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null || leaving) return;
    attempts += 1;
    var delay = Math.min(30000, 1000 * Math.pow(2, Math.min(attempts, 5)));
    reconnectTimer = window.setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (leaving) return;
    setStatus("Connecting…", "connecting");

    try {
      socket = new WebSocket(socketUrl());
    } catch (err) {
      scheduleReconnect();
      return;
    }

    socket.addEventListener("open", function () {
      attempts = 0;
      setStatus("Connected", "open");
      flush();
    });

    socket.addEventListener("message", function (event) {
      var data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      handle(data);
    });

    socket.addEventListener("close", function () {
      if (leaving) return;
      setStatus("Reconnecting…", "reconnecting");
      scheduleReconnect();
    });

    socket.addEventListener("error", function () {
      try {
        socket.close();
      } catch (err) {
        /* the close handler schedules the reconnect */
      }
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var body = (input.value || "").trim();
    if (!body) return;
    if (body.length > maxLength) {
      if (hint) hint.textContent = "Messages are limited to " + maxLength + " characters.";
      return;
    }

    var clientId = uuid();
    pending.set(clientId, body);
    input.value = "";
    if (hint) hint.textContent = "Messages are saved. If the connection drops, your unsent message is retried automatically.";

    if (socket && socket.readyState === WebSocket.OPEN) {
      sendOne(clientId, body);
      setStatus("Sending…", "sending");
    } else {
      setStatus("Queued — reconnecting…", "reconnecting");
      if (!socket || socket.readyState === WebSocket.CLOSED) scheduleReconnect();
    }
    input.focus();
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    }
  });

  window.addEventListener("beforeunload", function () {
    leaving = true;
    if (socket) socket.close();
  });

  scrollToLatest();
  connect();
})();
