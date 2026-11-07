import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

from common import instance_dir, load_auth, load_config, load_settings, save_settings
from drive_sync import DriveSync
from game import Game
from skin import clear_skin, has_skin, preview_image, save_skin

REDIRECT = "https://login.live.com/oauth20_desktop.srf"
BOOSTY_URL = "https://boosty.to/talesofheresy"
PATREON_URL = "https://www.patreon.com/cw/Tales_of_Heresy"
BLUE = "#1F6AA5"
BLUE_HOVER = "#144870"
BTN_SEC = "#3a3a3a"
BTN_SEC_HOVER = "#4a4a4a"
TEXT = "#dce4ee"
MUTED = "#9a9a9a"
BORDER = "#565b5e"
CARD = "#343638"
SURFACE = "#2b2b2b"


def _asset(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "assets" / name


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.settings = load_settings()
        self.config = {}
        self.game = None
        try:
            self.config = load_config()
            self.game = Game(self.config.get("azure_client_id") or "", self.config.get("redirect_uri") or REDIRECT)
        except Exception as error:
            self._startup_error = str(error)
        else:
            self._startup_error = ""
        self.microsoft_enabled = bool((self.config.get("azure_client_id") or "").strip())
        self.busy = False
        self.account = load_auth()
        self.progress_max = 1

        self.title("LOTMCraft")
        self.geometry("550x920")
        self.minsize(500, 760)
        self.resizable(False, False)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        header.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(titles, text="LOTMCraft", font=ctk.CTkFont(family="Segoe UI", size=32, weight="bold"), text_color="#ffffff").pack(anchor="w")
        links = ctk.CTkFrame(header, fg_color="transparent")
        links.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self._link_icons = []
        for column, (filename, url) in enumerate((("boosty.png", BOOSTY_URL), ("patreon.png", PATREON_URL))):
            icon = ctk.CTkImage(Image.open(_asset(filename)), size=(28, 28))
            self._link_icons.append(icon)
            ctk.CTkButton(
                links,
                text="",
                image=icon,
                width=40,
                height=40,
                corner_radius=10,
                fg_color=BTN_SEC,
                border_width=1,
                border_color=BORDER,
                hover_color=BTN_SEC_HOVER,
                cursor="hand2",
                command=lambda address=url: webbrowser.open(address),
            ).grid(row=0, column=column, padx=(6, 0))
        ctk.CTkFrame(header, height=1, fg_color=BORDER, corner_radius=0).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER,
            segmented_button_fg_color=SURFACE,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_HOVER,
            segmented_button_unselected_color=BTN_SEC,
            segmented_button_unselected_hover_color=BTN_SEC_HOVER,
            text_color=TEXT,
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=18, pady=(10, 6))
        play = self.tabs.add("Play")
        optional = self.tabs.add("Optional mods")
        play.grid_columnconfigure(0, weight=1)
        play.grid_rowconfigure(0, weight=1)
        optional.grid_columnconfigure(0, weight=1)
        optional.grid_rowconfigure(1, weight=1)

        body = ctk.CTkScrollableFrame(play, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body,
            text="OFFLINE ACCOUNT",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        self.nickname = ctk.CTkEntry(
            body,
            placeholder_text="Nickname",
            height=40,
            corner_radius=10,
            fg_color=CARD,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text_color=MUTED,
        )
        self.nickname.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        if self.settings.get("nickname"):
            self.nickname.insert(0, self.settings["nickname"])

        skin = ctk.CTkFrame(body, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
        skin.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        skin.grid_columnconfigure(1, weight=1)
        portrait = ctk.CTkFrame(skin, fg_color=SURFACE, corner_radius=12, width=76, height=76)
        portrait.grid(row=0, column=0, rowspan=4, padx=(10, 8), pady=10)
        portrait.grid_propagate(False)
        self.skin_view = ctk.CTkLabel(
            portrait,
            text="No\nskin",
            width=72,
            height=72,
            fg_color="transparent",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        )
        self.skin_view.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(skin, text="Skin", anchor="w", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=TEXT).grid(row=0, column=1, sticky="ew", padx=(4, 12), pady=(12, 0))
        self.skin_hint = ctk.CTkLabel(skin, text="", anchor="w", justify="left", wraplength=280, font=ctk.CTkFont(size=12), text_color=MUTED)
        self.skin_hint.grid(row=1, column=1, sticky="ew", padx=(4, 12), pady=(0, 6))
        actions = ctk.CTkFrame(skin, fg_color="transparent")
        actions.grid(row=2, column=1, sticky="w", padx=(4, 12))
        self.skin_choose = ctk.CTkButton(
            actions,
            text="Choose skin",
            width=118,
            height=30,
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            text_color="#ffffff",
            command=self._choose_skin,
        )
        self.skin_choose.grid(row=0, column=0, padx=(0, 8))
        self.skin_clear = ctk.CTkButton(
            actions,
            text="Clear",
            width=72,
            height=30,
            corner_radius=8,
            font=ctk.CTkFont(size=13),
            fg_color=BTN_SEC,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=BTN_SEC_HOVER,
            command=self._clear_skin,
        )
        self.skin_clear.grid(row=0, column=1)
        self.skin_model = ctk.CTkSegmentedButton(
            skin,
            values=["Classic", "Slim"],
            height=28,
            corner_radius=8,
            selected_color=BLUE,
            selected_hover_color=BLUE_HOVER,
            unselected_color=SURFACE,
            unselected_hover_color=BTN_SEC_HOVER,
            text_color=TEXT,
            command=self._skin_model_changed,
        )
        self.skin_model.grid(row=3, column=1, sticky="w", padx=(4, 12), pady=(8, 12))
        self.skin_model.set("Slim" if self.settings.get("skin_model") == "slim" else "Classic")
        self._skin_photo = None
        self._refresh_skin()

        self.ram_label = ctk.CTkLabel(
            body,
            text="",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT,
        )
        self.ram_label.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.ram = ctk.CTkSlider(
            body,
            from_=1,
            to=16,
            number_of_steps=15,
            command=self._ram_changed,
            height=18,
            button_color=BLUE,
            button_hover_color=BLUE_HOVER,
            progress_color=BLUE,
            fg_color=SURFACE,
        )
        self.ram.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 16))
        self.ram.set(int(self.settings.get("ram") or 4))
        self._ram_changed(self.ram.get())

        self.play = ctk.CTkButton(
            body,
            text="Play",
            height=52,
            corner_radius=14,
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            text_color="#ffffff",
            command=self._play,
        )
        self.play.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.updates = ctk.CTkButton(
            body,
            text="Check mod updates",
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=13),
            fg_color=BTN_SEC,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=BTN_SEC_HOVER,
            command=self._check_mods,
        )
        self.updates.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 16))

        account = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        account.grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 12))
        account.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            account,
            text="Microsoft",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 4))
        self.microsoft = ctk.CTkButton(
            account,
            text="Sign in with Microsoft",
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SEC,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=BTN_SEC_HOVER,
            command=self._login,
        )
        self.microsoft.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.use_microsoft = ctk.CTkCheckBox(
            account,
            text="Play with Microsoft",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            border_color=BORDER,
            checkmark_color="#ffffff",
            command=self._microsoft_toggled,
        )
        self.use_microsoft.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 2))
        if self.settings.get("use_microsoft"):
            self.use_microsoft.select()
        self.account_label = ctk.CTkLabel(
            account,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        )
        self.account_label.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 6))
        self.logout_button = ctk.CTkButton(
            account,
            text="Sign out",
            height=30,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SEC,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=BTN_SEC_HOVER,
            command=self._logout,
        )
        self.logout_button.grid(row=4, column=0, sticky="ew", padx=14, pady=(2, 8))
        self._show_account()
        self._sync_skin_state()

        ctk.CTkLabel(
            optional,
            text="Checked mods are installed. Uncheck to remove them.",
            anchor="w",
            wraplength=460,
            justify="left",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self.optional_list = ctk.CTkScrollableFrame(
            optional,
            fg_color=CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        self.optional_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.optional_list.grid_columnconfigure(0, weight=1)
        self._optional_boxes = []

        self.bar = ctk.CTkProgressBar(self, height=6, corner_radius=3, progress_color=BLUE, fg_color=SURFACE)
        self.bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 6))
        self.bar.set(0)
        self.status_label = ctk.CTkLabel(
            self,
            text="Ready",
            wraplength=480,
            justify="left",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=MUTED,
        )
        self.status_label.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 16))

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._load_optional()
        if self._startup_error:
            self._status(self._startup_error)

    def _ram_changed(self, value) -> None:
        self.ram_label.configure(text=f"Memory: {int(float(value))} GB")

    def _choose_skin(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Choose a skin", filetypes=[("PNG skin", "*.png")])
        if not selected:
            return
        try:
            save_skin(Path(selected))
        except RuntimeError as error:
            self._status(str(error))
            return
        self._refresh_skin()
        self._status("Skin saved")

    def _clear_skin(self) -> None:
        clear_skin()
        self._refresh_skin()
        self._status("Skin removed")

    def _skin_model_changed(self, _value: str) -> None:
        self._remember()

    def _microsoft_toggled(self) -> None:
        self._sync_skin_state()

    def _refresh_skin(self) -> None:
        image = preview_image()
        if image is None:
            self._skin_photo = None
            self.skin_view.configure(image=None, text="No\nskin")
        else:
            self._skin_photo = ctk.CTkImage(light_image=image, dark_image=image, size=(72, 72))
            self.skin_view.configure(image=self._skin_photo, text="")
        if hasattr(self, "use_microsoft"):
            self._sync_skin_state()

    def _sync_skin_state(self) -> None:
        offline = not bool(self.use_microsoft.get()) and not self.busy
        state = "normal" if offline else "disabled"
        self.skin_choose.configure(state=state)
        self.skin_clear.configure(state=state)
        self.skin_model.configure(state=state)
        if self.use_microsoft.get():
            self.skin_hint.configure(text="Microsoft uses the account skin.")
        elif has_skin():
            self.skin_hint.configure(text="Visible on your screen in offline play.")
        else:
            self.skin_hint.configure(text="64×64 or 64×32 PNG. Offline play only.")

    def _show_account(self) -> None:
        if self.account and self.account.get("name"):
            self.account_label.configure(text=f"Account: {self.account['name']}")
            self.logout_button.configure(state="normal")
        else:
            self.account_label.configure(text="No Microsoft account signed in")
            self.logout_button.configure(state="disabled")

    def _status(self, text: str) -> None:
        def apply() -> None:
            self.status_label.configure(text=text)

        self.after(0, apply)

    def _set_progress(self, done, total) -> None:
        def apply() -> None:
            if not total:
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.bar.set(0)
                return
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar.set(max(0, min(1, done / total)))

        self.after(0, apply)

    def _install_callback(self) -> dict:
        def set_status(text: str) -> None:
            self._status(text)

        def set_max(value: int) -> None:
            self.progress_max = value or 1

        def set_progress(value: int) -> None:
            self._set_progress(value, self.progress_max)

        return {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

    def _lock(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        self.play.configure(state=state)
        self.updates.configure(state=state)
        self.microsoft.configure(state=state)
        for box in self._optional_boxes:
            box.configure(state=state)
        self.busy = locked
        if hasattr(self, "skin_choose"):
            self._sync_skin_state()

    def _remember(self) -> None:
        self.settings = {
            "nickname": self.nickname.get().strip(),
            "ram": int(self.ram.get()),
            "use_microsoft": bool(self.use_microsoft.get()),
            "skin_model": "slim" if self.skin_model.get() == "Slim" else "classic",
            "optional_mods": list(self.settings.get("optional_mods") or []),
        }
        save_settings(self.settings)

    def _close(self) -> None:
        try:
            self._remember()
        finally:
            self.destroy()

    def _play(self) -> None:
        if self.busy:
            return
        if self.game is None:
            self._status("The mod folder link is missing.")
            return
        self._remember()
        self._lock(True)
        threading.Thread(target=self._play_work, daemon=True).start()

    def _play_work(self) -> None:
        try:
            sync = DriveSync(self.config.get("drive_folder_id", ""))
            sync.check_mods(self._status, self._set_progress, list(self.settings.get("optional_mods") or []))
            pack = sync.read_pack(self._status)
            account = None
            if self.microsoft_enabled and self.settings["use_microsoft"]:
                self._status("Signing in to Microsoft")
                account = self.game.refresh()
                self.account = account
                self.after(0, self._show_account)
                if account is None:
                    raise RuntimeError("Sign in with Microsoft first.")
            version_id = self.game.ensure(pack, self._install_callback())
            self._status("Starting the game")
            note = self.game.launch(
                version_id,
                pack,
                account,
                self.settings["nickname"],
                self.settings["ram"],
                self.settings.get("skin_model") or "classic",
                self._status,
            )
            message = "Game started. The window can take a few minutes."
            if note:
                message = f"{note} {message}"
            self._status(message)
            self._set_progress(1, 1)
        except Exception as error:
            self._status(str(error) or error.__class__.__name__)
        finally:
            self.after(0, self._finish_play)

    def _finish_play(self) -> None:
        self._lock(False)
        self._load_optional()

    def _check_mods(self) -> None:
        if self.busy:
            return
        if not self.config.get("drive_folder_id"):
            self._status("The mod folder link is missing.")
            return
        self._lock(True)
        threading.Thread(target=self._check_mods_work, daemon=True).start()

    def _check_mods_work(self) -> None:
        try:
            DriveSync(self.config.get("drive_folder_id", "")).check_mods(
                self._status,
                self._set_progress,
                list(self.settings.get("optional_mods") or []),
            )
        except Exception as error:
            self._status(str(error) or error.__class__.__name__)
        finally:
            self.after(0, self._finish_mods)

    def _finish_mods(self) -> None:
        self._lock(False)
        self._load_optional()

    def _load_optional(self) -> None:
        try:
            names = DriveSync(self.config.get("drive_folder_id", "")).local_optional()
        except Exception as error:
            names = []
            self._status(str(error) or error.__class__.__name__)
        self._show_optional(names)

    def _show_optional(self, names: list[str]) -> None:
        for child in self.optional_list.winfo_children():
            child.destroy()
        self._optional_boxes = []
        self._optional_names = []
        enabled = {str(name).casefold() for name in self.settings.get("optional_mods") or []}
        if not enabled:
            present = {path.name.casefold() for path in (instance_dir() / "mods").glob("*.jar")}
            enabled = {name.casefold() for name in names if name.casefold() in present}
        self.settings["optional_mods"] = [name for name in names if name.casefold() in enabled]
        if not names:
            ctk.CTkLabel(
                self.optional_list,
                text="No optional mods yet. Add optional.zip to the Drive folder, then check mod updates.",
                wraplength=400,
                justify="left",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=MUTED,
            ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        for index, name in enumerate(names):
            label = name[:-4] if name.lower().endswith(".jar") else name
            box = ctk.CTkCheckBox(
                self.optional_list,
                text=label,
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color=TEXT,
                fg_color=BLUE,
                hover_color=BLUE_HOVER,
                border_color=BORDER,
                checkmark_color="#ffffff",
                command=lambda chosen=name: self._toggle_optional(chosen),
            )
            if name.casefold() in enabled:
                box.select()
            box.grid(row=index, column=0, sticky="w", padx=12, pady=6)
            self._optional_names.append(name)
            self._optional_boxes.append(box)
        self._remember()
        if self.busy:
            for box in self._optional_boxes:
                box.configure(state="disabled")

    def _toggle_optional(self, name: str) -> None:
        box = self._optional_boxes[self._optional_names.index(name)]
        turning_on = bool(box.get())
        try:
            DriveSync(self.config.get("drive_folder_id", "")).set_optional(name, turning_on)
        except Exception as error:
            if turning_on:
                box.deselect()
            else:
                box.select()
            self._status(str(error) or error.__class__.__name__)
            return
        self.settings["optional_mods"] = [
            item
            for item, control in zip(self._optional_names, self._optional_boxes)
            if control.get()
        ]
        self._remember()
        self._status(f"{'Enabled' if turning_on else 'Disabled'} {name}")

    def _login(self) -> None:
        if self.busy:
            return
        if self.game is None or not self.microsoft_enabled:
            self._status("Microsoft sign-in is not set up.")
            return
        import minecraft_launcher_lib.microsoft_account as microsoft

        webbrowser.open(microsoft.get_login_url(self.game.client_id, self.game.redirect_uri))
        self._status("Sign in in the browser, then paste the page address.")
        pasted = ctk.CTkInputDialog(text="Paste the browser address here.", title="Microsoft").get_input()
        if not pasted:
            self._status("Sign-in cancelled.")
            return
        self._lock(True)
        threading.Thread(target=self._login_work, args=(pasted.strip(),), daemon=True).start()

    def _login_work(self, pasted: str) -> None:
        try:
            account = self.game.login_from_browser(pasted)
            self.account = account
            self.after(0, self.use_microsoft.select)
            self.after(0, self._show_account)
            self.after(0, self._sync_skin_state)
            self._status(f"Signed in as {account['name']}")
        except Exception as error:
            self._status(str(error) or error.__class__.__name__)
        finally:
            self.after(0, lambda: self._lock(False))

    def _logout(self) -> None:
        if self.game:
            self.game.logout()
        self.account = None
        self.use_microsoft.deselect()
        self._show_account()
        self._sync_skin_state()
        self._status("Signed out of Microsoft")
