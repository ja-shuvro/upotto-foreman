/** Lightweight Markdown → HTML (no CDN dependency). */
export function renderMarkdown(src) {
  if (!src) return "";
  let text = String(src).replace(/\r\n/g, "\n");

  // fenced code
  text = text.replace(/```[\w.-]*\n([\s\S]*?)```/g, (_, code) => {
    return `<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`;
  });

  const lines = text.split("\n");
  const out = [];
  let inList = false;

  for (let line of lines) {
    if (/^###\s+/.test(line)) {
      closeList();
      out.push(`<h3>${inline(line.replace(/^###\s+/, ""))}</h3>`);
      continue;
    }
    if (/^##\s+/.test(line)) {
      closeList();
      out.push(`<h2>${inline(line.replace(/^##\s+/, ""))}</h2>`);
      continue;
    }
    if (/^#\s+/.test(line)) {
      closeList();
      out.push(`<h1>${inline(line.replace(/^#\s+/, ""))}</h1>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      if (!inList) {
        out.push("<ol>");
        inList = true;
      }
      out.push(`<li>${inline(line.replace(/^\d+\.\s+/, ""))}</li>`);
      continue;
    }
    closeList();
    if (!line.trim()) {
      out.push("");
      continue;
    }
    if (line.startsWith("<pre>")) {
      out.push(line);
      continue;
    }
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("\n");

  function closeList() {
    if (inList) {
      out.push(out[out.length - 1]?.includes("<ol>") || out.some((x) => false) ? "</ol>" : "</ul>");
      // fix: track list type properly
      inList = false;
    }
  }

  function inline(s) {
    let t = escapeHtml(s);
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/__(.+?)__/g, "<strong>$1</strong>");
    t = t.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>");
    return t;
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Fixed list closing for ul/ol */
export function renderMarkdownSafe(src) {
  if (!src) return "";
  let text = String(src).replace(/\r\n/g, "\n");
  text = text.replace(/```[\w.-]*\n([\s\S]*?)```/g, (_, code) => {
    return `\n\nPREBLOCK${btoa(unescape(encodeURIComponent(code.replace(/\n$/, ""))))}PREBLOCK\n\n`;
  });

  const lines = text.split("\n");
  const out = [];
  let listType = null;

  const close = () => {
    if (listType) {
      out.push(listType === "ol" ? "</ol>" : "</ul>");
      listType = null;
    }
  };

  const inline = (s) => {
    let t = escapeHtml(s);
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/__(.+?)__/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    return t;
  };

  for (const line of lines) {
    const pre = line.match(/^PREBLOCK(.+)PREBLOCK$/);
    if (pre) {
      close();
      try {
        const code = decodeURIComponent(escape(atob(pre[1])));
        out.push(`<pre><code>${escapeHtml(code)}</code></pre>`);
      } catch {
        out.push(`<pre><code></code></pre>`);
      }
      continue;
    }
    if (/^###\s+/.test(line)) {
      close();
      out.push(`<h3>${inline(line.replace(/^###\s+/, ""))}</h3>`);
    } else if (/^##\s+/.test(line)) {
      close();
      out.push(`<h2>${inline(line.replace(/^##\s+/, ""))}</h2>`);
    } else if (/^#\s+/.test(line)) {
      close();
      out.push(`<h1>${inline(line.replace(/^#\s+/, ""))}</h1>`);
    } else if (/^[-*]\s+/.test(line)) {
      if (listType !== "ul") {
        close();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
    } else if (/^\d+\.\s+/.test(line)) {
      if (listType !== "ol") {
        close();
        out.push("<ol>");
        listType = "ol";
      }
      out.push(`<li>${inline(line.replace(/^\d+\.\s+/, ""))}</li>`);
    } else if (!line.trim()) {
      close();
    } else {
      close();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  close();
  return out.join("\n");
}
