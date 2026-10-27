// 我的设备电量 —— 前端逻辑：轮询 /api/battery 并渲染卡片
"use strict";

const $ = (s) => document.querySelector(s);
const cardsEl = $("#cards");
const footStatus = $("#footStatus");
const banner = $("#banner");
const refreshBtn = $("#refreshBtn");
const autoToggle = $("#autoToggle");
const intervalSel = $("#intervalSel");

const DEVICES = [
  { key: "headphone", emoji: "🎧", accent: "headphone" },
  { key: "controller", emoji: "🎮", accent: "controller" },
  { key: "keyboard", emoji: "⌨️", accent: "keyboard" },
];

const state = { auto: true, timer: null, busy: false };

// ---- 工具 ------------------------------------------------------------
function levelColor(l) {
  if (l == null) return "#565d68";
  if (l >= 60) return "#5ddc8a";
  if (l >= 30) return "#f5c451";
  return "#ff6b6b";
}

function lighten(l) {
  if (l == null) return "#3a3f47";
  if (l >= 60) return "#3f7a55";
  if (l >= 30) return "#8a6d2e";
  return "#8a4444";
}

// ---- 渲染 ------------------------------------------------------------
function cardHTML(dev, d) {
  const online = d && d.level != null;
  const color = levelColor(d && d.level);
  const pct = online ? d.level : null;
  const low = online && d.level < 30;

  const sub = d ? (d.subtitle || d.error || "") : "";
  const stateText = d
    ? (online ? (d.status || (d.detail ? "" : "在线")) : (d.error || "未连接"))
    : "正在读取…";
  const detail = d ? (online ? d.detail : "") : "";

  return `
  <section class="card ${online ? "" : "off"} ${low ? "low" : ""}" data-key="${dev.key}">
    <div class="card-head">
      <div class="chip ${dev.accent}">${dev.emoji}</div>
      <div>
        <div class="card-title">${d ? d.title : dev.key}</div>
        <div class="card-sub" title="${sub}">${sub || " "}</div>
      </div>
    </div>
    <div class="num-row">
      <span class="num ${online ? "" : "off"}" style="${online ? `color:${color}` : ""}">
        ${online ? d.level : "--"}${online ? '<span class="pct">%</span>' : ""}
      </span>
    </div>
    <div class="bar"><div class="bar-fill" style="width:${online ? pct : 0}%;
      background: linear-gradient(90deg, ${lighten(d && d.level)}, ${color});"></div></div>
    <div class="card-foot">
      <span class="state-dot ${online ? (low ? "bad" : d.level >= 60 ? "" : "warn") : "off"}"></span>
      <span>${stateText}</span>
    </div>
    ${detail ? `<div class="card-foot" style="margin-top:6px;opacity:.8"><span>${detail}</span></div>` : ""}
  </section>`;
}

function render(data) {
  const byKey = {};
  for (const d of data.devices || []) byKey[d.key] = d;
  cardsEl.innerHTML = DEVICES.map((dev) => cardHTML(dev, byKey[dev.key])).join("");
  footStatus.textContent =
    `更新于 ${data.updated || "--"} · 成功读取 ${data.ok}/${data.total} 台设备`;
  banner.classList.add("hidden");
}

// ---- 数据 ------------------------------------------------------------
async function fetchOnce() {
  if (state.busy) return;
  state.busy = true;
  refreshBtn.classList.add("loading");
  try {
    const res = await fetch("/api/battery", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    render(await res.json());
  } catch (e) {
    banner.classList.remove("hidden");
    $("#bannerText").textContent =
      "无法连接后台程序（窗口已失去数据源，请重新启动程序）";
  } finally {
    state.busy = false;
    refreshBtn.classList.remove("loading");
  }
}

function schedule() {
  if (state.timer) clearInterval(state.timer);
  if (state.auto) {
    state.timer = setInterval(fetchOnce, Number(intervalSel.value) * 1000);
  }
}

// ---- 交互 ------------------------------------------------------------
refreshBtn.addEventListener("click", fetchOnce);
autoToggle.addEventListener("change", () => {
  state.auto = autoToggle.checked;
  schedule();
});
intervalSel.addEventListener("change", () => {
  localStorage.setItem("mbv_interval", intervalSel.value);
  schedule();
});
window.addEventListener("pagehide", () => {
  try { navigator.sendBeacon("/api/shutdown"); } catch (e) {}
});

// ---- 窗口尺寸/位置记忆（仅在 --app 独立窗口里生效，浏览器标签页里自动无效）----
const WIN_KEY = "mbv_win";

function applyWin() {
  try {
    // 支持 ?win=W,H 参数（调试用）；否则恢复上次保存的尺寸
    const q = new URLSearchParams(location.search).get("win");
    const s = q
      ? (() => { const [w, h] = q.split(",").map(Number); return { w, h }; })()
      : JSON.parse(localStorage.getItem(WIN_KEY) || "null");
    if (!s || !s.w) return;
    const w = Math.max(560, Math.min(s.w, screen.availWidth - 16));
    const h = Math.max(420, Math.min(s.h, screen.availHeight - 16));
    window.resizeTo(w, h);
    if (!q && typeof s.x === "number") {
      const x = Math.max(-8, Math.min(s.x, screen.availWidth - 120));
      const y = Math.max(0, Math.min(s.y, screen.availHeight - 80));
      window.moveTo(x, y);
    }
  } catch (e) { /* 浏览器标签页里 resizeTo 无效，忽略 */ }
}

function watchWin() {
  let t = null;
  window.addEventListener("resize", () => {
    clearTimeout(t);
    t = setTimeout(() => {
      try {
        // 最大化/接近全屏时不保存，避免把最大化尺寸当成普通尺寸恢复
        if (window.outerWidth >= screen.availWidth - 24) return;
        localStorage.setItem(WIN_KEY, JSON.stringify({
          w: window.outerWidth, h: window.outerHeight,
          x: window.screenX, y: window.screenY,
        }));
      } catch (e) { /* ignore */ }
    }, 400);
  });
}

// ---- 启动 ------------------------------------------------------------
const savedInterval = localStorage.getItem("mbv_interval");
if (savedInterval && [...intervalSel.options].some((o) => o.value === savedInterval)) {
  intervalSel.value = savedInterval;
}
schedule();
fetchOnce();
applyWin();
watchWin();
