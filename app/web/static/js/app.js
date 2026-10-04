/**
 * JavaScript helpers for NAS File Monitoring & Duplicate Tracking System
 */

/**
 * Format an ISO date string or timestamp into Asia/Dhaka (+06:00) 12-hour format:
 * YYYY-MM-DD hh:mm:ss AM/PM
 * Example: 2026-10-03 04:12:23 PM
 */
function formatDhaka12h(val) {
  if (!val || val === "-" || val === "N/A") return "-";
  try {
    let date;
    if (typeof val === "string") {
      let s = val.trim();
      if (!s.endsWith("Z") && !s.includes("+") && !s.slice(10).includes("-")) {
        s = s.replace(" ", "T") + "Z";
      }
      date = new Date(s);
    } else if (val instanceof Date) {
      date = val;
    } else if (typeof val === "number") {
      date = new Date(val);
    } else {
      return "-";
    }

    if (isNaN(date.getTime())) return String(val);

    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Dhaka",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });

    const parts = formatter.formatToParts(date);
    const p = {};
    for (const part of parts) {
      p[part.type] = part.value;
    }
    return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${p.dayPeriod}`;
  } catch (e) {
    return String(val);
  }
}
window.formatDhaka12h = formatDhaka12h;

document.addEventListener("DOMContentLoaded", function () {
  // Initialize tooltips if Bootstrap is available
  if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
      return new bootstrap.Tooltip(tooltipTriggerEl);
    });
  }

  // Setup manual scan button trigger
  const scanBtn = document.getElementById("trigger-scan-btn");
  if (scanBtn) {
    scanBtn.addEventListener("click", function (e) {
      e.preventDefault();
      triggerScanNow(this);
    });
  }
});

/**
 * Triggers an immediate manual scan via POST /api/scan
 */
async function triggerScanNow(buttonElement) {
  const originalHtml = buttonElement.innerHTML;
  buttonElement.disabled = true;
  buttonElement.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> Scanning...`;

  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });

    if (response.status === 409) {
      showToast("Scan in Progress", "A scan is already actively running. Please wait.", "warning");
      return;
    }

    if (!response.ok) {
      const errData = await response.json().catch(() => ({ detail: "Unknown error occurred" }));
      showToast("Scan Failed", errData.detail || "Error triggering scan.", "danger");
      return;
    }

    const result = await response.json();
    showToast(
      "Scan Completed",
      `Scan finished! Total: ${result.total_files}, New: ${result.new_count}, Duplicates: ${result.duplicate_count}`,
      "success"
    );

    // Reload page after brief delay to reflect updated data
    setTimeout(() => {
      window.location.reload();
    }, 1200);
  } catch (error) {
    console.error("Scan trigger failed:", error);
    showToast("Network Error", "Failed to communicate with scanner server.", "danger");
  } finally {
    setTimeout(() => {
      buttonElement.disabled = false;
      buttonElement.innerHTML = originalHtml;
    }, 1500);
  }
}

/**
 * Helper to copy SHA-256 hash or text to clipboard
 */
function copyToClipboard(text, btnElement) {
  navigator.clipboard.writeText(text).then(
    function () {
      if (btnElement) {
        const originalTitle = btnElement.getAttribute("title");
        btnElement.classList.add("text-success");
        setTimeout(() => {
          btnElement.classList.remove("text-success");
        }, 1500);
      }
      showToast("Copied", "Hash copied to clipboard", "info");
    },
    function (err) {
      console.error("Could not copy text: ", err);
    }
  );
}

/**
 * Helper to display bootstrap toast notifications dynamically
 */
function showToast(title, message, variant = "info") {
  const container = document.getElementById("toast-container");
  if (!container) {
    alert(`${title}: ${message}`);
    return;
  }

  const toastId = "toast-" + Date.now();
  const bgClass = variant === "danger" ? "bg-danger text-white" : variant === "success" ? "bg-success text-white" : variant === "warning" ? "bg-warning text-dark" : "bg-primary text-white";

  const toastHtml = `
    <div id="${toastId}" class="toast align-items-center ${bgClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
      <div class="d-flex">
        <div class="toast-body">
          <strong>${title}:</strong> ${message}
        </div>
        <button type="button" class="btn-close ${variant === "warning" ? "" : "btn-close-white"} me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
    </div>
  `;

  container.insertAdjacentHTML("beforeend", toastHtml);
  const toastElem = document.getElementById(toastId);
  if (typeof bootstrap !== "undefined" && bootstrap.Toast) {
    const bsToast = new bootstrap.Toast(toastElem, { delay: 4000 });
    bsToast.show();
    toastElem.addEventListener("hidden.bs.toast", () => toastElem.remove());
  }
}
