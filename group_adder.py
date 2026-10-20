"""Robust direct-add workflow for WhatsApp Web group admins.

Automates member additions directly in WhatsApp Web. Handles already-in-group
detection smoothly in the same dialog, auto-clicks confirmation checkmarks,
and logs failures and skipped numbers to CSV files.
"""

import csv
import json
import os
import re
import threading
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

GROUP_CONFIG_FILE = "group_config.json"
GROUP_CONTACTS_FILE = "group_contacts.csv"
GROUP_RESULTS_FILE = "group_adder_results.csv"
GROUP_FAILED_FILE = "group_adder_failed.csv"


def format_phone_number(raw_phone, default_cc="+91"):
    """Return a normalized E.164-like number without punctuation."""
    raw = str(raw_phone or "").strip()
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return ""
    if not raw.startswith("+") and default_cc:
        country_code = "".join(character for character in str(default_cc) if character.isdigit())
        if country_code and not digits.startswith(country_code):
            digits = country_code + digits
    return digits


def get_contact_field(row, possible_keys):
    """Return a contact field using common case/spacing-insensitive aliases."""
    if not isinstance(row, dict):
        return ""
    targets = [re.sub(r"[ _-]", "", key.lower()) for key in possible_keys]
    for key, value in row.items():
        normalized = re.sub(r"[ _-]", "", str(key or "").strip().lower())
        if any(target in normalized for target in targets):
            return str(value or "").strip()
    return ""


