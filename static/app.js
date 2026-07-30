document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("refresh-status");
  if (btn) {
    btn.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const pill = document.querySelector(".status-pill");
        if (pill) {
          pill.textContent = (data.status || "idle").toUpperCase();
          pill.className = `status-pill status-${data.status || "idle"}`;
        }
      } catch (err) {
        console.error(err);
      }
    });
  }

  // Auto-refresh dashboard status every 5s
  if (document.querySelector(".status-pill")) {
    setInterval(async () => {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const pill = document.querySelector(".status-pill");
        if (pill && data.status) {
          pill.textContent = String(data.status).toUpperCase();
          pill.className = `status-pill status-${data.status}`;
        }
      } catch (_) {
        /* ignore */
      }
    }, 5000);
  }
});
