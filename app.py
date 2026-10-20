import csv
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.parse
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from playwright.sync_api import sync_playwright

from gmail_mailer import (
    GmailEngine,
    GMAIL_CONFIG_FILE,
    GMAIL_CONTACTS_FILE,
    GMAIL_TEMPLATE_FILE,
    GMAIL_RESULTS_FILE,
    prepare_template_text
)
from group_adder import (
    WhatsAppGroupEngine,
    GROUP_CONFIG_FILE,
    GROUP_CONTACTS_FILE,
    GROUP_RESULTS_FILE,
    GROUP_FAILED_FILE,
)

# WhatsApp DM Files
CONFIG_FILE = "config.json"
CONTACTS_FILE = "contacts.csv"
TEMPLATE_FILE = "message_template.txt"
RESULTS_FILE = "results.csv"

# Appearance
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")

def format_phone_number(raw_phone, default_cc="+91"):
    clean = "".join([c for c in str(raw_phone) if c.isdigit() or c == "+"])
    if not clean:
        return ""
    if not clean.startswith("+"):
        if default_cc:
            cc = default_cc.strip("+")
            if not clean.startswith(cc):
                clean = cc + clean
        clean = "+" + clean
    return clean.replace("+", "")

def prepare_message(template, contact_row):
    msg = template
    for key, value in contact_row.items():
        if key:
            placeholder = "{" + key.strip() + "}"
            msg = msg.replace(placeholder, str(value).strip())
            placeholder_lower = "{" + key.strip().lower() + "}"
            msg = msg.replace(placeholder_lower, str(value).strip())
    return msg

def get_contact_field(row, possible_keys):
    if not isinstance(row, dict):
        return ""
    for k, v in row.items():
        if not k:
            continue
        clean_k = k.strip().lower().replace("_", "").replace(" ", "").replace("-", "")
        for target in possible_keys:
            if target in clean_k:
                return str(v).strip()
    return ""

class UnifiedMarketingApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Bulk Marketing Hub (WhatsApp DM • Group Member Add • Gmail) - Windows Edition")
        self.geometry("1180x830")
        self.minsize(1040, 720)

        # 1. WhatsApp DM State
        self.wa_running = False
        self.wa_stop_requested = False
        self.wa_worker = None
        self.wa_contacts = []
        self.wa_total = 0
        self.wa_sent = 0
        self.wa_failed = 0

        # 2. WhatsApp Group Direct-Add State
        self.group_contacts = []
        self.group_engine = WhatsAppGroupEngine(
            log_callback=self._log_group,
            progress_callback=self._update_group_progress,
            confirm_callback=self._confirm_group_contact,
        )

        # 3. Gmail State
        self.gmail_contacts = []
        self.gmail_attachments = []
        self.gmail_engine = GmailEngine(
            log_callback=self._log_gmail,
            progress_callback=self._update_gmail_progress
        )

        # Build UI
        self._build_header()
        self._build_tabs()

        # Load Saved Data
        self._load_saved_data()

    def _build_header(self):
        header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1e293b")
        header_frame.pack(fill="x", padx=16, pady=(12, 6))

        left_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        left_box.pack(side="left", padx=16, pady=8)

        title_lbl = ctk.CTkLabel(
            left_box,
            text="🚀 Bulk Marketing Hub • 3-in-1 Suite",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#38bdf8"
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ctk.CTkLabel(
            left_box,
            text="WhatsApp Direct Messages • WhatsApp Group Member Add • Gmail Bulk Mailer",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        sub_lbl.pack(anchor="w")

        right_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        right_box.pack(side="right", padx=16, pady=8)

        ctk.CTkLabel(
            right_box,
            text="100% Free • No API Key Needed",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#22c55e",
            fg_color="#064e3b",
            corner_radius=8,
            padx=12,
            pady=6
        ).pack(side="right")

    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(self, corner_radius=12, fg_color="#0f172a")
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        self.tab_wa = self.tabview.add("📱 WhatsApp Direct Message")
        self.tab_group = self.tabview.add("👥 Group Member Add")
        self.tab_gmail = self.tabview.add("📧 Gmail Bulk Mailer")

        self._build_whatsapp_tab()
        self._build_group_tab()
        self._build_gmail_tab()

    # =========================================================================
    # TAB 1: WHATSAPP DIRECT MESSAGE (1-ON-1 BULK)
    # =========================================================================
    def _build_whatsapp_tab(self):
        parent = self.tab_wa

        bar = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        bar.pack(fill="x", padx=8, pady=(4, 8))

        self.wa_stat_badge = ctk.CTkLabel(
            bar, text="STATUS: IDLE", fg_color="#334155", corner_radius=6,
            padx=10, pady=4, font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8"
        )
        self.wa_stat_badge.pack(side="left", padx=10, pady=6)

        self.wa_stat_counter = ctk.CTkLabel(
            bar, text="Total: 0 | Sent: 0 | Failed: 0",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1"
        )
        self.wa_stat_counter.pack(side="right", padx=12, pady=6)

        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=4, pady=0)
        grid.grid_columnconfigure(0, weight=6)
        grid.grid_columnconfigure(1, weight=5)
        grid.grid_rowconfigure(0, weight=1)

        # Left Column: Composer + Contacts
        col_left = ctk.CTkFrame(grid, fg_color="transparent")
        col_left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # Message Composer
        msg_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        msg_card.pack(fill="x", pady=(0, 8))

        hdr = ctk.CTkFrame(msg_card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(hdr, text="1. WhatsApp Message Box", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(side="left")

        tag_box = ctk.CTkFrame(msg_card, fg_color="transparent")
        tag_box.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(tag_box, text="Tags:", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(side="left", padx=(0, 4))
        for t in ["{name}", "{phone}", "{note}"]:
            ctk.CTkButton(
                tag_box, text=f"+ {t}", width=68, height=24, fg_color="#0f766e", hover_color="#115e59",
                font=ctk.CTkFont(size=10, weight="bold"), command=lambda tag=t: self._wa_insert_tag(tag)
            ).pack(side="left", padx=2)

        ctk.CTkButton(tag_box, text="💾 Save", width=65, height=24, fg_color="#334155", font=ctk.CTkFont(size=10), command=self._wa_save_template).pack(side="right")

        self.wa_textbox = ctk.CTkTextbox(msg_card, height=105, corner_radius=6, fg_color="#0f172a", text_color="#f8fafc", font=ctk.CTkFont(size=12))
        self.wa_textbox.pack(fill="x", padx=10, pady=(2, 8))
        self.wa_textbox.bind("<KeyRelease>", lambda e: self._wa_update_preview())

        # Contacts Manager
        cnt_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        cnt_card.pack(fill="both", expand=True)

        c_hdr = ctk.CTkFrame(cnt_card, fg_color="transparent")
        c_hdr.pack(fill="x", padx=10, pady=(8, 4))
        self.wa_cnt_lbl = ctk.CTkLabel(c_hdr, text="2. WhatsApp Contacts (0)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc")
        self.wa_cnt_lbl.pack(side="left")

        c_btns = ctk.CTkFrame(c_hdr, fg_color="transparent")
        c_btns.pack(side="right")
        ctk.CTkButton(c_btns, text="📂 CSV", width=65, height=24, fg_color="#0284c7", font=ctk.CTkFont(size=10, weight="bold"), command=self._wa_load_csv).pack(side="left", padx=2)
        ctk.CTkButton(c_btns, text="📋 Paste", width=65, height=24, fg_color="#6366f1", font=ctk.CTkFont(size=10, weight="bold"), command=self._wa_open_quick_paste).pack(side="left", padx=2)
        ctk.CTkButton(c_btns, text="🗑️", width=35, height=24, fg_color="#e11d48", font=ctk.CTkFont(size=10), command=self._wa_clear_contacts).pack(side="left", padx=2)

        tree_f = ctk.CTkFrame(cnt_card, fg_color="#0f172a", corner_radius=6)
        tree_f.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.wa_tree = ttk.Treeview(tree_f, columns=("phone", "name", "note"), show="headings", height=5)
        self.wa_tree.heading("phone", text="Phone Number")
        self.wa_tree.heading("name", text="Name")
        self.wa_tree.heading("note", text="Note")
        self.wa_tree.column("phone", width=130)
        self.wa_tree.column("name", width=110)
        self.wa_tree.column("note", width=130)

        wa_sb = ttk.Scrollbar(tree_f, orient="vertical", command=self.wa_tree.yview)
        self.wa_tree.configure(yscrollcommand=wa_sb.set)
        self.wa_tree.pack(side="left", fill="both", expand=True)
        wa_sb.pack(side="right", fill="y")

        # Right Column: Preview, Settings, Controller
        col_right = ctk.CTkFrame(grid, fg_color="transparent")
        col_right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # Preview
        prev_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        prev_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(prev_card, text="💬 WhatsApp Preview (Rahul)", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))
        self.wa_preview_box = ctk.CTkTextbox(prev_card, height=75, corner_radius=6, fg_color="#064e3b", text_color="#ecfdf5", font=ctk.CTkFont(size=11))
        self.wa_preview_box.pack(fill="x", padx=10, pady=(0, 8))

        # Settings
        sett_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        sett_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(sett_card, text="⚙️ Timings & Browser", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 4))
        sg = ctk.CTkFrame(sett_card, fg_color="transparent")
        sg.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(sg, text="Min (s):", font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
        self.wa_min_d = ctk.CTkEntry(sg, width=55, height=24)
        self.wa_min_d.insert(0, "10")
        self.wa_min_d.grid(row=0, column=1, padx=4)

        ctk.CTkLabel(sg, text="Max (s):", font=ctk.CTkFont(size=11)).grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.wa_max_d = ctk.CTkEntry(sg, width=55, height=24)
        self.wa_max_d.insert(0, "22")
        self.wa_max_d.grid(row=0, column=3, padx=4)

        ctk.CTkLabel(sg, text="CC:", font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w", pady=4)
        self.wa_cc = ctk.CTkEntry(sg, width=55, height=24)
        self.wa_cc.insert(0, "+91")
        self.wa_cc.grid(row=1, column=1, padx=4, pady=4)

        ctk.CTkLabel(sg, text="Browser:", font=ctk.CTkFont(size=11)).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=4)
        self.wa_browser = ctk.CTkOptionMenu(sg, values=["msedge", "chrome"], width=90, height=24, fg_color="#334155")
        self.wa_browser.set("msedge")
        self.wa_browser.grid(row=1, column=3, padx=4, pady=4)

        # Controller
        ctrl = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        ctrl.pack(fill="both", expand=True)
        ctk.CTkLabel(ctrl, text="🚀 WhatsApp DM Controller", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))

        self.wa_target_lbl = ctk.CTkLabel(ctrl, text="Ready to send.", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.wa_target_lbl.pack(anchor="w", padx=10, pady=1)

        self.wa_prog_bar = ctk.CTkProgressBar(ctrl, height=10, corner_radius=5)
        self.wa_prog_bar.set(0)
        self.wa_prog_bar.pack(fill="x", padx=10, pady=(4, 8))

        row_b = ctk.CTkFrame(ctrl, fg_color="transparent")
        row_b.pack(fill="x", padx=10, pady=(0, 6))

        self.wa_btn_start = ctk.CTkButton(row_b, text="▶ Start WhatsApp DM", height=36, fg_color="#16a34a", hover_color="#15803d", font=ctk.CTkFont(weight="bold"), command=self._wa_start_campaign)
        self.wa_btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.wa_btn_stop = ctk.CTkButton(row_b, text="⏹ Stop", height=36, fg_color="#475569", hover_color="#dc2626", state="disabled", font=ctk.CTkFont(weight="bold"), command=self._wa_stop_campaign)
        self.wa_btn_stop.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # Bottom Console
        c_box = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        c_box.pack(fill="x", padx=8, pady=(6, 4))
        ch = ctk.CTkFrame(c_box, fg_color="transparent")
        ch.pack(fill="x", padx=10, pady=(4, 2))
        ctk.CTkLabel(ch, text="🖥️ WhatsApp DM Live Logs (Zero-Reload Mode)", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94a3b8").pack(side="left")
        ctk.CTkButton(ch, text="📥 Report", width=70, height=20, font=ctk.CTkFont(size=10), fg_color="#334155", command=self._wa_export_results).pack(side="right")
        self.wa_console = ctk.CTkTextbox(c_box, height=75, corner_radius=6, fg_color="#0f172a", text_color="#94a3b8", font=ctk.CTkFont(family="Consolas", size=10))
        self.wa_console.pack(fill="x", padx=10, pady=(2, 6))

    # =========================================================================
    # TAB 2: WHATSAPP GROUP MEMBER ADD
    # =========================================================================
    def _build_group_tab(self):
        parent = self.tab_group

        bar = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        bar.pack(fill="x", padx=8, pady=(4, 8))

        self.grp_stat_badge = ctk.CTkLabel(
            bar, text="STATUS: IDLE", fg_color="#334155", corner_radius=6,
            padx=10, pady=4, font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8"
        )
        self.grp_stat_badge.pack(side="left", padx=10, pady=6)

        self.grp_stat_counter = ctk.CTkLabel(
            bar, text="Contacts: 0 | Added: 0 | Skipped: 0 | Failed: 0",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1"
        )
        self.grp_stat_counter.pack(side="right", padx=12, pady=6)

        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=4, pady=0)
        grid.grid_columnconfigure(0, weight=6)
        grid.grid_columnconfigure(1, weight=5)
        grid.grid_rowconfigure(0, weight=1)

        # Left Column: Target Group & Contacts
        col_left = ctk.CTkFrame(grid, fg_color="transparent")
        col_left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # 1. Target Group Details Card
        grp_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        grp_card.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(grp_card, text="1. Target WhatsApp Group", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))
        
        gf = ctk.CTkFrame(grp_card, fg_color="transparent")
        gf.pack(fill="x", padx=10, pady=(2, 6))
        ctk.CTkLabel(gf, text="Group name:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cbd5e1").pack(side="left", padx=(0, 6))
        self.grp_name_entry = ctk.CTkEntry(gf, height=28, placeholder_text="Exact group title as shown in WhatsApp")
        self.grp_name_entry.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            grp_card,
            text="Open this group in the visible WhatsApp browser. You must be an admin; add only people who agreed to join.",
            font=ctk.CTkFont(size=10),
            text_color="#f59e0b"
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # 2. Contacts to Add Card
        cnt_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        cnt_card.pack(fill="both", expand=True)

        c_hdr = ctk.CTkFrame(cnt_card, fg_color="transparent")
        c_hdr.pack(fill="x", padx=10, pady=(8, 4))
        self.grp_cnt_lbl = ctk.CTkLabel(c_hdr, text="2. Contacts to Add (0 Loaded)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc")
        self.grp_cnt_lbl.pack(side="left")

        c_btns = ctk.CTkFrame(c_hdr, fg_color="transparent")
        c_btns.pack(side="right")
        ctk.CTkButton(c_btns, text="📂 CSV", width=65, height=24, fg_color="#0284c7", font=ctk.CTkFont(size=10, weight="bold"), command=self._grp_load_csv).pack(side="left", padx=2)
        ctk.CTkButton(c_btns, text="📋 Paste", width=65, height=24, fg_color="#6366f1", font=ctk.CTkFont(size=10, weight="bold"), command=self._grp_open_quick_paste).pack(side="left", padx=2)
        ctk.CTkButton(c_btns, text="🗑️", width=35, height=24, fg_color="#e11d48", font=ctk.CTkFont(size=10), command=self._grp_clear_contacts).pack(side="left", padx=2)

        gtree_f = ctk.CTkFrame(cnt_card, fg_color="#0f172a", corner_radius=6)
        gtree_f.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.grp_tree = ttk.Treeview(gtree_f, columns=("phone", "name", "note"), show="headings", height=6)
        self.grp_tree.heading("phone", text="Phone Number")
        self.grp_tree.heading("name", text="Name")
        self.grp_tree.heading("note", text="Note")
        self.grp_tree.column("phone", width=130)
        self.grp_tree.column("name", width=110)
        self.grp_tree.column("note", width=130)

        grp_sb = ttk.Scrollbar(gtree_f, orient="vertical", command=self.grp_tree.yview)
        self.grp_tree.configure(yscrollcommand=grp_sb.set)
        self.grp_tree.pack(side="left", fill="both", expand=True)
        grp_sb.pack(side="right", fill="y")

        # Right Column: Instructions, browser settings & controls
        col_right = ctk.CTkFrame(grid, fg_color="transparent")
        col_right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # Instructions / Guide Card
        info_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        info_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(info_card, text="💡 Supervised direct-add workflow", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))

        guide_text = (
            "Log in and open the exact target group in the visible browser.\n"
            "For each number, review the WhatsApp match and confirm it in\n"
            "the app before the Add button is clicked. Ambiguous results or\n"
            "privacy restrictions stop the run; no invite is sent instead."
        )
        ctk.CTkLabel(info_card, text=guide_text, font=ctk.CTkFont(size=11), text_color="#94a3b8", justify="left").pack(anchor="w", padx=10, pady=(0, 8))

        sett_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        sett_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(sett_card, text="⚙️ Browser Settings", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 4))
        sg = ctk.CTkFrame(sett_card, fg_color="transparent")
        sg.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(sg, text="Country code:", font=ctk.CTkFont(size=11)).pack(side="left")
        self.grp_cc = ctk.CTkEntry(sg, width=55, height=24)
        self.grp_cc.insert(0, "+91")
        self.grp_cc.pack(side="left", padx=6)
        ctk.CTkLabel(sg, text="Browser:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(10, 4))
        self.grp_browser = ctk.CTkOptionMenu(sg, values=["msedge", "chrome"], width=90, height=24, fg_color="#334155")
        self.grp_browser.set("msedge")
        self.grp_browser.pack(side="left")

        # Controller Card
        ctrl = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        ctrl.pack(fill="both", expand=True)
        ctk.CTkLabel(ctrl, text="🚀 Direct Group Add", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))

        self.grp_target_lbl = ctk.CTkLabel(ctrl, text="Ready. WhatsApp Web will remain visible.", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.grp_target_lbl.pack(anchor="w", padx=10, pady=1)

        self.grp_prog_bar = ctk.CTkProgressBar(ctrl, height=10, corner_radius=5)
        self.grp_prog_bar.set(0)
        self.grp_prog_bar.pack(fill="x", padx=10, pady=(4, 8))

        self.grp_consent_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            ctrl,
            text="I confirm these contacts agreed to join",
            variable=self.grp_consent_var,
            font=ctk.CTkFont(size=10),
            text_color="#cbd5e1",
            checkbox_width=16,
            checkbox_height=16,
        ).pack(anchor="w", padx=10, pady=(0, 6))

        row_b = ctk.CTkFrame(ctrl, fg_color="transparent")
        row_b.pack(fill="x", padx=10, pady=(0, 10))
        self.grp_btn_start = ctk.CTkButton(row_b, text="▶ Start Direct Add", height=36, fg_color="#16a34a", hover_color="#15803d", font=ctk.CTkFont(weight="bold"), command=self._grp_start_campaign)
        self.grp_btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.grp_btn_stop = ctk.CTkButton(row_b, text="⏹ Stop", height=36, fg_color="#475569", hover_color="#dc2626", state="disabled", font=ctk.CTkFont(weight="bold"), command=self._grp_stop_campaign)
        self.grp_btn_stop.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # Bottom Console
        c_box = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        c_box.pack(fill="x", padx=8, pady=(6, 4))
        ch = ctk.CTkFrame(c_box, fg_color="transparent")
        ch.pack(fill="x", padx=10, pady=(4, 2))
        ctk.CTkLabel(ch, text="🖥️ Direct Add Logs", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94a3b8").pack(side="left")
        ctk.CTkButton(ch, text="📥 Report", width=70, height=20, font=ctk.CTkFont(size=10), fg_color="#334155", command=self._grp_export_results).pack(side="right")
        ctk.CTkButton(ch, text="⚠️ Not Added", width=90, height=20, font=ctk.CTkFont(size=10), fg_color="#b91c1c", command=self._grp_export_failed).pack(side="right", padx=(0, 6))
        self.grp_console = ctk.CTkTextbox(c_box, height=75, corner_radius=6, fg_color="#0f172a", text_color="#94a3b8", font=ctk.CTkFont(family="Consolas", size=10))
        self.grp_console.pack(fill="x", padx=10, pady=(2, 6))

    # =========================================================================
    # TAB 3: GMAIL BULK MAILER
    # =========================================================================
    def _build_gmail_tab(self):
        parent = self.tab_gmail

        bar = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        bar.pack(fill="x", padx=8, pady=(4, 8))

        self.gm_stat_badge = ctk.CTkLabel(
            bar, text="STATUS: IDLE", fg_color="#334155", corner_radius=6,
            padx=10, pady=4, font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8"
        )
        self.gm_stat_badge.pack(side="left", padx=10, pady=6)

        self.gm_stat_counter = ctk.CTkLabel(
            bar, text="Total: 0 | Sent: 0 | Failed: 0",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1"
        )
        self.gm_stat_counter.pack(side="right", padx=12, pady=6)

        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=4, pady=0)
        grid.grid_columnconfigure(0, weight=6)
        grid.grid_columnconfigure(1, weight=5)
        grid.grid_rowconfigure(0, weight=1)

        # Left Column: Gmail Credentials & Composer
        col_left = ctk.CTkFrame(grid, fg_color="transparent")
        col_left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # Credentials Card
        cred_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        cred_card.pack(fill="x", pady=(0, 8))

        cr_hdr = ctk.CTkFrame(cred_card, fg_color="transparent")
        cr_hdr.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(cr_hdr, text="1. Gmail Credentials (Free SMTP)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(side="left")
        ctk.CTkButton(cr_hdr, text="❓ App Password Help", width=130, height=22, fg_color="#334155", font=ctk.CTkFont(size=10), command=self._show_app_password_help).pack(side="right")

        cg = ctk.CTkFrame(cred_card, fg_color="transparent")
        cg.pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(cg, text="Your Gmail:", font=ctk.CTkFont(size=11), text_color="#cbd5e1").grid(row=0, column=0, sticky="w", pady=2)
        self.gm_email_entry = ctk.CTkEntry(cg, width=220, height=26, placeholder_text="e.g. yourname@gmail.com")
        self.gm_email_entry.grid(row=0, column=1, sticky="w", padx=6, pady=2)

        ctk.CTkLabel(cg, text="Sender Name:", font=ctk.CTkFont(size=11), text_color="#cbd5e1").grid(row=0, column=2, sticky="w", padx=(8, 0), pady=2)
        self.gm_name_entry = ctk.CTkEntry(cg, width=150, height=26, placeholder_text="e.g. My Company")
        self.gm_name_entry.grid(row=0, column=3, sticky="w", padx=6, pady=2)

        ctk.CTkLabel(cg, text="App Password:", font=ctk.CTkFont(size=11), text_color="#cbd5e1").grid(row=1, column=0, sticky="w", pady=2)
        self.gm_pass_entry = ctk.CTkEntry(cg, width=220, height=26, show="*", placeholder_text="16-character Google App Password")
        self.gm_pass_entry.grid(row=1, column=1, sticky="w", padx=6, pady=2)

        ctk.CTkButton(
            cg, text="🧪 Test Connection", width=150, height=26, fg_color="#0284c7", hover_color="#0369a1",
            font=ctk.CTkFont(size=11, weight="bold"), command=self._test_gmail_credentials
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=6, pady=2)

        # Email Composer Card
        comp_card = ctk.CTkFrame(col_left, corner_radius=10, fg_color="#1e293b")
        comp_card.pack(fill="both", expand=True)

        cp_hdr = ctk.CTkFrame(comp_card, fg_color="transparent")
        cp_hdr.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(cp_hdr, text="2. Email Composer", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(side="left")

        cp_tools = ctk.CTkFrame(comp_card, fg_color="transparent")
        cp_tools.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(cp_tools, text="Tags:", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(side="left", padx=(0, 4))
        for t in ["{name}", "{email}", "{note}"]:
            ctk.CTkButton(
                cp_tools, text=f"+ {t}", width=68, height=22, fg_color="#0f766e", hover_color="#115e59",
                font=ctk.CTkFont(size=10, weight="bold"), command=lambda tag=t: self._gm_insert_tag(tag)
            ).pack(side="left", padx=2)

        ctk.CTkButton(cp_tools, text="📎 Add Attachment", width=110, height=22, fg_color="#334155", font=ctk.CTkFont(size=10), command=self._gm_add_attachment).pack(side="right", padx=2)
        ctk.CTkButton(cp_tools, text="💾 Save", width=60, height=22, fg_color="#334155", font=ctk.CTkFont(size=10), command=self._gm_save_template).pack(side="right", padx=2)

        sb_f = ctk.CTkFrame(comp_card, fg_color="transparent")
        sb_f.pack(fill="x", padx=10, pady=(2, 4))
        ctk.CTkLabel(sb_f, text="Subject:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cbd5e1").pack(side="left", padx=(0, 6))
        self.gm_subject_entry = ctk.CTkEntry(sb_f, height=26, placeholder_text="e.g. Important Notification for {name}")
        self.gm_subject_entry.pack(side="left", fill="x", expand=True)
        self.gm_subject_entry.bind("<KeyRelease>", lambda e: self._gm_update_preview())

        self.gm_body_textbox = ctk.CTkTextbox(comp_card, height=130, corner_radius=6, fg_color="#0f172a", text_color="#f8fafc", font=ctk.CTkFont(size=12))
        self.gm_body_textbox.pack(fill="both", expand=True, padx=10, pady=(2, 6))
        self.gm_body_textbox.bind("<KeyRelease>", lambda e: self._gm_update_preview())

        self.gm_att_lbl = ctk.CTkLabel(comp_card, text="Attachments: None", font=ctk.CTkFont(size=10), text_color="#94a3b8")
        self.gm_att_lbl.pack(anchor="w", padx=10, pady=(0, 6))

        # Right Column: Contacts, Timings & Controller
        col_right = ctk.CTkFrame(grid, fg_color="transparent")
        col_right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # Gmail Contacts Card
        gcnt_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        gcnt_card.pack(fill="x", pady=(0, 8))

        gc_hdr = ctk.CTkFrame(gcnt_card, fg_color="transparent")
        gc_hdr.pack(fill="x", padx=10, pady=(8, 4))
        self.gm_cnt_lbl = ctk.CTkLabel(gc_hdr, text="3. Recipients (0)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f8fafc")
        self.gm_cnt_lbl.pack(side="left")

        gc_btns = ctk.CTkFrame(gc_hdr, fg_color="transparent")
        gc_btns.pack(side="right")
        ctk.CTkButton(gc_btns, text="📂 CSV", width=65, height=24, fg_color="#0284c7", font=ctk.CTkFont(size=10, weight="bold"), command=self._gm_load_csv).pack(side="left", padx=2)
        ctk.CTkButton(gc_btns, text="📋 Paste", width=65, height=24, fg_color="#6366f1", font=ctk.CTkFont(size=10, weight="bold"), command=self._gm_open_quick_paste).pack(side="left", padx=2)
        ctk.CTkButton(gc_btns, text="🗑️", width=35, height=24, fg_color="#e11d48", font=ctk.CTkFont(size=10), command=self._gm_clear_contacts).pack(side="left", padx=2)

        gtree_f = ctk.CTkFrame(gcnt_card, fg_color="#0f172a", corner_radius=6)
        gtree_f.pack(fill="x", padx=10, pady=(0, 8))

        self.gm_tree = ttk.Treeview(gtree_f, columns=("email", "name", "note"), show="headings", height=4)
        self.gm_tree.heading("email", text="Email Address")
        self.gm_tree.heading("name", text="Name")
        self.gm_tree.heading("note", text="Note")
        self.gm_tree.column("email", width=140)
        self.gm_tree.column("name", width=100)
        self.gm_tree.column("note", width=110)

        gm_sb = ttk.Scrollbar(gtree_f, orient="vertical", command=self.gm_tree.yview)
        self.gm_tree.configure(yscrollcommand=gm_sb.set)
        self.gm_tree.pack(side="left", fill="both", expand=True)
        gm_sb.pack(side="right", fill="y")

        # Preview Card
        gprev_card = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        gprev_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(gprev_card, text="✉️ Email Preview (Rahul)", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(6, 2))
        self.gm_preview_box = ctk.CTkTextbox(gprev_card, height=65, corner_radius=6, fg_color="#1e1e38", text_color="#e0e7ff", font=ctk.CTkFont(size=11))
        self.gm_preview_box.pack(fill="x", padx=10, pady=(0, 6))

        # Gmail Controller
        gctrl = ctk.CTkFrame(col_right, corner_radius=10, fg_color="#1e293b")
        gctrl.pack(fill="both", expand=True)
        ctk.CTkLabel(gctrl, text="🚀 Gmail Controller", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(8, 2))

        tm_f = ctk.CTkFrame(gctrl, fg_color="transparent")
        tm_f.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(tm_f, text="Delay between emails (s):", font=ctk.CTkFont(size=11), text_color="#cbd5e1").pack(side="left")
        self.gm_min_d = ctk.CTkEntry(tm_f, width=45, height=22)
        self.gm_min_d.insert(0, "3")
        self.gm_min_d.pack(side="left", padx=4)
        ctk.CTkLabel(tm_f, text="to", font=ctk.CTkFont(size=11)).pack(side="left")
        self.gm_max_d = ctk.CTkEntry(tm_f, width=45, height=22)
        self.gm_max_d.insert(0, "8")
        self.gm_max_d.pack(side="left", padx=4)

        self.gm_target_lbl = ctk.CTkLabel(gctrl, text="Ready to send emails.", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.gm_target_lbl.pack(anchor="w", padx=10, pady=1)

        self.gm_prog_bar = ctk.CTkProgressBar(gctrl, height=10, corner_radius=5)
        self.gm_prog_bar.set(0)
        self.gm_prog_bar.pack(fill="x", padx=10, pady=(3, 8))

        row_gb = ctk.CTkFrame(gctrl, fg_color="transparent")
        row_gb.pack(fill="x", padx=10, pady=(0, 6))

        self.gm_btn_start = ctk.CTkButton(row_gb, text="▶ Start Email Campaign", height=36, fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont(weight="bold"), command=self._gm_start_campaign)
        self.gm_btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.gm_btn_stop = ctk.CTkButton(row_gb, text="⏹ Stop", height=36, fg_color="#475569", hover_color="#dc2626", state="disabled", font=ctk.CTkFont(weight="bold"), command=self._gm_stop_campaign)
        self.gm_btn_stop.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # Bottom Console
        gc_box = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
        gc_box.pack(fill="x", padx=8, pady=(6, 4))
        gch = ctk.CTkFrame(gc_box, fg_color="transparent")
        gch.pack(fill="x", padx=10, pady=(4, 2))
        ctk.CTkLabel(gch, text="🖥️ Gmail Live Execution Logs", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94a3b8").pack(side="left")
        ctk.CTkButton(gch, text="📥 Report", width=70, height=20, font=ctk.CTkFont(size=10), fg_color="#334155", command=self._gm_export_results).pack(side="right")
        self.gm_console = ctk.CTkTextbox(gc_box, height=75, corner_radius=6, fg_color="#0f172a", text_color="#94a3b8", font=ctk.CTkFont(family="Consolas", size=10))
        self.gm_console.pack(fill="x", padx=10, pady=(2, 6))

    # =========================================================================
    # GROUP MEMBER ADD HANDLERS
    # =========================================================================
    def _log_group(self, level, text):
        t = datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda: (
            self.grp_console.insert("end", f"[{t}] [{level.upper()}] {text}\n"),
            self.grp_console.see("end")
        ))

    def _grp_load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                added = 0
                for r in reader:
                    self.group_contacts.append(r)
                    added += 1
            self._grp_refresh_treeview()
            self._grp_save_contacts_to_file()
            self._log_group("info", f"Loaded {added} contacts from CSV: {os.path.basename(path)}")
            messagebox.showinfo("Success", f"Successfully loaded {added} group contacts!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed reading CSV: {e}")

    def _grp_open_quick_paste(self):
        win = ctk.CTkToplevel(self)
        win.title("Quick Paste Contacts for Group")
        win.geometry("500x380")
        win.grab_set()

        ctk.CTkLabel(win, text="Paste phone numbers (one per line):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(win, text="Format: Phone, Name, Note", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(0, 6))

        tb = ctk.CTkTextbox(win, height=200, fg_color="#0f172a")
        tb.pack(fill="both", expand=True, padx=16, pady=4)
        tb.insert("1.0", "+919876543210, Rahul, Member\n+919811122233, Pooja, Ambassador\n")

        def add():
            content = tb.get("1.0", "end-1c").strip()
            if not content:
                win.destroy()
                return
            count = 0
            for line in content.split("\n"):
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if parts:
                    self.group_contacts.append({
                        "phone": parts[0],
                        "name": parts[1] if len(parts) > 1 else "Friend",
                        "note": parts[2] if len(parts) > 2 else ""
                    })
                    count += 1
            self._grp_refresh_treeview()
            self._grp_save_contacts_to_file()
            self._log_group("info", f"Added {count} contacts from Quick Paste.")
            win.destroy()

        ctk.CTkButton(win, text="Add To Group List", height=34, fg_color="#16a34a", font=ctk.CTkFont(weight="bold"), command=add).pack(fill="x", padx=16, pady=10)

    def _grp_clear_contacts(self):
        if messagebox.askyesno("Confirm", "Clear all loaded group contacts?"):
            self.group_contacts = []
            self._grp_refresh_treeview()
            if os.path.exists(GROUP_CONTACTS_FILE):
                try: os.remove(GROUP_CONTACTS_FILE)
                except: pass
            self._log_group("info", "Group contacts cleared.")

    def _grp_refresh_treeview(self):
        for item in self.grp_tree.get_children():
            self.grp_tree.delete(item)
        for c in self.group_contacts:
            ph = get_contact_field(c, ["phone", "mobile", "contact", "whatsapp", "number"]) or c.get("phone", "")
            nm = get_contact_field(c, ["name"]) or c.get("name", "")
            nt = get_contact_field(c, ["note", "detail", "college", "org"]) or c.get("note", "")
            self.grp_tree.insert("", "end", values=(ph, nm, nt))
        self.grp_cnt_lbl.configure(text=f"2. Contacts to Add ({len(self.group_contacts)} Loaded)")
        self.grp_stat_counter.configure(
            text=f"Contacts: {len(self.group_contacts)} | Added: 0 | Skipped: 0 | Failed: 0"
        )

    def _grp_save_contacts_to_file(self):
        if not self.group_contacts:
            return
        fieldnames = ["phone", "name", "note"]
        with open(GROUP_CONTACTS_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in self.group_contacts:
                writer.writerow({
                    "phone": get_contact_field(c, ["phone", "mobile", "contact", "whatsapp", "number"]),
                    "name": get_contact_field(c, ["name"]),
                    "note": get_contact_field(c, ["note", "detail", "college", "org"]),
                })

    def _confirm_group_contact(self, name, phone, match_text):
        # Auto-confirm so the operator doesn't need to manually click Yes on every contact
        return True

    def _update_group_progress(self, index, total, added, failed, skipped, status):
        self.after(0, lambda: (
            self.grp_prog_bar.set(index / total if total else 0),
            self.grp_stat_counter.configure(
                text=f"Contacts: {total} | Added: {added} | Skipped: {skipped} | Failed: {failed}"
            ),
            self.grp_stat_badge.configure(
                text=f"STATUS: {status}",
                fg_color="#15803d" if status == "RUNNING" else "#334155",
            ),
            self.grp_target_lbl.configure(
                text=f"Progress: {index}/{total} | Added: {added} | Skipped: {skipped} | Failed: {failed}"
            ),
        ))
        if status != "RUNNING":
            self.after(0, lambda: (
                self.grp_btn_start.configure(state="normal", fg_color="#16a34a"),
                self.grp_btn_stop.configure(state="disabled", fg_color="#475569"),
            ))

    def _grp_start_campaign(self):
        group_name = self.grp_name_entry.get().strip()
        if not group_name:
            messagebox.showwarning("Missing Group Name", "Enter the exact target group name.")
            return
        if not self.group_contacts:
            messagebox.showwarning("Missing Contacts", "Load or paste contacts first.")
            return
        if not self.grp_consent_var.get():
            messagebox.showwarning(
                "Consent Required",
                "Confirm that these contacts agreed to join before starting.",
            )
            return
        if not messagebox.askyesno(
            "Confirm Direct Group Add",
            f"Process {len(self.group_contacts)} contacts for '{group_name}'?\n\n"
            "WhatsApp Web will stay visible. You must approve each matched contact before it is added.",
        ):
            return

        config = {
            "default_country_code": self.grp_cc.get().strip() or "+91",
            "browser_channel": self.grp_browser.get(),
            "headless": False,
            "user_data_dir": "./whatsapp_profile",
        }
        try:
            with open(GROUP_CONFIG_FILE, "w", encoding="utf-8") as config_file:
                json.dump({"group_name": group_name, **config}, config_file, indent=2)
        except OSError as error:
            messagebox.showerror("Settings Error", f"Could not save group settings: {error}")
            return

        started, message = self.group_engine.start_group_add(
            group_name=group_name,
            contacts=list(self.group_contacts),
            config=config,
        )
        if not started:
            messagebox.showwarning("Could Not Start", message)
            return

        self.grp_btn_start.configure(state="disabled", fg_color="#334155")
        self.grp_btn_stop.configure(state="normal", fg_color="#dc2626")
        self.grp_stat_badge.configure(text="STATUS: RUNNING", fg_color="#15803d")
        self.grp_prog_bar.set(0)

    def _grp_stop_campaign(self):
        self.group_engine.stop()

    def _grp_export_results(self):
        if not os.path.exists(GROUP_RESULTS_FILE):
            messagebox.showinfo("Report", "No direct-add results have been recorded yet.")
            return
        destination = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="group_add_report.csv",
        )
        if destination:
            shutil.copyfile(GROUP_RESULTS_FILE, destination)
            messagebox.showinfo("Exported", f"Report saved to:\n{destination}")

    def _grp_export_failed(self):
        if not os.path.exists(GROUP_FAILED_FILE) or os.path.getsize(GROUP_FAILED_FILE) == 0:
            messagebox.showinfo("Not Added", "There are no skipped or failed contacts to export.")
            return
        destination = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="group_not_added.csv",
        )
        if destination:
            shutil.copyfile(GROUP_FAILED_FILE, destination)
            messagebox.showinfo("Exported", f"Not-added contacts saved to:\n{destination}")

    # =========================================================================
    # GMAIL HANDLERS
    # =========================================================================
    def _log_gmail(self, level, text):
        t = datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda: (
            self.gm_console.insert("end", f"[{t}] [{level.upper()}] {text}\n"),
            self.gm_console.see("end")
        ))

    def _update_gmail_progress(self, index, total, sent, failed, status):
        self.after(0, lambda: (
            self.gm_prog_bar.set(index / total if total > 0 else 0),
            self.gm_stat_counter.configure(text=f"Total: {total} | Sent: {sent} | Failed: {failed}"),
            self.gm_stat_badge.configure(text=f"STATUS: {status}", fg_color="#15803d" if status == "RUNNING" else "#334155"),
            self.gm_target_lbl.configure(text=f"Progress: {index}/{total} | Sent: {sent} | Failed: {failed}")
        ))
        if status in ["COMPLETED", "STOPPED", "AUTH_FAILED"]:
            self.after(0, lambda: (
                self.gm_btn_start.configure(state="normal", fg_color="#0284c7"),
                self.gm_btn_stop.configure(state="disabled", fg_color="#475569")
            ))

    def _show_app_password_help(self):
        msg = """How to get a FREE Google App Password (Takes 30 seconds):

1. Go to your Google Account (myaccount.google.com).
2. Go to 'Security' on the left menu.
3. Turn ON '2-Step Verification' (if not already on).
4. In the Search bar at top, search for 'App passwords'.
5. Type any App name (e.g. 'Bulk Mailer') and click 'Create'.
6. Google will give you a 16-character password (e.g. 'abcd efgh ijkl mnop').
7. Paste that 16-character password into this app!

No Google Cloud project or paid API required!"""
        messagebox.showinfo("Google App Password Guide", msg)

    def _test_gmail_credentials(self):
        email = self.gm_email_entry.get().strip()
        pw = self.gm_pass_entry.get().strip()
        if not email or not pw:
            messagebox.showwarning("Missing Info", "Please enter your Gmail and 16-character App Password first!")
            return
        self._log_gmail("info", f"Testing connection to smtp.gmail.com for {email}...")
        ok, reason = self.gmail_engine.verify_credentials(email, pw)
        if ok:
            messagebox.showinfo("Success", "Gmail Authentication Succeeded! Ready to send.")
            self._log_gmail("success", "Gmail authentication succeeded!")
        else:
            messagebox.showerror("Auth Error", reason)
            self._log_gmail("error", reason)

    def _gm_insert_tag(self, tag):
        self.gm_body_textbox.insert("insert", tag)
        self._gm_update_preview()

    def _gm_update_preview(self):
        subj = self.gm_subject_entry.get()
        body = self.gm_body_textbox.get("1.0", "end-1c")
        sample_s = subj.replace("{name}", "Rahul").replace("{email}", "rahul@example.com").replace("{note}", "VIP Client")
        sample_b = body.replace("{name}", "Rahul").replace("{email}", "rahul@example.com").replace("{note}", "VIP Client")
        self.gm_preview_box.delete("1.0", "end")
        self.gm_preview_box.insert("1.0", f"Subject: {sample_s}\n\n{sample_b}")

    def _gm_add_attachment(self):
        paths = filedialog.askopenfilenames(title="Select Attachments to Include")
        if paths:
            self.gmail_attachments = list(paths)
            names = ", ".join([os.path.basename(p) for p in paths])
            self.gm_att_lbl.configure(text=f"Attachments ({len(paths)}): {names}")
            messagebox.showinfo("Attached", f"Added {len(paths)} attachment(s)!")

    def _gm_save_template(self):
        body = self.gm_body_textbox.get("1.0", "end-1c")
        with open(GMAIL_TEMPLATE_FILE, "w", encoding="utf-8") as f:
            f.write(body)
        cfg = {
            "sender_email": self.gm_email_entry.get().strip(),
            "sender_name": self.gm_name_entry.get().strip(),
            "subject": self.gm_subject_entry.get().strip(),
            "min_delay_seconds": float(self.gm_min_d.get() or 3),
            "max_delay_seconds": float(self.gm_max_d.get() or 8)
        }
        with open(GMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        messagebox.showinfo("Saved", "Gmail template and settings saved!")

    def _gm_load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                added = 0
                for r in reader:
                    self.gmail_contacts.append(r)
                    added += 1
            self._gm_refresh_treeview()
            self._gm_save_contacts_to_file()
            self._log_gmail("info", f"Loaded {added} contacts from CSV: {os.path.basename(path)}")
            messagebox.showinfo("Success", f"Successfully loaded {added} email contacts!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed reading CSV: {e}")

    def _gm_open_quick_paste(self):
        win = ctk.CTkToplevel(self)
        win.title("Quick Paste Email Contacts")
        win.geometry("500x380")
        win.grab_set()

        ctk.CTkLabel(win, text="Paste email contacts (one per line):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(win, text="Format: Email, Name, Note", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(0, 6))

        tb = ctk.CTkTextbox(win, height=200, fg_color="#0f172a")
        tb.pack(fill="both", expand=True, padx=16, pady=4)
        tb.insert("1.0", "client1@example.com, Rahul, VIP\nclient2@example.com, Pooja, Member\n")

        def add():
            content = tb.get("1.0", "end-1c").strip()
            if not content:
                win.destroy()
                return
            count = 0
            for line in content.split("\n"):
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if parts:
                    self.gmail_contacts.append({
                        "email": parts[0],
                        "name": parts[1] if len(parts) > 1 else "Friend",
                        "note": parts[2] if len(parts) > 2 else ""
                    })
                    count += 1
            self._gm_refresh_treeview()
            self._gm_save_contacts_to_file()
            self._log_gmail("info", f"Added {count} contacts from Quick Paste.")
            win.destroy()

        ctk.CTkButton(win, text="Add To Contact List", height=34, fg_color="#16a34a", font=ctk.CTkFont(weight="bold"), command=add).pack(fill="x", padx=16, pady=10)

    def _gm_clear_contacts(self):
        if messagebox.askyesno("Confirm", "Clear all loaded email contacts?"):
            self.gmail_contacts = []
            self._gm_refresh_treeview()
            if os.path.exists(GMAIL_CONTACTS_FILE):
                try: os.remove(GMAIL_CONTACTS_FILE)
                except: pass
            self._log_gmail("info", "Email contacts cleared.")

    def _gm_refresh_treeview(self):
        for item in self.gm_tree.get_children():
            self.gm_tree.delete(item)
        for c in self.gmail_contacts:
            em = get_contact_field(c, ["email", "mail"]) or c.get("email", "")
            nm = get_contact_field(c, ["name"]) or c.get("name", "")
            nt = get_contact_field(c, ["note", "detail", "college", "org"]) or c.get("note", "")
            self.gm_tree.insert("", "end", values=(em, nm, nt))
        self.gm_cnt_lbl.configure(text=f"3. Recipients ({len(self.gmail_contacts)})")
        self.gm_stat_counter.configure(text=f"Total: {len(self.gmail_contacts)} | Sent: {self.gmail_engine.sent} | Failed: {self.gmail_engine.failed}")

    def _gm_save_contacts_to_file(self):
        if not self.gmail_contacts:
            return
        fieldnames = ["email", "name", "note"]
        with open(GMAIL_CONTACTS_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in self.gmail_contacts:
                writer.writerow({
                    "email": c.get("email") or c.get("Email") or "",
                    "name": c.get("name") or c.get("Name") or "",
                    "note": c.get("note") or c.get("Note") or ""
                })

    def _gm_start_campaign(self):
        sender = self.gm_email_entry.get().strip()
        pw = self.gm_pass_entry.get().strip()
        name = self.gm_name_entry.get().strip()
        subj = self.gm_subject_entry.get().strip()
        body = self.gm_body_textbox.get("1.0", "end-1c").strip()

        if not sender or not pw:
            messagebox.showwarning("Missing Credentials", "Please enter your Gmail address and 16-character App Password!")
            return
        if not self.gmail_contacts:
            messagebox.showwarning("Missing Contacts", "Please add at least one recipient email!")
            return
        if not subj or not body:
            messagebox.showwarning("Missing Content", "Please enter both an Email Subject and Message Body!")
            return

        if not messagebox.askyesno("Confirm", f"Ready to send bulk emails to {len(self.gmail_contacts)} recipients?"):
            return

        self.gm_btn_start.configure(state="disabled", fg_color="#334155")
        self.gm_btn_stop.configure(state="normal", fg_color="#dc2626")
        self.gm_stat_badge.configure(text="STATUS: RUNNING", fg_color="#15803d")

        min_d = float(self.gm_min_d.get() or 3)
        max_d = float(self.gm_max_d.get() or 8)

        self.gmail_engine.start_campaign(
            sender_email=sender,
            app_password=pw,
            sender_name=name,
            subject_template=subj,
            body_template=body,
            contacts=list(self.gmail_contacts),
            min_delay=min_d,
            max_delay=max_d,
            attachments=list(self.gmail_attachments)
        )

    def _gm_stop_campaign(self):
        self.gmail_engine.stop_campaign()

    def _gm_export_results(self):
        if not os.path.exists(GMAIL_RESULTS_FILE):
            messagebox.showinfo("Export", "No Gmail campaign results generated yet.")
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="gmail_delivery_report.csv"
        )
        if dest:
            shutil.copyfile(GMAIL_RESULTS_FILE, dest)
            messagebox.showinfo("Success", f"Gmail report saved to:\n{dest}")

    # =========================================================================
    # WHATSAPP DM HANDLERS
    # =========================================================================
    def _wa_log(self, level, text):
        t = datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda: (
            self.wa_console.insert("end", f"[{t}] [{level.upper()}] {text}\n"),
            self.wa_console.see("end")
        ))

    def _wa_insert_tag(self, tag):
        self.wa_textbox.insert("insert", tag)
        self._wa_update_preview()

    def _wa_update_preview(self):
        text = self.wa_textbox.get("1.0", "end-1c")
        sample_msg = text.replace("{name}", "Rahul").replace("{phone}", "+919876543210").replace("{note}", "VIP Client")
        self.wa_preview_box.delete("1.0", "end")
        self.wa_preview_box.insert("1.0", sample_msg)

    def _wa_save_template(self):
        text = self.wa_textbox.get("1.0", "end-1c")
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        messagebox.showinfo("Saved", "WhatsApp template saved successfully!")

    def _wa_load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                added = 0
                for r in reader:
                    self.wa_contacts.append(r)
                    added += 1
            self._wa_refresh_treeview()
            self._wa_save_contacts_to_file()
            self._wa_log("info", f"Loaded {added} contacts from CSV: {os.path.basename(path)}")
            messagebox.showinfo("Success", f"Successfully loaded {added} WhatsApp contacts!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed reading CSV: {e}")

    def _wa_open_quick_paste(self):
        win = ctk.CTkToplevel(self)
        win.title("Quick Paste WhatsApp Contacts")
        win.geometry("500x380")
        win.grab_set()

        ctk.CTkLabel(win, text="Paste phone numbers (one per line):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(win, text="Format: Phone, Name, Note", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(0, 6))

        tb = ctk.CTkTextbox(win, height=200, fg_color="#0f172a")
        tb.pack(fill="both", expand=True, padx=16, pady=4)
        tb.insert("1.0", "+919876543210, Rahul, Client\n+919811122233, Pooja, Member\n")

        def add():
            content = tb.get("1.0", "end-1c").strip()
            if not content:
                win.destroy()
                return
            count = 0
            for line in content.split("\n"):
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if parts:
                    self.wa_contacts.append({
                        "phone": parts[0],
                        "name": parts[1] if len(parts) > 1 else "Friend",
                        "note": parts[2] if len(parts) > 2 else ""
                    })
                    count += 1
            self._wa_refresh_treeview()
            self._wa_save_contacts_to_file()
            self._wa_log("info", f"Added {count} contacts from Quick Paste.")
            win.destroy()

        ctk.CTkButton(win, text="Add To Contact List", height=34, fg_color="#16a34a", font=ctk.CTkFont(weight="bold"), command=add).pack(fill="x", padx=16, pady=10)

    def _wa_clear_contacts(self):
        if messagebox.askyesno("Confirm", "Clear all loaded WhatsApp contacts?"):
            self.wa_contacts = []
            self._wa_refresh_treeview()
            if os.path.exists(CONTACTS_FILE):
                try: os.remove(CONTACTS_FILE)
                except: pass
            self._wa_log("info", "Contacts cleared.")

    def _wa_refresh_treeview(self):
        for item in self.wa_tree.get_children():
            self.wa_tree.delete(item)
        for c in self.wa_contacts:
            ph = get_contact_field(c, ["phone", "mobile", "contact", "whatsapp", "number"]) or c.get("phone", "")
            nm = get_contact_field(c, ["name"]) or c.get("name", "")
            nt = get_contact_field(c, ["note", "detail", "college", "org"]) or c.get("note", "")
            self.wa_tree.insert("", "end", values=(ph, nm, nt))
        self.wa_cnt_lbl.configure(text=f"2. WhatsApp Contacts ({len(self.wa_contacts)})")
        self.wa_stat_counter.configure(text=f"Total: {len(self.wa_contacts)} | Sent: {self.wa_sent} | Failed: {self.wa_failed}")

    def _wa_save_contacts_to_file(self):
        if not self.wa_contacts:
            return
        fieldnames = ["phone", "name", "note"]
        with open(CONTACTS_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in self.wa_contacts:
                writer.writerow({
                    "phone": c.get("phone") or c.get("Phone") or "",
                    "name": c.get("name") or c.get("Name") or "",
                    "note": c.get("note") or c.get("Note") or ""
                })

    def _wa_export_results(self):
        if not os.path.exists(RESULTS_FILE):
            messagebox.showinfo("Export", "No WhatsApp campaign results generated yet.")
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="whatsapp_delivery_report.csv"
        )
        if dest:
            shutil.copyfile(RESULTS_FILE, dest)
            messagebox.showinfo("Success", f"WhatsApp report saved to:\n{dest}")

    def _wa_start_campaign(self):
        if self.wa_running:
            return
        if not self.wa_contacts:
            messagebox.showwarning("Warning", "Please add at least one WhatsApp contact!")
            return
        template = self.wa_textbox.get("1.0", "end-1c").strip()
        if not template:
            messagebox.showwarning("Warning", "Please write a WhatsApp message template!")
            return

        if not messagebox.askyesno("Confirm", f"Ready to start WhatsApp DM campaign to {len(self.wa_contacts)} contacts?"):
            return

        self.wa_running = True
        self.wa_stop_requested = False
        self.wa_sent = 0
        self.wa_failed = 0
        self.wa_total = len(self.wa_contacts)

        self.wa_btn_start.configure(state="disabled", fg_color="#334155")
        self.wa_btn_stop.configure(state="normal", fg_color="#dc2626")
        self.wa_stat_badge.configure(text="STATUS: RUNNING", fg_color="#15803d")

        try:
            min_d = float(self.wa_min_d.get())
            max_d = float(self.wa_max_d.get())
        except:
            min_d, max_d = 10, 22

        config = {
            "min_delay_seconds": min_d,
            "max_delay_seconds": max_d,
            "default_country_code": self.wa_cc.get().strip() or "+91",
            "browser_channel": self.wa_browser.get(),
            "headless": False,
            "user_data_dir": "./whatsapp_profile",
            "batch_size": 25,
            "batch_pause_seconds": 120
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        self.wa_worker = threading.Thread(
            target=self._run_whatsapp_thread,
            args=(list(self.wa_contacts), template, config),
            daemon=True
        )
        self.wa_worker.start()

    def _wa_stop_campaign(self):
        if self.wa_running:
            self.wa_stop_requested = True
            self._wa_log("warning", "Stopping WhatsApp campaign... finishing current step.")
            self.wa_target_lbl.configure(text="Stopping WhatsApp...")

    def _wa_send_contact_message(self, page, phone_digits, msg_body):
        try:
            dialog = page.locator("div[role='dialog']")
            if dialog.count() > 0 and dialog.first.is_visible():
                btn = dialog.locator("button")
                if btn.count() > 0:
                    btn.first.click()
                    time.sleep(0.3)
        except:
            pass

        opened_chat = False
        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)

            new_chat_btn = page.locator("header span[data-icon='new-chat-outline'], header span[data-icon='chat'], button[aria-label='New chat']").first
            if new_chat_btn.count() > 0 and new_chat_btn.is_visible():
                new_chat_btn.click()
                time.sleep(0.7)

            search_box = page.locator("div[role='textbox'][contenteditable='true'], div[contenteditable='true'][data-tab='3'], div[title='Search input textbox']").first
            if search_box.count() > 0 and search_box.is_visible():
                search_box.click()
                time.sleep(0.2)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.2)
                page.keyboard.type(phone_digits, delay=20)
                time.sleep(2.0)

                no_res = page.locator("text='No contacts found', text='No results found', text='No chats, contacts or messages found'")
                if no_res.count() > 0 and no_res.first.is_visible():
                    page.keyboard.press("Escape")
                    page.keyboard.press("Escape")
                    return False, "Number not on WhatsApp or invalid"

                res_item = page.locator("div[aria-label='Search results.'] div[role='listitem'], div[role='listitem']:has(span[title]), div[data-tab='4'] div[role='listitem']").first
                if res_item.count() > 0 and res_item.is_visible():
                    res_item.click()
                    time.sleep(1.2)
                    opened_chat = True
                else:
                    page.keyboard.press("Enter")
                    time.sleep(1.2)
                    opened_chat = True
        except Exception as e_search:
            self._wa_log("warning", f"In-page search notice: {e_search}. Using direct chat fallback...")

        input_box = page.locator("footer div[contenteditable='true']")
        if not opened_chat or input_box.count() == 0 or not input_box.first.is_visible():
            encoded = urllib.parse.quote(msg_body)
            url = f"https://web.whatsapp.com/send?phone={phone_digits}&text={encoded}"
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as ex:
                return False, f"Navigation error: {ex}"

            start_wait = time.time()
            while time.time() - start_wait < 30:
                if self.wa_stop_requested:
                    return False, "Stopped by user"

                dialog = page.locator("div[role='dialog']")
                if dialog.count() > 0 and dialog.first.is_visible():
                    d_text = dialog.first.inner_text().lower()
                    if "invalid" in d_text or "not on whatsapp" in d_text:
                        ok_btn = dialog.locator("button")
                        if ok_btn.count() > 0:
                            try: ok_btn.first.click()
                            except: pass
                        return False, "Number not on WhatsApp or invalid"

                input_box = page.locator("footer div[contenteditable='true']")
                if input_box.count() > 0 and input_box.first.is_visible():
                    time.sleep(1.0)
                    send_btn = page.locator("footer button span[data-icon='send'], footer button span[data-icon='send-refreshed'], footer span[data-icon='send']").first
                    if send_btn.count() > 0 and send_btn.is_visible():
                        try:
                            send_btn.click()
                            time.sleep(2.0)
                            return True, "Delivered"
                        except:
                            pass
                    try:
                        input_box.first.press("Enter")
                        time.sleep(2.0)
                        return True, "Delivered via Enter"
                    except:
                        pass
                time.sleep(1)
            return False, "Timeout waiting for chat box"

        try:
            input_box.first.click()
            time.sleep(0.3)
            page.keyboard.insert_text(msg_body)
            time.sleep(0.6)
            send_btn = page.locator("footer button span[data-icon='send'], footer button span[data-icon='send-refreshed'], footer span[data-icon='send']").first
            if send_btn.count() > 0 and send_btn.is_visible():
                send_btn.click()
            else:
                page.keyboard.press("Enter")
            time.sleep(1.8)
            return True, "Delivered (in-session)"
        except Exception as e_send:
            return False, f"Failed sending message: {e_send}"

    def _run_whatsapp_thread(self, contacts_list, template_text, config):
        browser_channel = config.get("browser_channel", "msedge")
        user_data_dir = os.path.abspath(config.get("user_data_dir", "./whatsapp_profile"))
        headless = config.get("headless", False)
        default_cc = config.get("default_country_code", "+91")
        min_delay = float(config.get("min_delay_seconds", 10))
        max_delay = float(config.get("max_delay_seconds", 22))
        batch_size = int(config.get("batch_size", 25))
        batch_pause = float(config.get("batch_pause_seconds", 120))

        self._wa_log("info", f"Launching native browser: {browser_channel}...")

        with sync_playwright() as p:
            context = None
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=browser_channel,
                    headless=headless,
                    ignore_default_args=["--enable-automation"],
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
            except Exception as e:
                self._wa_log("warning", f"Failed with {browser_channel}: {e}. Retrying with Chrome...")
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        channel="chrome",
                        headless=headless,
                        ignore_default_args=["--enable-automation"],
                        args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-sandbox"]
                    )
                except Exception as e2:
                    self._wa_log("error", f"Could not launch browser: {e2}")
                    self._wa_finish("ERROR")
                    return

            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = context.pages[0] if context.pages else context.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})

            # Check WhatsApp Login
            self.after(0, lambda: self.wa_stat_badge.configure(text="STATUS: SCAN QR CODE", fg_color="#d97706"))
            self.after(0, lambda: self.wa_target_lbl.configure(text="Waiting for WhatsApp QR scan in browser..."))
            self._wa_log("info", "Opening WhatsApp Web. Please scan QR code if not already linked.")

            try:
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
            except Exception as e:
                self._wa_log("error", f"Page load error: {e}")

            logged_in_selectors = [
                "div#pane-side",
                "div[id='pane-side']",
                "div[data-tab='3']",
                "div[contenteditable='true'][data-tab='3']",
                "span[data-icon='new-chat-outline']",
                "span[data-icon='chat']",
                "div[title='Search input textbox']",
                "#side"
            ]

            logged_in = False
            for _ in range(120):
                if self.wa_stop_requested:
                    break
                for sel in logged_in_selectors:
                    if page.locator(sel).count() > 0 and page.locator(sel).first.is_visible():
                        logged_in = True
                        break
                if logged_in:
                    break
                time.sleep(2)

            if not logged_in or self.wa_stop_requested:
                if self.wa_stop_requested:
                    self._wa_log("warning", "Stopped during login phase.")
                else:
                    self._wa_log("error", "Login timed out! WhatsApp Web was not authenticated.")
                context.close()
                self._wa_finish("STOPPED" if self.wa_stop_requested else "TIMEOUT")
                return

            self._wa_log("success", "WhatsApp Web authenticated! Ready to send.")
            self.after(0, lambda: self.wa_stat_badge.configure(text="STATUS: SENDING", fg_color="#15803d"))

            for index, contact in enumerate(contacts_list, start=1):
                if self.wa_stop_requested:
                    self._wa_log("warning", "WhatsApp campaign stopped by user.")
                    break

                phone_raw = get_contact_field(contact, ["phone", "mobile", "contact", "whatsapp", "number"])
                name = get_contact_field(contact, ["name"]) or "Friend"

                self.after(0, lambda n=name, p=phone_raw, i=index, t=self.wa_total: (
                    self.wa_target_lbl.configure(text=f"[{i}/{t}] Sending to: {n} ({p})"),
                    self.wa_prog_bar.set(i / t),
                    self.wa_stat_counter.configure(text=f"Total: {t} | Sent: {self.wa_sent} | Failed: {self.wa_failed}")
                ))

                if not phone_raw:
                    self._wa_log("warning", f"[{index}/{self.wa_total}] Skipped: Missing phone for {name}")
                    self.wa_failed += 1
                    continue

                phone_digits = format_phone_number(phone_raw, default_cc)
                msg_body = prepare_message(template_text, contact)

                self._wa_log("info", f"[{index}/{self.wa_total}] Opening chat for {name} (+{phone_digits})...")
                sent_ok, reason = self._wa_send_contact_message(page, phone_digits, msg_body)

                if sent_ok:
                    self._wa_log("success", f"[{index}/{self.wa_total}] Delivered to {name} (+{phone_digits})")
                    self.wa_sent += 1
                else:
                    self._wa_log("error", f"[{index}/{self.wa_total}] Failed for {name}: {reason}")
                    self.wa_failed += 1

                if index < self.wa_total and not self.wa_stop_requested:
                    if index % batch_size == 0:
                        self._wa_log("warning", f"Batch limit reached ({batch_size}). Pausing {int(batch_pause)}s...")
                        pause_start = time.time()
                        while time.time() - pause_start < batch_pause and not self.wa_stop_requested:
                            time.sleep(1)
                    else:
                        delay = random.uniform(min_delay, max_delay)
                        self._wa_log("info", f"Anti-ban pause for {delay:.1f}s...")
                        delay_start = time.time()
                        while time.time() - delay_start < delay and not self.wa_stop_requested:
                            time.sleep(0.5)

            time.sleep(2)
            try:
                context.close()
            except:
                pass

            final_status = "STOPPED" if self.wa_stop_requested else "COMPLETED"
            self._wa_finish(final_status)

    def _wa_finish(self, status):
        self.wa_running = False
        self.after(0, lambda: (
            self.wa_btn_start.configure(state="normal", fg_color="#16a34a"),
            self.wa_btn_stop.configure(state="disabled", fg_color="#475569"),
            self.wa_stat_badge.configure(text=f"STATUS: {status}", fg_color="#334155"),
            self.wa_stat_counter.configure(text=f"Total: {self.wa_total} | Sent: {self.wa_sent} | Failed: {self.wa_failed}"),
            self.wa_target_lbl.configure(text=f"WhatsApp {status.lower()}. Sent: {self.wa_sent}, Failed: {self.wa_failed}"),
            self._wa_log("info", f"WhatsApp Campaign {status}. Total: {self.wa_total} | Sent: {self.wa_sent} | Failed: {self.wa_failed}")
        ))

    # =========================================================================
    # LOAD SAVED DATA (ALL 3 TABS)
    # =========================================================================
    def _load_saved_data(self):
        # 1. WhatsApp DM Template
        if os.path.exists(TEMPLATE_FILE):
            try:
                with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
                    self.wa_textbox.insert("1.0", f.read())
            except: pass
        else:
            self.wa_textbox.insert("1.0", "Hello {name},\n\nHope you are doing well!\n\nBest regards,\nMy Team")
        self._wa_update_preview()

        # 2. WhatsApp DM Contacts
        if os.path.exists(CONTACTS_FILE):
            try:
                with open(CONTACTS_FILE, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        self.wa_contacts.append(r)
                self._wa_refresh_treeview()
            except: pass

        # 3. WhatsApp DM Config
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.wa_min_d.delete(0, "end")
                    self.wa_min_d.insert(0, str(cfg.get("min_delay_seconds", 10)))
                    self.wa_max_d.delete(0, "end")
                    self.wa_max_d.insert(0, str(cfg.get("max_delay_seconds", 22)))
                    self.wa_cc.delete(0, "end")
                    self.wa_cc.insert(0, str(cfg.get("default_country_code", "+91")))
                    self.wa_browser.set(cfg.get("browser_channel", "msedge"))
            except: pass

        # 4. WhatsApp Group Member-Add Config & Contacts
        if os.path.exists(GROUP_CONFIG_FILE):
            try:
                with open(GROUP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    g_cfg = json.load(f)
                    self.grp_name_entry.insert(0, g_cfg.get("group_name", ""))
                    self.grp_cc.delete(0, "end")
                    self.grp_cc.insert(0, str(g_cfg.get("default_country_code", "+91")))
                    self.grp_browser.set(g_cfg.get("browser_channel", "msedge"))
            except: pass

        if os.path.exists(GROUP_CONTACTS_FILE):
            try:
                with open(GROUP_CONTACTS_FILE, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        self.group_contacts.append(r)
                self._grp_refresh_treeview()
            except: pass

        # 5. Gmail Template, Config & Contacts
        if os.path.exists(GMAIL_TEMPLATE_FILE):
            try:
                with open(GMAIL_TEMPLATE_FILE, "r", encoding="utf-8") as f:
                    self.gm_body_textbox.insert("1.0", f.read())
            except: pass
        else:
            self.gm_body_textbox.insert("1.0", "Hello {name},\n\nWe hope this email finds you well.\nThis is an automated update regarding your record ({note}).\n\nBest regards,\nOperations Team")

        if os.path.exists(GMAIL_CONTACTS_FILE):
            try:
                with open(GMAIL_CONTACTS_FILE, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        self.gmail_contacts.append(r)
                self._gm_refresh_treeview()
            except: pass

        if os.path.exists(GMAIL_CONFIG_FILE):
            try:
                with open(GMAIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                    gcfg = json.load(f)
                    self.gm_email_entry.insert(0, gcfg.get("sender_email", ""))
                    self.gm_name_entry.insert(0, gcfg.get("sender_name", "My Team"))
                    self.gm_subject_entry.insert(0, gcfg.get("subject", "Update for {name}"))
                    self.gm_min_d.delete(0, "end")
                    self.gm_min_d.insert(0, str(gcfg.get("min_delay_seconds", 3)))
                    self.gm_max_d.delete(0, "end")
                    self.gm_max_d.insert(0, str(gcfg.get("max_delay_seconds", 8)))
            except: pass
        else:
            self.gm_subject_entry.insert(0, "Important Notification for {name}")

        self._gm_update_preview()

if __name__ == "__main__":
    app = UnifiedMarketingApp()
    app.mainloop()
