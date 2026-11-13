"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t");
const viaHttp = () => !window.pywebview && !!TOKEN;
const call = (name, ...args) => {
  if (!viaHttp()) return window.pywebview.api[name](...args);
  return fetch(`/rpc/${name}`, {
    method: "POST",
    headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify(args),
  }).then((response) => response.json());
};

async function pumpEvents() {
  let after = 0;
  for (;;) {
    try {
      const response = await fetch(`/events?after=${after}`, { headers: { "X-Token": TOKEN } });
      const data = await response.json();
      for (const item of data.events) { after = item.id; window.__emit(item.event); }
    } catch (error) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}

let S = null; // launcher state from Python
let page = "home";
let busy = false;
const skinCache = new Map();
const imageCache = new Map();

/* ---------------------------------------------------------------- helpers */

function toast(text, isError = false) {
  const node = $("#toast");
  node.textContent = text;
  node.classList.toggle("err", isError);
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), isError ? 5200 : 2800);
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

const icon = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
};

function loadImage(url) {
  if (!url) return Promise.resolve(null);
  if (imageCache.has(url)) return imageCache.get(url);
  const promise = new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = url;
  });
  imageCache.set(url, promise);
  return promise;
}

async function skinImage(id) {
  if (!id) return null;
  if (!skinCache.has(id)) skinCache.set(id, call("get_skin", id));
  const url = await skinCache.get(id);
  return loadImage(url);
}

function forgetSkin(id) {
  skinCache.delete(id);
}

/* ---------------------------------------------------------------- skin drawing */

function drawHead(canvas, image) {
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!image) {
    const u = canvas.width / 8;
    const px = (color, x, y, w = 1, h = 1) => { ctx.fillStyle = color; ctx.fillRect(x * u, y * u, w * u, h * u); };
    px("#b78a64", 0, 0, 8, 8);
    px("#4a3320", 0, 0, 8, 2);
    px("#4a3320", 0, 2, 1, 1);
    px("#4a3320", 7, 2, 1, 1);
    px("#ffffff", 1, 4, 2, 1);
    px("#ffffff", 5, 4, 2, 1);
    px("#4d3c9c", 2, 4, 1, 1);
    px("#4d3c9c", 5, 4, 1, 1);
    px("#8e5f45", 3, 5, 2, 1);
    px("#6e3d2c", 2, 6, 4, 1);
    return;
  }
  const k = image.width / 64;
  ctx.drawImage(image, 8 * k, 8 * k, 8 * k, 8 * k, 0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, 40 * k, 8 * k, 8 * k, 8 * k, 0, 0, canvas.width, canvas.height);
}

function drawFigure(canvas, image, slim) {
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const s = canvas.width / 16;
  if (!image) {
    const block = (color, x, y, w, h) => {
      ctx.fillStyle = color;
      ctx.fillRect(x * s, y * s, w * s, h * s);
    };
    block("#b98a5e", 4, 0, 8, 8);
    block("#3a2a1a", 5, 4, 2, 1);
    block("#3a2a1a", 9, 4, 2, 1);
    block("#2f8f9d", 4, 8, 8, 12);
    block("#b98a5e", 0, 8, 4, 12);
    block("#b98a5e", 12, 8, 4, 12);
    block("#3b3f9d", 4, 20, 4, 12);
    block("#3b3f9d", 8, 20, 4, 12);
    return;
  }
  const k = image.width / 64;
  const legacy = image.height * 2 === image.width;
  const part = (sx, sy, sw, sh, dx, dy, flip) => {
    ctx.save();
    if (flip) {
      ctx.translate((dx + sw) * s, dy * s);
      ctx.scale(-1, 1);
      ctx.drawImage(image, sx * k, sy * k, sw * k, sh * k, 0, 0, sw * s, sh * s);
    } else {
      ctx.drawImage(image, sx * k, sy * k, sw * k, sh * k, dx * s, dy * s, sw * s, sh * s);
    }
    ctx.restore();
  };
  const aw = slim ? 3 : 4;
  part(4, 20, 4, 12, 4, 20, false);
  part(legacy ? 4 : 20, legacy ? 20 : 52, 4, 12, 8, 20, legacy);
  if (!legacy) {
    part(4, 36, 4, 12, 4, 20, false);
    part(4, 52, 4, 12, 8, 20, false);
  }
  part(20, 20, 8, 12, 4, 8, false);
  if (!legacy) part(20, 36, 8, 12, 4, 8, false);
  part(44, 20, aw, 12, slim ? 1 : 0, 8, false);
  part(legacy ? 44 : 36, legacy ? 20 : 52, aw, 12, 12, 8, legacy);
  if (!legacy) {
    part(44, 36, aw, 12, slim ? 1 : 0, 8, false);
    part(52, 52, aw, 12, 12, 8, false);
  }
  part(8, 8, 8, 8, 4, 0, false);
  part(40, 8, 8, 8, 4, 0, false);
}

