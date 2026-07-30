"""Modern CustomTkinter desktop UI — dark glass-style + Telegram-synced chat."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk
from PIL import Image

from config import (
    EDITABLE_SETTINGS,
    REQUIRED_DOCS,
    SECRET_SETTINGS,
    docs_checklist,
    get_docs_dir,
    get_project_dir,
    is_git_repo,
    mask_secret,
    read_env_settings,
    set_project_dir,
    write_env_settings,
)
from runner import get_runner_snapshot, start_run, stop_run
from startup_windows import is_startup_enabled, set_startup
from state import load_pending_approval, load_state
from telegram_chat import answer_project_question

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

# Windows taskbar: force unique AppUserModelID so custom .ico sticks
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "Upotto.Foreman.Desktop"
        )
    except Exception:  # noqa: BLE001
        pass


def _asset_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets" / Path(*parts)
    return ASSETS_DIR.joinpath(*parts)


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Modern dark palette (dashboard / glass-inspired)
BG = "#0b0d10"
SIDE = "#12151a"
PANEL = "#161a21"
PANEL2 = "#1c222c"
BORDER = "#2a313c"
ACCENT = "#4c8dff"
ACCENT2 = "#6ea0ff"
DANGER = "#e35d6a"
OK = "#3ecf8e"
WARN = "#e6b84d"
MUTED = "#8b96a8"
TEXT = "#f0f3f7"
USER_BUBBLE = "#2a4a7a"
AGENT_BUBBLE = "#222830"
TG_BADGE = "#2aabee"


class ForemanApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Upotto Foreman")
        self.geometry("1280x820")
        self.minsize(1020, 680)
        self.configure(fg_color=BG)

        self._status_var = tk.StringVar(value="IDLE")
        self._project_var = tk.StringVar(value=str(get_project_dir()))
        self._poll_job: str | None = None
        self._chat_len = 0
        self._active_page = "dashboard"
        self._bubble_refs: list[Any] = []

        self._apply_window_icon()
        self.after(100, self._apply_window_icon)
        self.after(500, self._apply_window_icon)

        self._build_layout()
        self.refresh_all()
        self._schedule_poll()

    def _apply_window_icon(self) -> None:
        ico = _asset_path("upotto-foreman.ico")
        try:
            if ico.is_file():
                path = str(ico.resolve())
                self.iconbitmap(path)
                self.wm_iconbitmap(path)
                try:
                    self.iconbitmap(default=path)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not set window icon: %s", exc)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=SIDE)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=14, pady=(22, 18))
        png = _asset_path("upotto-foreman.png")
        if png.is_file():
            try:
                logo = ctk.CTkImage(
                    light_image=Image.open(png),
                    dark_image=Image.open(png),
                    size=(44, 44),
                )
                self._sidebar_logo = logo
                ctk.CTkLabel(brand, image=logo, text="").pack(side="left", padx=(0, 10))
            except Exception as exc:  # noqa: BLE001
                logger.warning("sidebar logo: %s", exc)
        ctk.CTkLabel(
            brand,
            text="Upotto\nForeman",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=TEXT,
            justify="left",
            anchor="w",
        ).pack(side="left")

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("dashboard", "  Dashboard"),
            ("project", "  Project"),
            ("approvals", "  Approvals"),
            ("logs", "  Logs"),
            ("chat", "  Chat"),
            ("settings", "  Settings"),
        ):
            btn = ctk.CTkButton(
                side,
                text=label,
                anchor="w",
                height=42,
                corner_radius=12,
                fg_color="transparent",
                hover_color=PANEL2,
                text_color=MUTED,
                font=ctk.CTkFont(size=14),
                command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x", padx=12, pady=3)
            self._nav_buttons[key] = btn

        ctk.CTkLabel(
            side,
            text="Telegram synced",
            text_color=TG_BADGE,
            font=ctk.CTkFont(size=11),
        ).pack(side="bottom", pady=16)

        self._host = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._host.grid(row=0, column=1, sticky="nsew")
        self._host.grid_columnconfigure(0, weight=1)
        self._host.grid_rowconfigure(0, weight=1)

        self._pages: dict[str, ctk.CTkFrame] = {}
        self._pages["dashboard"] = self._build_dashboard(self._host)
        self._pages["project"] = self._build_project(self._host)
        self._pages["approvals"] = self._build_approvals(self._host)
        self._pages["logs"] = self._build_logs(self._host)
        self._pages["chat"] = self._build_chat(self._host)
        self._pages["settings"] = self._build_settings(self._host)
        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self.show_page("dashboard")

    def show_page(self, key: str) -> None:
        self._active_page = key
        self._pages[key].tkraise()
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=PANEL2, text_color=TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=MUTED)
        refresh = {
            "logs": self.refresh_logs,
            "approvals": self.refresh_approvals,
            "chat": self.refresh_chat,
            "settings": self.refresh_settings,
            "project": self.refresh_project,
            "dashboard": self.refresh_dashboard,
        }.get(key)
        if refresh:
            refresh()

    def _card(self, parent: Any, **kwargs: Any) -> ctk.CTkFrame:
        opts = {
            "fg_color": PANEL,
            "corner_radius": 18,
            "border_width": 1,
            "border_color": BORDER,
        }
        opts.update(kwargs)
        return ctk.CTkFrame(parent, **opts)

    # ------------------------------------------------------------------ pages
    def _build_dashboard(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(2, weight=1)
        page.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(28, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Dashboard", font=ctk.CTkFont(size=28, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._status_pill = ctk.CTkFrame(header, fg_color=PANEL2, corner_radius=999)
        self._status_pill.grid(row=0, column=1, sticky="e")
        self._status_pill_lbl = ctk.CTkLabel(
            self._status_pill,
            textvariable=self._status_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
        )
        self._status_pill_lbl.pack(padx=16, pady=8)

        self._dash_project_lbl = ctk.CTkLabel(page, text="", text_color=MUTED, anchor="w")
        self._dash_project_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=28)

        left = self._card(page)
        left.grid(row=2, column=0, sticky="nsew", padx=(28, 10), pady=16)
        ctk.CTkLabel(left, text="Controls", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=18, pady=(18, 10)
        )
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=6)
        self._btn_start = ctk.CTkButton(
            btn_row, text="Start run", height=40, corner_radius=12, fg_color=ACCENT, command=self._on_start
        )
        self._btn_start.pack(side="left", padx=(0, 8))
        self._btn_stop = ctk.CTkButton(
            btn_row,
            text="Stop",
            height=40,
            corner_radius=12,
            fg_color=DANGER,
            hover_color="#c44b57",
            command=self._on_stop,
        )
        self._btn_stop.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Refresh", height=40, corner_radius=12, fg_color=PANEL2, command=self.refresh_all
        ).pack(side="left")
        self._dash_meta = ctk.CTkLabel(left, text="", justify="left", anchor="w", text_color=MUTED)
        self._dash_meta.pack(fill="x", padx=18, pady=10)
        self._dash_error = ctk.CTkTextbox(
            left, height=72, corner_radius=12, fg_color="#2a1518", text_color="#ffb4bb"
        )
        self._dash_error.pack(fill="x", padx=18, pady=(0, 18))

        right = self._card(page)
        right.grid(row=2, column=1, sticky="nsew", padx=(10, 28), pady=16)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(right, text="Last summary", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 8)
        )
        self._dash_summary = ctk.CTkTextbox(right, corner_radius=12, fg_color="#0e1116")
        self._dash_summary.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))

        plan_card = self._card(page)
        plan_card.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=28, pady=(0, 28))
        plan_card.grid_columnconfigure(0, weight=1)
        plan_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(plan_card, text="Current plan", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 8)
        )
        # Markdown preview (not plain textbox)
        plan_wrap = ctk.CTkFrame(plan_card, fg_color="#0e1116", corner_radius=12)
        plan_wrap.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        plan_wrap.grid_columnconfigure(0, weight=1)
        plan_wrap.grid_rowconfigure(0, weight=1)
        self._dash_plan = tk.Text(plan_wrap, height=12, wrap="word")
        from md_view import configure_md_tags

        configure_md_tags(self._dash_plan, fg=TEXT, bg="#0e1116")
        self._dash_plan.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        plan_scroll = ctk.CTkScrollbar(plan_wrap, command=self._dash_plan.yview)
        plan_scroll.grid(row=0, column=1, sticky="ns", pady=4)
        self._dash_plan.configure(yscrollcommand=plan_scroll.set)
        return page

    def _build_project(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        # Outer CTkFrame required — CTkScrollableFrame alone breaks tkraise() nav
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(scroll, text="Project", font=ctk.CTkFont(size=28, weight="bold")).pack(
            anchor="w", padx=28, pady=(28, 10)
        )
        path_card = self._card(scroll)
        path_card.pack(fill="x", padx=28, pady=8)
        ctk.CTkLabel(path_card, text="Project path", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=18, pady=(18, 8)
        )
        row = ctk.CTkFrame(path_card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 8))
        self._path_entry = ctk.CTkEntry(row, height=40, corner_radius=12, textvariable=self._project_var)
        self._path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            row, text="Browse", width=90, height=40, corner_radius=12, fg_color=PANEL2, command=self._browse_project
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Save", width=90, height=40, corner_radius=12, fg_color=ACCENT, command=self._save_project
        ).pack(side="left")
        self._git_lbl = ctk.CTkLabel(path_card, text="", text_color=MUTED)
        self._git_lbl.pack(anchor="w", padx=18, pady=(0, 18))

        docs_card = self._card(scroll)
        docs_card.pack(fill="x", padx=28, pady=8)
        ctk.CTkLabel(
            docs_card, text="Spec docs (required)", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(18, 8))
        self._docs_box = ctk.CTkTextbox(docs_card, height=140, corner_radius=12, fg_color="#0e1116")
        self._docs_box.pack(fill="x", padx=18, pady=(0, 18))

        upload_card = self._card(scroll)
        upload_card.pack(fill="x", padx=28, pady=8)
        ctk.CTkLabel(upload_card, text="Upload MD file", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=18, pady=(18, 8)
        )
        urow = ctk.CTkFrame(upload_card, fg_color="transparent")
        urow.pack(fill="x", padx=18, pady=(0, 18))
        self._doc_target = ctk.CTkComboBox(urow, values=list(REQUIRED_DOCS), width=200, height=36, corner_radius=10)
        self._doc_target.set(REQUIRED_DOCS[0])
        self._doc_target.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            urow, text="Choose file…", height=36, corner_radius=12, fg_color=ACCENT, command=self._upload_doc
        ).pack(side="left")

        paste_card = self._card(scroll)
        paste_card.pack(fill="both", expand=True, padx=28, pady=(8, 28))
        ctk.CTkLabel(paste_card, text="Paste / edit content", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=18, pady=(18, 8)
        )
        self._paste_target = ctk.CTkComboBox(paste_card, values=list(REQUIRED_DOCS), height=36, corner_radius=10)
        self._paste_target.set(REQUIRED_DOCS[4])
        self._paste_target.pack(fill="x", padx=18, pady=(0, 8))
        self._paste_box = ctk.CTkTextbox(paste_card, height=200, corner_radius=12, fg_color="#0e1116")
        self._paste_box.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        ctk.CTkButton(
            paste_card, text="Save content", height=40, corner_radius=12, fg_color=ACCENT, command=self._save_paste
        ).pack(anchor="e", padx=18, pady=(0, 18))
        return page

    def _build_settings(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        # Outer CTkFrame required — CTkScrollableFrame alone breaks tkraise() nav
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color=BG)
        scroll.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(scroll, text="Settings", font=ctk.CTkFont(size=28, weight="bold")).pack(
            anchor="w", padx=28, pady=(28, 10)
        )
        startup_card = self._card(scroll)
        startup_card.pack(fill="x", padx=28, pady=8)
        ctk.CTkLabel(
            startup_card, text="Windows Startup", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(18, 8))
        self._startup_var = tk.BooleanVar(value=is_startup_enabled())
        ctk.CTkCheckBox(
            startup_card,
            text="Open Upotto Foreman when Windows starts",
            variable=self._startup_var,
        ).pack(anchor="w", padx=18, pady=(0, 18))

        env_card = self._card(scroll)
        env_card.pack(fill="both", expand=True, padx=28, pady=(8, 28))
        ctk.CTkLabel(env_card, text="Environment", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=18, pady=(18, 8)
        )
        self._settings_entries: dict[str, ctk.CTkEntry] = {}
        grid = ctk.CTkFrame(env_card, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        for i, key in enumerate(EDITABLE_SETTINGS):
            r, c = divmod(i, 2)
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=6)
            grid.grid_columnconfigure(c, weight=1)
            ctk.CTkLabel(cell, text=key, text_color=MUTED, anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(cell, height=36, corner_radius=10)
            entry.pack(fill="x")
            self._settings_entries[key] = entry
        ctk.CTkLabel(
            env_card,
            text="Secret fields show a masked value — leave unchanged to keep the current secret.",
            text_color=MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 8))
        ctk.CTkButton(
            env_card, text="Save settings", height=40, corner_radius=12, fg_color=ACCENT, command=self._save_settings
        ).pack(anchor="e", padx=18, pady=(0, 18))
        return page

    def _build_approvals(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(page, text="Approvals", font=ctk.CTkFont(size=28, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=28, pady=(28, 10)
        )
        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 28))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        self._appr_reason = ctk.CTkLabel(card, text="No pending approval.", anchor="w", justify="left")
        self._appr_reason.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))
        self._appr_plan = ctk.CTkTextbox(card, corner_radius=12, fg_color="#0e1116")
        self._appr_plan.grid(row=2, column=0, sticky="nsew", padx=18, pady=8)
        brow = ctk.CTkFrame(card, fg_color="transparent")
        brow.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 18))
        self._btn_approve = ctk.CTkButton(
            brow, text="Approve", height=40, corner_radius=12, fg_color=ACCENT, command=lambda: self._decide(True)
        )
        self._btn_approve.pack(side="left", padx=(0, 8))
        self._btn_reject = ctk.CTkButton(
            brow,
            text="Reject",
            height=40,
            corner_radius=12,
            fg_color=DANGER,
            hover_color="#c44b57",
            command=lambda: self._decide(False),
        )
        self._btn_reject.pack(side="left")
        return page

    def _build_logs(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        page.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 10))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Logs", font=ctk.CTkFont(size=28, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            head, text="Refresh", width=100, height=36, corner_radius=12, fg_color=PANEL2, command=self.refresh_logs
        ).grid(row=0, column=1, sticky="e")
        hist = self._card(page)
        hist.grid(row=1, column=0, sticky="nsew", padx=28, pady=8)
        hist.grid_columnconfigure(0, weight=1)
        hist.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(hist, text="State log history", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 8)
        )
        self._log_history = ctk.CTkTextbox(hist, corner_radius=12, fg_color="#0e1116")
        self._log_history.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        file_card = self._card(page)
        file_card.grid(row=2, column=0, sticky="nsew", padx=28, pady=(8, 28))
        file_card.grid_columnconfigure(0, weight=1)
        file_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(file_card, text="File log tail", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 8)
        )
        self._log_file = ctk.CTkTextbox(file_card, corner_radius=12, fg_color="#0e1116")
        self._log_file.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        return page

    def _build_chat(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        """Modern chat panel — bubbles, Telegram sync badge, live poll."""
        page = ctk.CTkFrame(parent, fg_color=BG)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        shell = self._card(page)
        shell.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=28, pady=28)
        page.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        # Header
        head = ctk.CTkFrame(shell, fg_color=PANEL2, corner_radius=16)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        head.grid_columnconfigure(1, weight=1)
        png = _asset_path("upotto-foreman.png")
        if png.is_file():
            try:
                av = ctk.CTkImage(
                    light_image=Image.open(png),
                    dark_image=Image.open(png),
                    size=(36, 36),
                )
                self._chat_avatar = av
                ctk.CTkLabel(head, image=av, text="").grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=12)
            except Exception:  # noqa: BLE001
                pass
        ctk.CTkLabel(
            head, text="Agent Chat", font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT
        ).grid(row=0, column=1, sticky="w", pady=(12, 0))
        ctk.CTkLabel(
            head,
            text="Desktop + Telegram  ·  same agent",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        ).grid(row=1, column=1, sticky="w", pady=(0, 12))
        self._chat_online = ctk.CTkLabel(
            head,
            text="● Online",
            text_color=OK,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._chat_online.grid(row=0, column=2, rowspan=2, padx=16)

        # Scrollable bubbles
        self._chat_scroll = ctk.CTkScrollableFrame(
            shell, fg_color="#0e1116", corner_radius=16, border_width=0
        )
        self._chat_scroll.grid(row=1, column=0, sticky="nsew", padx=14, pady=8)
        self._chat_scroll.grid_columnconfigure(0, weight=1)

        # Composer
        composer = ctk.CTkFrame(shell, fg_color=PANEL2, corner_radius=16)
        composer.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 14))
        composer.grid_columnconfigure(0, weight=1)
        self._chat_entry = ctk.CTkEntry(
            composer,
            height=44,
            corner_radius=14,
            placeholder_text="Message the agent… (also works from Telegram)",
            fg_color="#0e1116",
            border_color=BORDER,
        )
        self._chat_entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        self._chat_entry.bind("<Return>", lambda _e: self._send_chat())
        self._chat_send = ctk.CTkButton(
            composer,
            text="Send",
            width=100,
            height=44,
            corner_radius=14,
            fg_color=ACCENT,
            hover_color=ACCENT2,
            command=self._send_chat,
        )
        self._chat_send.grid(row=0, column=1, padx=(0, 12), pady=12)
        return page

    # ------------------------------------------------------------------ chat bubbles
    def _clear_bubbles(self) -> None:
        for w in self._chat_scroll.winfo_children():
            w.destroy()
        self._bubble_refs.clear()

    def _add_bubble(self, *, who: str, text: str, source: str = "") -> None:
        from md_view import create_markdown_bubble

        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=6)
        is_user = who == "you"
        align = "e" if is_user else "w"
        bubble_color = USER_BUBBLE if is_user else AGENT_BUBBLE
        wrap = ctk.CTkFrame(row, fg_color="transparent")
        wrap.pack(anchor=align, fill="x")

        meta = "You" if is_user else "Agent"
        if source == "telegram":
            meta += "  ·  Telegram"
        elif source == "desktop":
            meta += "  ·  Desktop"

        ctk.CTkLabel(
            wrap,
            text=meta,
            text_color=TG_BADGE if source == "telegram" else MUTED,
            font=ctk.CTkFont(size=11),
            anchor=align,
        ).pack(anchor=align, padx=4)

        bubble = ctk.CTkFrame(wrap, fg_color=bubble_color, corner_radius=16)
        bubble.pack(anchor=align, padx=4, pady=(2, 0))
        # Markdown-rendered content (bold / headers / code / lists)
        md = create_markdown_bubble(
            bubble,
            text or "",
            fg=TEXT,
            bg=bubble_color,
            width=560,
        )
        md.pack(padx=8, pady=6, anchor="w")
        self._bubble_refs.append(bubble)

    def _render_chat_history(self, history: list[dict]) -> None:
        self._clear_bubbles()
        if not history:
            empty = ctk.CTkLabel(
                self._chat_scroll,
                text="No messages yet.\nChat here or from Telegram — replies sync both ways.",
                text_color=MUTED,
                justify="center",
            )
            empty.pack(expand=True, pady=40)
            self._chat_len = 0
            return
        for turn in history:
            src = turn.get("source") or ""
            self._add_bubble(who="you", text=str(turn.get("question") or ""), source=src)
            self._add_bubble(who="agent", text=str(turn.get("answer") or ""), source=src)
        self._chat_len = len(history)
        try:
            self._chat_scroll._parent_canvas.yview_moveto(1.0)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ actions
    def _browse_project(self) -> None:
        path = filedialog.askdirectory(title="Select project folder")
        if path:
            self._project_var.set(path)

    def _save_project(self) -> None:
        path = self._project_var.get().strip()
        if not path:
            messagebox.showerror("Project", "Path is required")
            return
        set_project_dir(path)
        self.refresh_project()
        self.refresh_dashboard()
        messagebox.showinfo("Project", f"Saved:\n{get_project_dir()}")

    def _upload_doc(self) -> None:
        name = self._doc_target.get()
        path = filedialog.askopenfilename(
            title=f"Upload {name}",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All", "*.*")],
        )
        if not path:
            return
        dest = get_docs_dir()
        dest.mkdir(parents=True, exist_ok=True)
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        (dest / name).write_text(content, encoding="utf-8")
        self.refresh_project()
        messagebox.showinfo("Docs", f"Saved {name}")

    def _save_paste(self) -> None:
        name = self._paste_target.get()
        content = self._paste_box.get("1.0", "end-1c")
        dest = get_docs_dir()
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text(content, encoding="utf-8")
        self.refresh_project()
        messagebox.showinfo("Docs", f"Saved {name}")

    def _on_start(self) -> None:
        result = start_run(skip_human_input=True)
        if not result.get("ok"):
            messagebox.showwarning("Run", result.get("error", "Could not start"))
        self.refresh_dashboard()

    def _on_stop(self) -> None:
        stop_run()
        self.refresh_dashboard()

    def _decide(self, approved: bool) -> None:
        from webhook import apply_approval

        apply_approval(approved=approved, source="desktop_ctk")
        self.refresh_approvals()
        self.refresh_dashboard()

    def _send_chat(self) -> None:
        q = self._chat_entry.get().strip()
        if not q:
            return
        self._chat_entry.delete(0, "end")
        self._chat_send.configure(state="disabled")
        self._add_bubble(who="you", text=q, source="desktop")
        self._add_bubble(who="agent", text="Thinking…", source="desktop")

        def work() -> None:
            try:
                reply = answer_project_question(q, source="desktop")
                # Desktop chat stays in the app only — Telegram chat is separate
                # (Telegram → agent → Telegram via telegram_poller)
            except Exception as exc:  # noqa: BLE001
                reply = f"Error: {exc}"
            self.after(0, lambda: self._finish_chat_reply(reply))

        threading.Thread(target=work, daemon=True).start()

    def _finish_chat_reply(self, reply: str) -> None:
        self._chat_send.configure(state="normal")
        self.refresh_chat(force=True)

    def _save_settings(self) -> None:
        updates: dict[str, str] = {}
        for key, entry in self._settings_entries.items():
            val = entry.get().strip()
            if key in SECRET_SETTINGS and ("…" in val or val == "********"):
                continue
            updates[key] = val
        if updates:
            write_env_settings(updates)
            if updates.get("PROJECT_DIR"):
                set_project_dir(updates["PROJECT_DIR"])
                self._project_var.set(str(get_project_dir()))
        result = set_startup(bool(self._startup_var.get()))
        if not result.get("ok") and self._startup_var.get():
            messagebox.showwarning("Startup", result.get("error", "Failed to set startup"))
        else:
            messagebox.showinfo("Settings", "Saved")
        self.refresh_settings()

    # ------------------------------------------------------------------ refresh
    def refresh_all(self) -> None:
        self.refresh_dashboard()
        self.refresh_project()

    def refresh_dashboard(self) -> None:
        snap = get_runner_snapshot()
        status = (snap.get("status") or "idle").upper()
        self._status_var.set(status)
        colors = {
            "RUNNING": (OK, "#1c3d2f"),
            "PENDING_APPROVAL": (WARN, "#3d3420"),
            "STOPPING": (WARN, "#3d3420"),
            "IDLE": (MUTED, PANEL2),
        }
        fg, bg = colors.get(status, (MUTED, PANEL2))
        self._status_pill_lbl.configure(text_color=fg)
        self._status_pill.configure(fg_color=bg)
        project = snap.get("project_dir") or str(get_project_dir())
        self._dash_project_lbl.configure(text=project)
        docs = docs_checklist()
        present = sum(1 for d in docs if d["present"])
        git = "ready" if is_git_repo() else "not initialized"
        self._dash_meta.configure(
            text=f"Git: {git}\nDocs: {present}/{len(docs)} present\nLast run: {snap.get('last_run') or 'never'}"
        )
        self._set_text(self._dash_error, snap.get("last_error") or "")
        self._set_text(self._dash_summary, snap.get("last_summary") or "No summary yet.")
        self._set_markdown(self._dash_plan, snap.get("current_plan") or "_No plan yet._")
        running = bool(snap.get("running"))
        self._btn_start.configure(state="disabled" if running else "normal")
        self._btn_stop.configure(state="normal" if running else "disabled")

    def refresh_project(self) -> None:
        self._project_var.set(str(get_project_dir()))
        if is_git_repo():
            self._git_lbl.configure(text="Git: .git found", text_color=OK)
        else:
            self._git_lbl.configure(text="Git: run git init in this folder", text_color=WARN)
        lines = [
            f"{d['name']:18}  {'OK' if d['present'] else 'MISSING':8}  {d['size']} bytes"
            for d in docs_checklist()
        ]
        self._set_text(self._docs_box, "\n".join(lines))

    def refresh_approvals(self) -> None:
        pending = load_pending_approval() or load_state().get("pending_approval")
        if pending and pending.get("status") == "pending":
            self._appr_reason.configure(
                text=f"PENDING\nReason: {pending.get('reason')}\nRequested: {pending.get('requested_at')}"
            )
            self._set_text(self._appr_plan, pending.get("plan") or "")
            self._btn_approve.configure(state="normal")
            self._btn_reject.configure(state="normal")
        elif pending:
            self._appr_reason.configure(
                text=f"Last decision: {pending.get('status')} ({pending.get('source')})\n{pending.get('reason')}"
            )
            self._set_text(self._appr_plan, pending.get("plan") or "")
            self._btn_approve.configure(state="disabled")
            self._btn_reject.configure(state="disabled")
        else:
            self._appr_reason.configure(text="No pending approval.")
            self._set_text(self._appr_plan, "")
            self._btn_approve.configure(state="disabled")
            self._btn_reject.configure(state="disabled")

    def refresh_logs(self) -> None:
        state = load_state()
        hist = state.get("log_history") or []
        hist_text = "\n".join(
            f"[{e.get('timestamp')}] {e.get('level')} — {e.get('message')}" for e in hist[-100:]
        ) or "(empty)"
        self._set_text(self._log_history, hist_text)
        log_dir = Path(__file__).resolve().parent / "logs"
        file_tail = "(no log file yet)"
        if log_dir.exists():
            logs = sorted(log_dir.glob("agent_*.log"))
            if logs:
                text = logs[-1].read_text(encoding="utf-8", errors="replace")
                file_tail = "\n".join(text.splitlines()[-200:])
        self._set_text(self._log_file, file_tail)

    def refresh_chat(self, force: bool = False) -> None:
        history = load_state().get("chat_history") or []
        if force or len(history) != self._chat_len:
            self._render_chat_history(history)

    def refresh_settings(self) -> None:
        self._startup_var.set(is_startup_enabled())
        env = read_env_settings()
        import os

        for key, entry in self._settings_entries.items():
            val = env.get(key, os.getenv(key, ""))
            if key in SECRET_SETTINGS and val:
                val = mask_secret(val)
            entry.delete(0, "end")
            entry.insert(0, val)

    @staticmethod
    def _set_text(widget: ctk.CTkTextbox, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text or "")

    @staticmethod
    def _set_markdown(widget: tk.Text, markdown: str) -> None:
        from md_view import insert_markdown

        insert_markdown(widget, markdown or "")

    def _schedule_poll(self) -> None:
        self.refresh_dashboard()
        if self._active_page == "chat":
            self.refresh_chat()
        self._poll_job = self.after(3000, self._schedule_poll)

    def on_close(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def run_ui() -> None:
    app = ForemanApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_ui()
