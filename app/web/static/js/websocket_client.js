/**
 * WebSocket Real-Time Client for NAS File Monitoring System
 * Connects to /ws and updates dashboard metrics, scan status, and event tables in real time
 * without requiring any browser reload.
 * Author: Mostasim Mahmud Tasim
 */

(function () {
  let socket = null;
  let reconnectTimer = null;
  let pingTimer = null;
  let lastKnownScanId = null;

  function updateStatusBadge(status) {
    const badge = document.getElementById("wsStatusBadge");
    const text = document.getElementById("wsStatusText");
    if (!badge || !text) return;

    if (status === "connected") {
      badge.className = "badge bg-success-subtle text-success border border-success-subtle d-flex align-items-center gap-1";
      badge.innerHTML = `<span class="spinner-grow spinner-grow-sm text-success" style="width: 7px; height: 7px;" role="status"></span> <span id="wsStatusText">WebSocket Live</span>`;
    } else if (status === "connecting") {
      badge.className = "badge bg-warning-subtle text-warning border border-warning-subtle d-flex align-items-center gap-1";
      badge.innerHTML = `<span class="spinner-border spinner-border-sm text-warning" style="width: 7px; height: 7px;" role="status"></span> <span id="wsStatusText">Connecting...</span>`;
    } else {
      badge.className = "badge bg-danger-subtle text-danger border border-danger-subtle d-flex align-items-center gap-1";
      badge.innerHTML = `<span class="spinner-grow spinner-grow-sm text-danger" style="width: 7px; height: 7px;" role="status"></span> <span id="wsStatusText">Reconnecting...</span>`;
    }
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    updateStatusBadge("connecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;

    try {
      socket = new WebSocket(wsUrl);
    } catch (e) {
      console.warn("WebSocket init error:", e);
      scheduleReconnect();
      return;
    }

    socket.onopen = function () {
      console.log("[WebSocket] Connected to NAS live stream.");
      updateStatusBadge("connected");

      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }

      // Start ping heartbeat
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send("ping");
        }
      }, 15000);
    };

    socket.onmessage = function (event) {
      if (event.data === "pong") return;

      try {
        const payload = JSON.parse(event.data);
        handleServerEvent(payload);
      } catch (err) {
        console.debug("[WebSocket] Non-JSON message:", event.data);
      }
    };

    socket.onerror = function (err) {
      console.warn("[WebSocket] Error occurred:", err);
    };

    socket.onclose = function (event) {
      console.warn("[WebSocket] Connection closed (code: " + event.code + "). Reconnecting...");
      updateStatusBadge("disconnected");
      if (pingTimer) clearInterval(pingTimer);
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 2000);
  }

  function handleServerEvent(msg) {
    if (!msg) return;

    const data = msg.data || msg;
    const stats = data.stats;
    const latestScan = data.latest_scan;
    const recentEvents = data.recent_events;

    // 1. Update Dashboard Metric Counter Cards
    if (stats) {
      updateValueWithPulse("stat-total-files", stats.total_files);
      updateValueWithPulse("stat-new-files", stats.new_files);
      updateValueWithPulse("stat-existing-files", stats.existing_files);
      updateValueWithPulse("stat-removed-files", stats.removed_files);

      const dupElem = document.getElementById("stat-duplicate-files");
      if (dupElem) {
        const dupHtml = `${stats.duplicate_files} <span class="fs-6 text-muted fw-normal">(${stats.duplicate_groups} groups)</span>`;
        if (dupElem.innerHTML.trim() !== dupHtml.trim()) {
          dupElem.innerHTML = dupHtml;
          pulseElement(dupElem);
        }
      }
    }

    // 2. Update Latest Scan Status Box
    if (latestScan) {
      const scanIdElem = document.getElementById("scan-id-val");
      if (scanIdElem) {
        scanIdElem.textContent = `#${latestScan.id} (${latestScan.trigger_type || "SCHEDULED"})`;
      }

      const scanBadge = document.getElementById("scan-status-badge");
      if (scanBadge) {
        scanBadge.textContent = latestScan.status;
        scanBadge.className = `badge ${latestScan.status === "COMPLETED" ? "bg-success" : latestScan.status === "RUNNING" ? "bg-primary" : "bg-danger"}`;
      }

      if (typeof formatDhaka12h === "function") {
        const startedElem = document.getElementById("scan-started-val");
        if (startedElem && latestScan.started_at) {
          startedElem.textContent = formatDhaka12h(latestScan.started_at);
        }

        const compElem = document.getElementById("scan-completed-val");
        if (compElem && latestScan.completed_at) {
          compElem.textContent = formatDhaka12h(latestScan.completed_at);
        }
      }

      const durElem = document.getElementById("scan-duration-val");
      if (durElem && latestScan.duration_seconds !== null && latestScan.duration_seconds !== undefined) {
        durElem.textContent = `${Number(latestScan.duration_seconds).toFixed(2)}s`;
      }

      const totScanElem = document.getElementById("scan-total-val");
      if (totScanElem) totScanElem.textContent = latestScan.total_files;

      const newScanElem = document.getElementById("scan-new-val");
      if (newScanElem) newScanElem.textContent = `+${latestScan.new_files_count || 0}`;

      const existScanElem = document.getElementById("scan-existing-val");
      if (existScanElem) existScanElem.textContent = `${latestScan.existing_files_count || 0}`;

      const remScanElem = document.getElementById("scan-removed-val");
      if (remScanElem) remScanElem.textContent = `-${latestScan.removed_files_count || 0}`;
    }

    // 3. Update Recent File Activity Table
    if (Array.isArray(recentEvents)) {
      renderRecentEventsTable(recentEvents);
    }

    // 4. If Scan changed and we are on a list/subpage (e.g. /files/duplicates, /files/new, etc.)
    const currentScanId = latestScan ? latestScan.id : null;
    const scanChanged = lastKnownScanId !== null && currentScanId !== null && lastKnownScanId !== currentScanId;
    lastKnownScanId = currentScanId;

    if (scanChanged && !document.getElementById("stat-total-files")) {
      // We are on a subpage! Automatically refresh subpage content seamlessly
      refreshSubpageContent();
    }

    // Show toast for newly completed scan if not initial connection
    if (msg.type === "SCAN_COMPLETED" && typeof showToast === "function") {
      const newCount = msg.new_count || (latestScan ? latestScan.new_files_count : 0);
      const totalCount = msg.total_count || (stats ? stats.total_files : 0);
      showToast(
        "Live Scan Update",
        `Scan #${msg.scan_id || ""} completed! Total: ${totalCount} files${newCount > 0 ? ` (+${newCount} new)` : ""}.`,
        "info"
      );
    }
  }

  function updateValueWithPulse(elemId, val) {
    if (val === undefined || val === null) return;
    const elem = document.getElementById(elemId);
    if (!elem) return;

    const strVal = String(val);
    if (elem.textContent.trim() !== strVal) {
      elem.textContent = strVal;
      pulseElement(elem);
    }
  }

  function pulseElement(elem) {
    elem.classList.add("text-primary", "fw-bolder");
    setTimeout(() => {
      elem.classList.remove("text-primary", "fw-bolder");
    }, 1200);
  }

  function renderRecentEventsTable(events) {
    const tbody = document.getElementById("recent-events-tbody");
    if (!tbody || events.length === 0) return;

    const rowsHtml = events.slice(0, 10).map((ev) => {
      const eventTypeClass = String(ev.event_type || "").toLowerCase();
      const sizeStr = ev.file_size !== null && ev.file_size !== undefined
        ? `${(ev.file_size / 1024).toFixed(1)} KB`
        : "-";
      const formattedTime = typeof formatDhaka12h === "function" ? formatDhaka12h(ev.event_time) : (ev.event_time || "-");

      return `
        <tr>
          <td>
            <span class="badge-status badge-${eventTypeClass}">
              ${ev.event_type}
            </span>
          </td>
          <td>
            <div class="fw-semibold text-truncate" style="max-width: 320px;" title="${ev.file_name || ""}">
              <a href="/files/${ev.file_id || ""}" class="text-decoration-none text-dark">
                ${ev.file_name || "Unknown"}
              </a>
            </div>
            <div class="text-muted font-monospace small text-truncate" style="max-width: 320px;" title="${ev.file_path || ""}">
              ${ev.file_path || ""}
            </div>
          </td>
          <td>${sizeStr}</td>
          <td class="text-muted font-monospace small">
            ${formattedTime}
          </td>
        </tr>
      `;
    }).join("");

    if (tbody.innerHTML.trim() !== rowsHtml.trim()) {
      tbody.innerHTML = rowsHtml;
    }
  }

  async function refreshSubpageContent() {
    if (window.location.pathname.startsWith("/websocket-api")) {
      return;
    }
    try {
      const res = await fetch(window.location.href, { cache: "no-store" });
      if (!res.ok) return;

      const html = await res.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");

      const newMain = doc.querySelector("main");
      const currentMain = document.querySelector("main");

      if (newMain && currentMain) {
        currentMain.innerHTML = newMain.innerHTML;
      }
    } catch (e) {
      console.debug("Subpage sync failed:", e);
    }
  }

  // Initialize WebSocket when document is loaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