async function paintHead(canvas, accountId) {
  drawHead(canvas, null);
  const image = await skinImage(accountId);
  if (image) drawHead(canvas, image);
}

/* ---------------------------------------------------------------- state */

const activeAccount = () => (S?.accounts || []).find((item) => item.id === S.active) || null;

function applyAppearance() {
  const settings = S.settings;
  $("#bg").style.filter = settings.bg_blur ? `blur(${settings.bg_blur}px)` : "";
  document.documentElement.style.setProperty("--dim", String(settings.bg_dim / 100));
}

async function applyBackground() {
  const custom = await call("get_background");
  $("#bg").style.backgroundImage = `url("${custom || "assets/bg.jpg"}")`;
}

function setBusy(value) {
  busy = value;
  for (const id of ["#btn-join", "#btn-solo", "#btn-check", "#add-ms", "#add-off"]) {
    const node = $(id);
    if (node) node.disabled = busy;
  }
  $("#join-text").textContent = busy ? "Working…" : joinLabel();
  if (!busy) setTimeout(() => !busy && $("#dock").classList.remove("show"), 5000);
}

const joinLabel = () => (S?.pack?.server ? "Play online" : "Play");

function renderAll() {
  if (!S) return;
  applyAppearance();
  renderHeader();
  renderHome();
  renderAccounts();
  renderMods();
  renderLogs(true);
  setBusy(S.busy);
}

function renderHeader() {
  const account = activeAccount();
  $("#acct-name").textContent = account ? account.name : "Add account";
  drawHead($("#acct-head"), null);
  const menu = $("#menu-acct");
  menu.replaceChildren();
  for (const item of S.accounts) {
    const head = el("canvas", { width: 32, height: 32 });
    paintHead(head, item.id);
    const row = el("button", { class: "item", onclick: () => switchAccount(item.id) }, head, el("span", { text: item.name }));
    if (item.id === S.active) row.append(el("span", { class: "tag", text: "ACTIVE" }));
    menu.append(row);
  }
  if (S.accounts.length) menu.append(el("hr"));
  menu.append(el("button", { class: "item", onclick: () => { closeMenus(); openSettings(); } }, icon("settings"), el("span", { text: "Settings" })));
}

async function renderHome() {
  const pack = S.pack;
  $("#hero-title").textContent = (pack.name || "LOTMCraft");
  $("#m-mc").textContent = pack.minecraft;
  $("#m-ld").textContent = "NeoForge";
  $("#m-mods").textContent = String(S.mods.installed || 0);
  const account = activeAccount();
  if (account) {
    $("#rank-k").textContent = account.type === "microsoft" ? "MICROSOFT ACCOUNT" : "OFFLINE ACCOUNT";
    $("#rank-v").textContent = account.name;
    $("#rank-d").textContent = "";
  } else {
    $("#rank-k").textContent = "ACCOUNT";
    $("#rank-v").textContent = "NO ACCOUNT";
    $("#rank-d").textContent = "Add an account to start playing.";
  }
  const hasServer = !!pack.server;
  $("#join-text").textContent = busy ? "Working…" : joinLabel();
  $("#btn-solo").classList.toggle("hidden", !hasServer);
}

