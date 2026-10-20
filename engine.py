import csv
import json
import os
import random
import threading
import time
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright

RESULTS_FILE = "results.csv"

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

class WhatsAppCampaignEngine:
    def __init__(self):
        self.is_running = False
        self.stop_requested = False
        self.worker_thread = None
        self.state = "idle"  # idle, waiting_for_login, running, paused, stopped, completed
        self.current_contact = ""
        self.total = 0
        self.sent = 0
        self.failed = 0
        self.progress_percent = 0
        self.logs = []
        self.lock = threading.Lock()

    def add_log(self, level, text):
        with self.lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.logs.append({
                "time": timestamp,
                "level": level,  # info, success, warning, error
                "text": text
            })
            # Keep max 500 logs in memory
            if len(self.logs) > 500:
                self.logs.pop(0)

    def get_status(self):
        with self.lock:
            return {
                "state": self.state,
                "is_running": self.is_running,
                "current_contact": self.current_contact,
                "total": self.total,
                "sent": self.sent,
                "failed": self.failed,
                "progress_percent": self.progress_percent,
                "logs": self.logs[-50:]  # send last 50 logs
            }

    def stop(self):
        if self.is_running:
            self.stop_requested = True
            self.add_log("warning", "Stop signal received. Finishing current action and stopping...")

    def start_campaign(self, contacts, message_template, config):
        if self.is_running:
            return False, "A campaign is already running!"

        self.is_running = True
        self.stop_requested = False
        self.state = "starting"
        self.total = len(contacts)
        self.sent = 0
        self.failed = 0
        self.progress_percent = 0
        self.current_contact = ""
        self.logs = []

        self.add_log("info", f"Campaign initialized with {len(contacts)} contacts.")
        self.worker_thread = threading.Thread(
            target=self._run_campaign,
            args=(contacts, message_template, config),
            daemon=True
        )
        self.worker_thread.start()
        return True, "Campaign started successfully."

    def _save_result(self, contact, status, reason=""):
        file_exists = os.path.exists(RESULTS_FILE)
        fieldnames = list(contact.keys())
        for extra in ["status", "reason", "timestamp"]:
            if extra not in fieldnames:
                fieldnames.append(extra)

        row_data = dict(contact)
        row_data["status"] = status
        row_data["reason"] = reason
        row_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(RESULTS_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row_data)

    def _send_contact_message(self, page, phone_digits, msg_body):
        # 1. Dismiss any open dialog
        try:
            dialog = page.locator("div[role='dialog']")
            if dialog.count() > 0 and dialog.first.is_visible():
                btn = dialog.locator("button")
                if btn.count() > 0:
                    btn.first.click()
                    time.sleep(0.3)
        except:
            pass

        # 2. Try in-page 'New chat' / search to avoid page reload
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
            self.add_log("warning", f"In-page search notice: {e_search}. Using direct chat fallback...")

        # 3. Check if chat input is visible
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
                if self.stop_requested:
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

        # 4. If opened via in-page search
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

    def _run_campaign(self, contacts, message_template, config):
        browser_channel = config.get("browser_channel", "msedge")
        user_data_dir = os.path.abspath(config.get("user_data_dir", "./whatsapp_profile"))
        headless = config.get("headless", False)
        default_cc = config.get("default_country_code", "+91")
        min_delay = float(config.get("min_delay_seconds", 10))
        max_delay = float(config.get("max_delay_seconds", 22))
        batch_size = int(config.get("batch_size", 25))
        batch_pause = float(config.get("batch_pause_seconds", 120))

        with sync_playwright() as p:
            self.add_log("info", f"Launching browser ({browser_channel})...")
            context = None
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=browser_channel,
                    headless=headless,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
                )
            except Exception as e:
                self.add_log("warning", f"Could not launch {browser_channel}: {e}. Retrying with Chrome...")
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        channel="chrome",
                        headless=headless,
                        args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
                    )
                except Exception as e2:
                    self.add_log("error", f"Failed to launch browser: {e2}")
                    self.state = "error"
                    self.is_running = False
                    return

            page = context.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})

            # Check WhatsApp Login
            self.state = "waiting_for_login"
            self.add_log("info", "Navigating to WhatsApp Web. If not logged in, please scan QR code on screen.")
            page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

            logged_in_selectors = [
                "div#pane-side",
                "div[data-tab='3']",
                "header",
                "div[contenteditable='true'][data-tab='3']"
            ]

            logged_in = False
            for _ in range(120):  # Wait up to 4 minutes for login
                if self.stop_requested:
                    break
                for sel in logged_in_selectors:
                    if page.locator(sel).count() > 0 and page.locator(sel).first.is_visible():
                        logged_in = True
                        break
                if logged_in:
                    break
                time.sleep(2)

            if not logged_in or self.stop_requested:
                if self.stop_requested:
                    self.add_log("warning", "Campaign stopped during login phase.")
                else:
                    self.add_log("error", "Login timeout! WhatsApp was not linked in time.")
                context.close()
                self.is_running = False
                self.state = "stopped"
                return

            self.add_log("success", "WhatsApp Web authenticated successfully!")
            self.state = "running"

            # Iterate contacts
            for index, contact in enumerate(contacts, start=1):
                if self.stop_requested:
                    self.add_log("warning", "Campaign stopped by user.")
                    self.state = "stopped"
                    break

                phone_raw = contact.get("phone") or contact.get("Phone") or contact.get("mobile") or contact.get("Mobile")
                name = contact.get("name") or contact.get("Name") or "Friend"
                self.current_contact = f"{name} ({phone_raw})"

                if not phone_raw:
                    self.add_log("warning", f"[{index}/{self.total}] Skipped: Missing phone number for {name}")
                    self._save_result(contact, "Skipped", "Missing phone number")
                    self.failed += 1
                    self.progress_percent = int((index / self.total) * 100)
                    continue

                phone_digits = format_phone_number(phone_raw, default_cc)
                msg_body = prepare_message(message_template, contact)

                self.add_log("info", f"[{index}/{self.total}] Opening chat for {name} (+{phone_digits})...")
                
                sent_ok, reason = self._send_contact_message(page, phone_digits, msg_body)

                if sent_ok:
                    self.add_log("success", f"[{index}/{self.total}] Message delivered to {name} (+{phone_digits})")
                    self._save_result(contact, "Success", reason)
                    self.sent += 1
                else:
                    self.add_log("error", f"[{index}/{self.total}] Delivery failed for {name}: {reason}")
                    self._save_result(contact, "Failed", reason)
                    self.failed += 1

                self.progress_percent = int((index / self.total) * 100)

                # Delay before next contact
                if index < self.total and not self.stop_requested:
                    if index % batch_size == 0:
                        self.add_log("warning", f"Batch limit ({batch_size}) reached. Pausing for {int(batch_pause)}s to protect account...")
                        pause_start = time.time()
                        while time.time() - pause_start < batch_pause and not self.stop_requested:
                            time.sleep(1)
                    else:
                        delay = random.uniform(min_delay, max_delay)
                        self.add_log("info", f"Pausing for {delay:.1f}s (anti-ban simulation)...")
                        delay_start = time.time()
                        while time.time() - delay_start < delay and not self.stop_requested:
                            time.sleep(0.5)

            if not self.stop_requested:
                self.state = "completed"
                self.add_log("success", f"Campaign completed! Total: {self.total} | Sent: {self.sent} | Failed: {self.failed}")

            time.sleep(3)
            try:
                context.close()
            except:
                pass
            self.is_running = False
