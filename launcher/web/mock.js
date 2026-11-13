/* Development-only stand-in for the Python backend: open index.html?mock in any browser. */
(function () {
  const sampleSkin = (() => {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const x = c.getContext("2d");
    const fill = (color, a, b, w, h) => { x.fillStyle = color; x.fillRect(a, b, w, h); };
    fill("#caa07a", 8, 8, 8, 8); fill("#1b1410", 8, 8, 8, 3); fill("#2a1d14", 10, 12, 1, 1); fill("#2a1d14", 13, 12, 1, 1);
    fill("#20232b", 20, 20, 8, 12); fill("#caa07a", 44, 20, 4, 12); fill("#caa07a", 36, 52, 4, 12);
    fill("#20232b", 4, 20, 4, 12); fill("#20232b", 20, 52, 4, 12); fill("#3a3f4d", 20, 22, 8, 3);
    return c.toDataURL("image/png");
  })();
  let state = {
    settings: { ram: 10, jvm_args: "-XX:+UseG1GC -XX:+ParallelRefProcEnabled", game_dir: "", width: 1920, height: 1080, hide_on_launch: false, active_account: "a1", optional_mods: [], bg_blur: 0, bg_dim: 35, music_enabled: false, music_volume: 40 },
    accounts: [
      { id: "a1", type: "microsoft", name: "RealOTI", skin_model: "classic", has_skin: true },
      { id: "a2", type: "offline", name: "Amongus", skin_model: "slim", has_skin: false },
    ],
    active: "a1",
    pack: { name: "LOTMCraft", minecraft: "1.21.1", neoforge: "21.1.262", server: "mc.lotmcraft.example" },
    mods: { optional: [{ name: "sodium-extra.jar", enabled: true }, { name: "shaders-pack.jar", enabled: false }], installed: 84 },
    busy: false,
    logs: [
      { source: "Launcher", level: "info", text: 'Preparing instance "default"', time: "12:00:00" },
      { source: "Launcher", level: "info", text: "Minecraft 1.21.1 already installed", time: "12:00:01" },
      { source: "Launcher", level: "info", text: "NeoForge 21.1.262 already installed", time: "12:00:01" },
      { source: "Launcher", level: "warn", text: "No updates found for connected account", time: "12:00:02" },
      { source: "Launcher", level: "info", text: "Launching game ...", time: "12:00:03" },
    ],
    system: { total_ram: 32, default_jvm_args: "-XX:+UseG1GC -XX:+ParallelRefProcEnabled", default_game_dir: "C:\\Users\\you\\AppData\\Roaming\\LOTMCraft\\instance", game_dir: "C:\\Users\\you\\AppData\\Roaming\\LOTMCraft\\instance", version: "1.1.0" },
  };
  const ok = () => ({ ok: true, state });
  window.pywebview = {
    api: {
      get_state: async () => state,
      get_skin: async (id) => (id === "a1" ? sampleSkin : null),
      get_background: async () => null,
      get_music: async () => null,
      server_status: async () => ({ address: state.pack.server, online: true, players: 12, max: 100, latency: 31 }),
      save_settings: async (patch) => { Object.assign(state.settings, patch); return ok(); },
      set_active: async (id) => { state.active = id; state.settings.active_account = id; return ok(); },
      remove_account: async (id) => { state.accounts = state.accounts.filter((a) => a.id !== id); return ok(); },
      add_offline: async (name) => { state.accounts.push({ id: "n" + Date.now(), type: "offline", name, skin_model: "classic", has_skin: false }); return ok(); },
      update_offline: async () => ok(),
      play: async () => ({ ok: true }),
      login_microsoft: async () => { setTimeout(() => window.__emit({ type: "login", phase: "code", code: "NV4TUTRT", url: "https://www.microsoft.com/link" }), 10); return { ok: true }; },
      login_cancel: async () => window.__emit({ type: "login", phase: "closed" }),
      login_open_browser: async () => {},
      open_support: async () => {}, open_folder: async () => {}, choose_folder: async () => null, pick_skin_file: async () => "C:\\skins\\me.png",
      choose_background: async () => ({ ok: false, error: "" }), clear_background: async () => {}, choose_music: async () => ({ ok: false, error: "" }), clear_music: async () => {},
      check_mods: async () => ({ ok: true }), set_optional: async (n, e) => { const m = state.mods.optional.find((x) => x.name === n); if (m) m.enabled = e; return ok(); },
      clear_logs: async () => {}, open_log_file: async () => {},
    },
  };
})();