async function refreshServer() {
  const stats = $("#server-stats");
  if (!S?.pack?.server) {
    $("#server-pill").classList.add("hidden");
    stats.textContent = "";
    return;
  }
  $("#server-pill").classList.remove("hidden");
  $("#server-text").textContent = S.pack.server;
  $("#server-dot").className = "dot";
  stats.textContent = "…";
  try {
    const info = await call("server_status");
    $("#server-dot").className = "dot " + (info.online ? "on" : "off");
    stats.textContent = info.online
      ? `${info.players} / ${info.max} online · ${info.latency} ms`
      : "Server is offline";
  } catch (error) {
    stats.textContent = "";
  }
}

/* ---------------------------------------------------------------- navigation */

function go(name) {
  page = name;
  document.body.classList.toggle("home", name === "home");
  document.body.classList.toggle("sub", name !== "home");
  document.body.classList.remove("edge");
  $$(".page").forEach((node) => node.classList.toggle("active", node.id === `page-${name}`));
  $$(".topnav .nav[data-go]").forEach((node) => node.classList.toggle("active", node.dataset.go === name));
  if (name === "logs") renderLogs(true);
  closeMenus();
}

function closeMenus() {
  $("#menu-acct").classList.remove("open");
  $("#menu-support").classList.remove("open");
}

function openSupport(anchor, side) {
  const menu = $("#menu-support");
  const open = !menu.classList.contains("open");
  closeMenus();
  if (!open) return;
  const rect = anchor.getBoundingClientRect();
  menu.style.position = "fixed";
  if (side === "right") {
    menu.style.left = `${rect.right + 10}px`;
    menu.style.right = "auto";
    menu.style.top = "auto";
    menu.style.bottom = `${innerHeight - rect.bottom}px`;
  } else {
    menu.style.left = "auto";
    menu.style.right = `${innerWidth - rect.right}px`;
    menu.style.bottom = "auto";
    menu.style.top = `${rect.bottom + 8}px`;
  }
  menu.classList.add("open");
}

async function switchAccount(id) {
  closeMenus();
  const result = await call("set_active", id);
  if (result.ok) { S = result.state; renderAll(); }
}

/* ---------------------------------------------------------------- accounts page */

function confirmClick(button, label, action) {
  if (button.dataset.armed) { action(); return; }
  const original = button.textContent;
  button.dataset.armed = "1";
  button.textContent = "Confirm?";
  setTimeout(() => { delete button.dataset.armed; button.textContent = original; }, 3000);
}

function renderAccounts() {
  const list = $("#acc-list");
  list.replaceChildren();
  if (!S.accounts.length) {
    list.append(el("div", { class: "empty", text: "No accounts yet. Add a Microsoft account, or create an offline one." }));
  }
  for (const account of S.accounts) {
    const head = el("canvas", { class: "av", width: 32, height: 32 });
    paintHead(head, account.id);
    const active = account.id === S.active;
    const meta = el("div", { class: "meta" },
      el("div", { class: "nm", text: account.name }),
      el("div", { class: "st" + (active ? " active" : ""), text: (active ? "Active · " : "") + (account.type === "microsoft" ? "Microsoft" : "Offline") }));
    const actions = el("div", { class: "acts" });
    if (!active) actions.append(el("button", { class: "pbtn btn-sm", text: "Use", onclick: () => switchAccount(account.id) }));
    if (account.type === "offline") actions.append(el("button", { class: "pbtn ghost btn-sm", text: "Skin", onclick: () => openOffline(account) }));
    const remove = el("button", { class: "pbtn ghost btn-sm", text: account.type === "microsoft" ? "Sign out" : "Remove" });
    remove.addEventListener("click", () => confirmClick(remove, "", async () => {
      const result = await call("remove_account", account.id);
      forgetSkin(account.id);
      if (result.ok) { S = result.state; renderAll(); }
    }));
    actions.append(remove);
    list.append(el("div", { class: "acc card-b" }, head, meta, actions));
  }
}

/* ---------------------------------------------------------------- offline dialog */

const off = { id: null, mode: "none", file: "", model: "classic" };