class WhatsAppGroupEngine:
    """Add contacts through a logged-in, visible WhatsApp Web session."""

    def __init__(self, log_callback=None, progress_callback=None, confirm_callback=None):
        self.is_running = False
        self.stop_requested = False
        self.worker_thread = None
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.confirm_callback = confirm_callback
        self.total = 0
        self.added = 0
        self.failed = 0
        self.skipped = 0

    def log(self, level, text):
        if self.log_callback:
            self.log_callback(level, text)
        else:
            print(f"[{level.upper()}] {text}")

    def _progress(self, index, status):
        if self.progress_callback:
            self.progress_callback(
                index, self.total, self.added, self.failed, self.skipped, status
            )

    def _save_result(self, contact, status, reason=""):
        row = dict(contact)
        row.update(
            {
                "status": status,
                "reason": reason,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        fieldnames = list(row)
        for field in ("status", "reason", "timestamp"):
            if field not in fieldnames:
                fieldnames.append(field)

        exists = os.path.exists(GROUP_RESULTS_FILE)
        with open(GROUP_RESULTS_FILE, "a", newline="", encoding="utf-8") as result_file:
            writer = csv.DictWriter(result_file, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)

        if status != "Success":
            failed_exists = os.path.exists(GROUP_FAILED_FILE)
            with open(GROUP_FAILED_FILE, "a", newline="", encoding="utf-8") as failed_file:
                writer = csv.DictWriter(failed_file, fieldnames=fieldnames, extrasaction="ignore")
                if not failed_exists:
                    writer.writeheader()
                writer.writerow(row)

    def start_group_add(self, group_name, contacts, config):
        if self.is_running:
            return False, "A group-add operation is already running."
        if not self.confirm_callback:
            return False, "Per-contact confirmation is required before adding members."

        for report_path in (GROUP_RESULTS_FILE, GROUP_FAILED_FILE):
            try:
                os.remove(report_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                return False, f"Could not reset report file '{report_path}': {error}"

        self.is_running = True
        self.stop_requested = False
        self.total = len(contacts)
        self.added = 0
        self.failed = 0
        self.skipped = 0
        self.worker_thread = threading.Thread(
            target=self._run_group_add,
            args=(group_name, list(contacts), dict(config)),
            daemon=True,
        )
        self.worker_thread.start()
        return True, "Direct group-add workflow started."

    def stop(self):
        if self.is_running:
            self.stop_requested = True
            self.log("warning", "Stop requested; current operation will wrap up safely.")

    @staticmethod
    def _is_logged_in(page):
        for selector in ("#pane-side", "#side", "div[contenteditable='true'][data-tab='3']"):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _wait_for_login(self, page):
        self.log("info", "Opening WhatsApp Web. Log in by scanning the QR code if needed.")
        try:
            page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        except Exception as e:
            self.log("warning", f"Page load note: {e}")

        for _ in range(180):
            if self.stop_requested:
                return False
            if self._is_logged_in(page):
                self.log("success", "WhatsApp Web is logged in.")
                return True
            time.sleep(1)
        self.log("error", "Timed out waiting for WhatsApp Web login.")
        return False

    def _wait_for_target_group(self, page, group_name):
        self.log(
            "info",
            f"Looking for target group '{group_name}'. Please ensure it is selected or visible.",
        )
        # Try checking if already open in #main
        for _ in range(180):
            if self.stop_requested:
                return False
            header = page.locator("#main header").first
            try:
                if header.count() and header.is_visible():
                    title = header.inner_text().strip().splitlines()[0].strip()
                    if group_name.casefold() in title.casefold():
                        self.log("success", f"Verified active group: {title}")
                        return True
            except Exception:
                pass
            time.sleep(1)
        self.log("error", f"Timed out waiting for group '{group_name}' to be open.")
        return False

    @staticmethod
    def _search_box(dialog):
        for selector in (
            "div[contenteditable='true'][role='textbox']",
            "div[contenteditable='true']",
            "input[type='text']",
            "[role='textbox']",
        ):
            inputs = dialog.locator(selector)
            for index in range(inputs.count()):
                candidate = inputs.nth(index)
                try:
                    if candidate.is_visible() and not candidate.is_disabled():
                        return candidate
                except Exception:
                    continue
        return None

    def _open_add_dialog(self, page):
        """Ensure Add participants dialog is open. Reuses already open dialogs."""
        # Check if already open
        dialogs = page.locator("div[role='dialog']")
        for index in range(dialogs.count()):
            d = dialogs.nth(index)
            if d.is_visible() and self._search_box(d):
                return d, ""

        # Try to find 'Add participant' or 'Add member' control
        add_selectors = [
            "span[data-icon='add-user']",
            "span[data-icon='community-add']",
            "div[role='button']:has-text('Add participant')",
            "div[role='button']:has-text('Add member')",
            "button:has-text('Add participant')",
            "button:has-text('Add member')",
            "button[aria-label*='Add participant']",
            "button[aria-label*='Add member']",
            "div[role='button'][aria-label*='Add participant']",
            "div[role='button'][aria-label*='Add member']",
        ]

        add_btn = None
        for sel in add_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                add_btn = loc
                break

        # If not visible in header, click group header to open Group Info sidebar
        if not add_btn:
            header = page.locator("#main header").first
            if header.count() > 0 and header.is_visible():
                header.click()
                time.sleep(1.2)
                for sel in add_selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        add_btn = loc
                        break

        if not add_btn:
            return None, "Could not locate 'Add participant' button. Ensure you are a Group Admin."

        add_btn.click()
        for _ in range(40):
            if self.stop_requested:
                return None, "Stopped by user."
            dialogs = page.locator("div[role='dialog']")
            for index in range(dialogs.count()):
                d = dialogs.nth(index)
                if d.is_visible() and self._search_box(d):
                    return d, ""
            time.sleep(0.25)

        return None, "Add participants dialog did not appear."

    def _clear_search_box(self, page, dialog):
        """Clears the search input so the dialog remains open for the next contact."""
        search = self._search_box(dialog)
        if search:
            try:
                search.click()
                time.sleep(0.1)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.2)
            except Exception:
                pass

    def _find_contact_row(self, dialog, phone_digits):
        """Wait for contact search results and determine status."""
        deadline = time.time() + 6.0
        while time.time() < deadline and not self.stop_requested:
            text = dialog.inner_text().casefold()

            # Check if contact is already in group
            if any(term in text for term in (
                "already in group",
                "already in this group",
                "already added",
                "already a participant",
                "already a member",
            )):
                return None, "already_in_group"

            # Check if no contact found
            if any(term in text for term in (
                "no contacts found",
                "no results found",
                "no chats, contacts or messages found",
            )):
                return None, "not_found"

            # Check for selectable listitems
            rows = dialog.locator("[role='listitem']")
            visible_rows = []
            for index in range(rows.count()):
                row = rows.nth(index)
                try:
                    if row.is_visible():
                        row_text = row.inner_text().casefold()
                        # If row itself indicates already in group
                        if any(term in row_text for term in ("already in group", "already in this group", "already added")):
                            return None, "already_in_group"
                        # Check disabled attribute
                        disabled_attr = row.get_attribute("aria-disabled")
                        if disabled_attr == "true":
                            return None, "already_in_group"
                        visible_rows.append(row)
                except Exception:
                    continue

            if len(visible_rows) == 1:
                return visible_rows[0], ""
            if len(visible_rows) > 1:
                # Find matching digits
                for r in visible_rows:
                    r_digits = "".join(c for c in r.inner_text() if c.isdigit())
                    if phone_digits in r_digits:
                        return r, ""
                return visible_rows[0], ""

            time.sleep(0.25)

        return None, "timeout"

    @staticmethod
    def _find_action_button(dialog):
        """Find the green checkmark or submit button in the participant picker."""
        selectors = [
            "button:has(span[data-icon='checkmark-medium'])",
            "div[role='button']:has(span[data-icon='checkmark-medium'])",
            "button:has(span[data-icon='check'])",
            "div[role='button']:has(span[data-icon='check'])",
            "span[data-icon='checkmark-medium']",
            "span[data-icon='check']",
            "button[aria-label='Next']",
            "button[aria-label='Done']",
            "button[aria-label='Add']",
            "div[role='button'][aria-label='Next']",
            "div[role='button'][aria-label='Done']",
            "div[role='button'][aria-label='Add']",
            "button:has-text('Add')",
        ]
        for sel in selectors:
            candidate = dialog.locator(sel).first
            try:
                if candidate.count() > 0 and candidate.is_visible() and not candidate.is_disabled():
                    return candidate
            except Exception:
                continue

        # Fallback regex inspection
        candidates = dialog.locator("button, [role='button']")
        for index in range(candidates.count() - 1, -1, -1):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible() or candidate.is_disabled():
                    continue
                label = " ".join(
                    part for part in (
                        candidate.inner_text().strip(),
                        candidate.get_attribute("aria-label") or "",
                        candidate.get_attribute("title") or "",
                    ) if part
                )
                if re.search(r"\b(add|done|next)\b", label, re.I):
                    return candidate
            except Exception:
                continue
        return None

    def _add_one(self, page, contact, phone_digits):
        """Process one contact. Keeps dialog open on skips/not-found."""
        dialog, err = self._open_add_dialog(page)
        if not dialog:
            return "failed", err

        search = self._search_box(dialog)
        if not search:
            return "failed", "Search box not found in dialog."

        # Clear and type phone number
        try:
            search.click()
            time.sleep(0.1)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.1)
            page.keyboard.type(phone_digits, delay=20)
            time.sleep(1.2)
        except Exception as ex:
            return "failed", f"Failed typing search: {ex}"

        row, status_code = self._find_contact_row(dialog, phone_digits)

        if status_code == "already_in_group":
            # Keep dialog open! Just clear search box for the next contact
            self._clear_search_box(page, dialog)
            return "skipped", "WhatsApp indicates this contact is already in the group."

        if status_code == "not_found":
            self._clear_search_box(page, dialog)
            return "failed", "Number not found on WhatsApp or invalid."

        if not row:
            self._clear_search_box(page, dialog)
            return "failed", "No selectable contact row appeared."

        # Click the matched contact to select
        try:
            row.click()
            time.sleep(0.4)
        except Exception as ex:
            self._clear_search_box(page, dialog)
            return "failed", f"Failed clicking contact row: {ex}"

        # If confirm_callback provided, verify with operator
        if self.confirm_callback:
            name = get_contact_field(contact, ["name"]) or "Contact"
            try:
                confirmed = self.confirm_callback(name, f"+{phone_digits}", "Matched contact")
                if confirmed is False:
                    self._clear_search_box(page, dialog)
                    return "skipped", "Operator declined this contact."
            except Exception:
                pass

        if self.stop_requested:
            self._clear_search_box(page, dialog)
            return "failed", "Stopped before submission."

        # Find green checkmark or submit button
        action_btn = self._find_action_button(dialog)
        if not action_btn:
            self._clear_search_box(page, dialog)
            return "failed", "Could not locate Green checkmark/Add button in dialog."

        try:
            action_btn.click()
            time.sleep(0.8)
        except Exception as ex:
            self._clear_search_box(page, dialog)
            return "failed", f"Error clicking Add action button: {ex}"

        # Handle secondary confirmation dialog ("Add participant to group? [Cancel] [Add]")
        deadline = time.time() + 5.0
        while time.time() < deadline:
            dialogs = page.locator("div[role='dialog']")
            if dialogs.count() > 0:
                last_d = dialogs.last
                if last_d.is_visible():
                    d_text = last_d.inner_text().casefold()
                    if any(term in d_text for term in ("couldn't add", "could not add", "privacy", "invite privately")):
                        # Privacy popup! Dismiss it safely
                        cancel = last_d.locator("button:has-text('Cancel'), button:has-text('Dismiss'), span[data-icon='x']").first
                        if cancel.count() > 0:
                            cancel.click()
                        else:
                            page.keyboard.press("Escape")
                        return "failed", "Privacy restriction - user cannot be added directly to groups."

                    confirm_add = last_d.locator("button:has-text('Add'), [role='button']:has-text('Add')").first
                    if confirm_add.count() > 0 and confirm_add.is_visible():
                        confirm_add.click()
                        time.sleep(1.0)
                        break
            else:
                break
            time.sleep(0.25)

        time.sleep(1.0)
        return "success", "Successfully added to group."

    def _run_group_add(self, group_name, contacts, config):
        browser_channel = config.get("browser_channel", "msedge")
        user_data_dir = os.path.abspath(config.get("user_data_dir", "./whatsapp_profile"))
        default_cc = config.get("default_country_code", "+91")
        context = None
        status = "COMPLETED"

        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=browser_channel,
                    headless=False,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )

                page = context.pages[0] if context.pages else context.new_page()
                page.set_viewport_size({"width": 1280, "height": 800})

                if not self._wait_for_login(page):
                    status = "STOPPED" if self.stop_requested else "LOGIN_TIMEOUT"
                    return

                if not self._wait_for_target_group(page, group_name):
                    status = "STOPPED" if self.stop_requested else "GROUP_NOT_OPEN"
                    return

                added_numbers = set()
                for index, contact in enumerate(contacts, start=1):
                    if self.stop_requested:
                        status = "STOPPED"
                        break

                    raw_phone = get_contact_field(
                        contact, ["phone", "mobile", "contact", "whatsapp", "number"]
                    )
                    name = get_contact_field(contact, ["name"]) or "Contact"
                    phone_digits = format_phone_number(raw_phone, default_cc)

                    if not phone_digits or not phone_digits.isdigit() or not 7 <= len(phone_digits) <= 15:
                        reason = "Missing or invalid phone number."
                        self.failed += 1
                        self._save_result(contact, "Failed", reason)
                        self.log("error", f"[{index}/{self.total}] {name}: {reason}")
                        self._progress(index, "RUNNING")
                        continue

                    normalized_number = phone_digits.lstrip("0")
                    if normalized_number in added_numbers:
                        self.skipped += 1
                        reason = "Duplicate phone number in contact list."
                        self._save_result(contact, "Skipped", reason)
                        self.log("warning", f"[{index}/{self.total}] Skipped {name}: {reason}")
                        self._progress(index, "RUNNING")
                        continue
                    added_numbers.add(normalized_number)

                    self.log("info", f"[{index}/{self.total}] Checking WhatsApp for {name} (+{phone_digits})...")
                    result, reason = self._add_one(page, contact, phone_digits)

                    if result == "success":
                        self.added += 1
                        self._save_result(contact, "Success", reason)
                        self.log("success", f"[{index}/{self.total}] Added {name} (+{phone_digits}).")
                        time.sleep(1.8)
                    elif result == "skipped":
                        self.skipped += 1
                        self._save_result(contact, "Skipped", reason)
                        self.log("warning", f"[{index}/{self.total}] Skipped {name}: {reason}")
                        time.sleep(0.4)
                    else:
                        self.failed += 1
                        self._save_result(contact, "Failed", reason)
                        self.log("error", f"[{index}/{self.total}] {name}: {reason}")
                        time.sleep(0.4)

                    self._progress(index, "RUNNING")
                    # Resilient: Always continue to the next contact without breaking!

        except Exception as error:
            self.log("error", f"Group add halted: {error}")
            status = "ERROR"
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            self.log(
                "info",
                f"Group add {status}. Total: {self.total} | Added: {self.added} | Skipped: {self.skipped} | Failed: {self.failed}",
            )
            self.is_running = False
            self._progress(self.total, status)