function setOffMode(mode) {
  off.mode = mode;
  $$("#off-mode button").forEach((node) => node.classList.toggle("on", node.dataset.m === mode));
  $("#off-file").classList.toggle("hidden", mode !== "file");
  $("#off-url").classList.toggle("hidden", mode !== "url");
}

function setOffModel(model) {
  off.model = model;
  $$("#off-model button").forEach((node) => node.classList.toggle("on", node.dataset.v === model));
  paintOffPreview();
}

async function paintOffPreview() {
  const canvas = $("#off-prev");
  drawFigure(canvas, null, off.model === "slim");
  if (off.id) {
    const image = await skinImage(off.id);
    if (image) drawFigure(canvas, image, off.model === "slim");
  }
}

function openOffline(account = null) {
  off.id = account ? account.id : null;
  off.file = "";
  $("#off-title").textContent = account ? `Skin · ${account.name}` : "Add offline account";
  $("#off-name-wrap").classList.toggle("hidden", !!account);
  $("#off-keep").classList.toggle("hidden", !account);
  $("#off-name").value = "";
  $("#off-link").value = "";
  $("#off-file-name").textContent = "No file chosen.";
  $("#off-err").textContent = "";
  setOffMode(account ? "keep" : "none");
  setOffModel(account?.skin_model === "slim" ? "slim" : "classic");
  $("#dlg-offline").classList.add("open");
  if (!account) setTimeout(() => $("#off-name").focus(), 50);
}

async function saveOffline() {
  const err = $("#off-err");
  err.textContent = "";
  let value = "";
  let mode = off.mode;
  if (mode === "file") {
    value = off.file;
    if (!value) { err.textContent = "Choose a PNG file first."; return; }
  } else if (mode === "url") {
    value = $("#off-link").value.trim();
    if (!value) { err.textContent = "Paste a link to the skin."; return; }
  }
  const button = $("#off-save");
  button.disabled = true;
  try {
    let result;
    if (off.id) {
      result = await call("update_offline", off.id, mode === "none" ? "clear" : mode, value, off.model);
    } else {
      result = await call("add_offline", $("#off-name").value, mode, value, off.model);
    }
    if (!result.ok) { err.textContent = result.error || "Could not save."; return; }
    skinCache.clear();
    S = result.state;
    $("#dlg-offline").classList.remove("open");
    renderAll();
    toast(off.id ? "Skin saved" : "Offline account added");
  } finally {
    button.disabled = false;
  }
}

/* ---------------------------------------------------------------- microsoft login */

function loginPhase(event) {
  const dialog = $("#dlg-login");
  $("#login-err").textContent = event.error || "";
  switch (event.phase) {
    case "code":
      $("#login-code").textContent = event.code;
      $("#login-url").textContent = event.url;
      dialog.classList.add("open");
      call("login_open_browser");
      break;
    case "error":
      $("#login-code").textContent = "········";
      break;
    case "closed":
      dialog.classList.remove("open");
      break;
    case "done":
      dialog.classList.remove("open");
      S = event.state;
      skinCache.clear();
      renderAll();
      toast(`Signed in as ${activeAccount()?.name || "Microsoft account"}`);
      break;
  }
}

/* ---------------------------------------------------------------- mods */

function renderMods() {
  const list = $("#mod-list");
  list.replaceChildren();
  const mods = S.mods.optional;
  $("#mods-info").textContent = `${S.mods.installed} installed`;
  if (!mods.length) {
    list.append(el("div", { class: "empty", text: "No optional mods yet. Add optional.zip to the modpack release, then check for updates." }));
    return;
  }
  for (const mod of mods) {
    const label = mod.name.replace(/\.jar$/i, "");
    const toggle = el("button", { class: "switch" + (mod.enabled ? " on" : ""), "aria-label": label }, el("i"));
    toggle.disabled = busy;
    toggle.addEventListener("click", async () => {
      const next = !toggle.classList.contains("on");
      toggle.classList.toggle("on", next);
      const result = await call("set_optional", mod.name, next);
      if (!result.ok) {
        toggle.classList.toggle("on", !next);
        toast(result.error || "Could not change the mod", true);
        return;
      }
      S = result.state;
      $("#mods-info").textContent = `${S.mods.installed} installed`;
      $("#m-mods").textContent = String(S.mods.installed);
    });
    list.append(el("div", { class: "mod card-b" }, el("span", { text: label }), toggle));
  }
}

/* ---------------------------------------------------------------- logs */

function logNode(entry) {
  return el("div", { class: `ln ${entry.level}` },
    el("span", { class: "src" + (entry.source === "Game" ? " game" : ""), text: `[${entry.source}] ` }),
    document.createTextNode(entry.text));
}

function renderLogs(full) {
  const box = $("#log-box");
  if (full) {
    box.replaceChildren(...S.logs.map(logNode));
  }
  box.scrollTop = box.scrollHeight;
}

function appendLog(entry) {
  if (!S) return;
  S.logs.push(entry);
  if (S.logs.length > 600) S.logs.splice(0, 100);
  const box = $("#log-box");
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.append(logNode(entry));
  while (box.childElementCount > 600) box.firstChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

/* ---------------------------------------------------------------- status */

function setStatus(text, level) {
  const txt = $("#dock-txt");
  txt.textContent = text;
  txt.classList.toggle("error", level === "error");
  $("#dock").classList.add("show");
  $("#mods-status").textContent = text;
  if (level === "error") toast(text, true);
}

function setProgress(value) {
  for (const id of ["#dock-bar", "#mods-bar"]) {
    const bar = $(id);
    if (value == null) {
      bar.classList.add("ind");
      bar.style.width = "";
    } else {
      bar.classList.remove("ind");
      bar.style.width = `${Math.round(value * 100)}%`;
    }
  }
  $("#dock").classList.add("show");
}

/* ---------------------------------------------------------------- settings */

const saveTimers = {};
function saveSetting(patch, delay = 0) {
  Object.assign(S.settings, patch);
  const key = Object.keys(patch).join(",");
  clearTimeout(saveTimers[key]);
  saveTimers[key] = setTimeout(async () => {
    const result = await call("save_settings", patch);
    if (!result.ok) toast(result.error, true);
    else {
      S.system = result.state.system;
      S.pack = result.state.pack;
      S.mods = result.state.mods;
      renderHome();
    }
    $("#gdir").value = S.system.game_dir;
  }, delay);
}

function bindSlider(root, value, onInput, onChange) {
  const input = $("input", root);
  const min = +root.dataset.min;
  const max = +root.dataset.max;
  input.min = min;
  input.max = max;
  input.step = 1;
  input.value = value;
  const paint = () => {
    const current = +input.value;
    const percent = max === min ? 0 : ((current - min) / (max - min)) * 100;
    $(".fill", root).style.width = `${percent}%`;
    $(".thumb", root).style.left = `${percent}%`;
    $(".val", root).textContent = `${current}${root.dataset.suffix || ""}`;
  };
  input.oninput = () => { paint(); onInput(+input.value); };
  input.onchange = () => onChange?.(+input.value);
  paint();
}

function setSwitch(node, on) { node.classList.toggle("on", !!on); }

function fillSettings() {
  const settings = S.settings;
  const ramMax = Math.max(2, Math.min(64, S.system.total_ram));
  const ramRoot = $("#sl-ram");
  ramRoot.dataset.max = ramMax;
  $("#ram-hint").textContent = `Your PC has ${S.system.total_ram} GB.`;
  bindSlider(ramRoot, Math.min(settings.ram, ramMax), () => {}, (value) => saveSetting({ ram: value }));
  bindSlider($("#sl-vol"), settings.music_volume, (value) => { $("#audio").volume = value / 100; }, (value) => saveSetting({ music_volume: value }));
  $("#win-w").value = settings.width;
  $("#win-h").value = settings.height;
  setSwitch($("#sw-hide"), settings.hide_on_launch);
  setSwitch($("#sw-music"), settings.music_enabled);
  $("#jvm").value = settings.jvm_args;
  $("#gdir").value = S.system.game_dir;
}

function openSettings(tab) {
  fillSettings();
  $("#settings").classList.add("open");
  if (tab) showTab(tab);
  refreshMusicLabel();
}

function showTab(name) {
  $$(".set-nav .tab").forEach((node) => node.classList.toggle("on", node.dataset.tab === name));
  $$(".set-pane").forEach((node) => node.classList.toggle("on", node.id === `tab-${name}`));
}

/* ---------------------------------------------------------------- music */

let musicLoaded = false;
async function loadMusic() {
  const track = await call("get_music");
  const audio = $("#audio");
  $("#music-name").textContent = track ? track.name : "No track chosen. MP3, OGG, WAV, M4A or FLAC.";
  if (track) audio.src = track.url; else audio.removeAttribute("src");
  musicLoaded = !!track;
  syncMusic();
}

function refreshMusicLabel() {
  $("#music-name").textContent = musicLoaded ? $("#music-name").textContent : "No track chosen. MP3, OGG, WAV, M4A or FLAC.";
}

function syncMusic() {
  const audio = $("#audio");
  audio.volume = (S.settings.music_volume || 0) / 100;
  if (S.settings.music_enabled && musicLoaded) audio.play().catch(() => {});
  else audio.pause();
}

/* ---------------------------------------------------------------- wiring */

function wire() {
  $$("[data-go]").forEach((node) => node.addEventListener("click", () => go(node.dataset.go)));

  $("#btn-support").addEventListener("click", (event) => { event.stopPropagation(); openSupport($("#btn-support"), "below"); });
  $$("[data-link]").forEach((node) => node.addEventListener("click", (event) => {
    event.stopPropagation();
    call("open_support", node.dataset.link);
    closeMenus();
  }));
  $("#btn-acct").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("#menu-acct");
    const open = !menu.classList.contains("open");
    closeMenus();
    menu.classList.toggle("open", open);
  });
  document.addEventListener("click", closeMenus);

  const play = async (join) => {
    const result = await call("play", join);
    if (!result.ok) {
      toast(result.error || "Could not start.", true);
      if (result.need_account) openOffline();
    }
  };
  $("#btn-join").addEventListener("click", () => play(true));
  $("#btn-solo").addEventListener("click", () => play(false));
  $("#btn-check").addEventListener("click", async () => {
    const result = await call("check_mods");
    if (!result.ok) toast(result.error, true);
  });

  $("#add-off").addEventListener("click", () => openOffline());
  $("#add-ms").addEventListener("click", async () => {
    $("#login-err").textContent = "";
    const result = await call("login_microsoft");
    if (!result.ok) toast(result.error || "Could not start sign-in.", true);
  });

  $("#off-cancel").addEventListener("click", () => $("#dlg-offline").classList.remove("open"));
  $("#off-save").addEventListener("click", saveOffline);
  $("#off-name").addEventListener("keydown", (event) => event.key === "Enter" && saveOffline());
  $$("#off-mode button").forEach((node) => node.addEventListener("click", () => setOffMode(node.dataset.m)));
  $$("#off-model button").forEach((node) => node.addEventListener("click", () => setOffModel(node.dataset.v)));
  $("#off-pick").addEventListener("click", async () => {
    const path = await call("pick_skin_file");
    if (path) { off.file = path; $("#off-file-name").textContent = path.split(/[\\/]/).pop(); }
  });

  $("#login-cancel").addEventListener("click", () => call("login_cancel"));
  $("#login-open").addEventListener("click", () => call("login_open_browser"));
  $("#login-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#login-code").textContent); toast("Code copied"); }
    catch (error) { toast("Select the code and press Ctrl+C", true); }
  });

  $("#logs-clear").addEventListener("click", async () => { await call("clear_logs"); S.logs = []; renderLogs(true); });
  $("#logs-file").addEventListener("click", () => call("open_log_file"));

  // settings
  $("#set-close").addEventListener("click", () => $("#settings").classList.remove("open"));
  $("#scrim").addEventListener("click", () => $("#settings").classList.remove("open"));
  $$(".set-nav .tab").forEach((node) => node.addEventListener("click", () => showTab(node.dataset.tab)));
  $("#bg-choose").addEventListener("click", async () => {
    const result = await call("choose_background");
    if (result.ok) { await applyBackground(); toast("Background changed"); }
    else if (result.error) toast(result.error, true);
  });
  $("#bg-reset").addEventListener("click", async () => { await call("clear_background"); await applyBackground(); });
  $("#music-choose").addEventListener("click", async () => {
    const result = await call("choose_music");
    if (result.ok) { await loadMusic(); toast("Track chosen"); }
    else if (result.error) toast(result.error, true);
  });
  $("#music-clear").addEventListener("click", async () => { await call("clear_music"); await loadMusic(); });
  $("#sw-music").addEventListener("click", () => {
    const next = !$("#sw-music").classList.contains("on");
    setSwitch($("#sw-music"), next);
    saveSetting({ music_enabled: next });
    syncMusic();
  });
  $("#sw-hide").addEventListener("click", () => {
    const next = !$("#sw-hide").classList.contains("on");
    setSwitch($("#sw-hide"), next);
    saveSetting({ hide_on_launch: next });
  });
  for (const [id, key] of [["#win-w", "width"], ["#win-h", "height"]]) {
    $(id).addEventListener("change", () => {
      const value = parseInt($(id).value, 10);
      if (Number.isFinite(value)) saveSetting({ [key]: value });
      else $(id).value = S.settings[key];
    });
  }
  $("#jvm").addEventListener("input", () => saveSetting({ jvm_args: $("#jvm").value }, 400));
  $("#jvm-reset").addEventListener("click", () => { $("#jvm").value = S.system.default_jvm_args; saveSetting({ jvm_args: S.system.default_jvm_args }); });
  $("#gdir").addEventListener("keydown", (event) => event.key === "Enter" && saveSetting({ game_dir: $("#gdir").value }));
  $("#gdir-browse").addEventListener("click", async () => {
    const folder = await call("choose_folder");
    if (folder) { $("#gdir").value = folder; saveSetting({ game_dir: folder }); }
  });
  $("#gdir-open").addEventListener("click", () => call("open_folder"));
  $("#gdir-reset").addEventListener("click", () => { saveSetting({ game_dir: "" }); });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if ($("#dlg-offline").classList.contains("open")) $("#dlg-offline").classList.remove("open");
    else if ($("#dlg-login").classList.contains("open")) call("login_cancel");
    else if ($("#settings").classList.contains("open")) $("#settings").classList.remove("open");
    else closeMenus();
  });
  document.addEventListener("pointerdown", () => { if (S?.settings.music_enabled) syncMusic(); }, { once: true });
}

window.__emit = (event) => {
  switch (event.type) {
    case "log": appendLog(event.entry); break;
    case "status": setStatus(event.text, event.level); break;
    case "progress": setProgress(event.value); break;
    case "busy": setBusy(event.value); if (S) S.busy = event.value; break;
    case "login": loginPhase(event); break;
    case "state": S = event.state; skinCache.clear(); renderAll(); break;
    case "game": break;
  }
};

async function init() {
  wire();
  S = await call("get_state");
  go("home");
  renderAll();
  applyBackground();
  loadMusic();
  refreshServer();
  call("refresh_pack").then((result) => {
    if (result && result.ok && JSON.stringify(result.pack) !== JSON.stringify(S.pack)) {
      S.pack = result.pack;
      renderHome();
      refreshServer();
    }
  }).catch(() => {});
  setInterval(() => { if (page === "home") refreshServer(); }, 60000);
}

if (new URLSearchParams(location.search).has("mock")) {
  const script = document.createElement("script");
  script.src = "mock.js";
  script.onload = init;
  document.head.append(script);
} else if (TOKEN && !window.pywebview) {
  setInterval(() => fetch("/ping", { method: "POST", headers: { "X-Token": TOKEN } }).catch(() => {}), 3000);
  pumpEvents();
  init();
} else if (window.pywebview && window.pywebview.api) {
  init();
} else {
  window.addEventListener("pywebviewready", init);
}
