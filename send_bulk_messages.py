import csv
import json
import os
import random
import sys
import time
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

CONFIG_FILE = "config.json"
CONTACTS_FILE = "contacts.csv"
TEMPLATE_FILE = "message_template.txt"
RESULTS_FILE = "results.csv"

def load_config():
    default_config = {
        "browser_channel": "msedge",  # "msedge" (built into Windows) or "chrome"
        "min_delay_seconds": 10,
        "max_delay_seconds": 22,
        "headless": False,
        "user_data_dir": "./whatsapp_profile",
        "default_country_code": "+91",
        "batch_size": 25,
        "batch_pause_seconds": 120
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                default_config.update(user_cfg)
        except Exception as e:
            print(f"[!] Warning reading config.json: {e}. Using defaults.")
    return default_config

def format_phone_number(raw_phone, default_cc):
    """Normalize phone number to international format digits only."""
    clean = "".join([c for c in str(raw_phone) if c.isdigit() or c == "+"])
    if not clean:
        return ""
    if not clean.startswith("+"):
        if default_cc:
            cc = default_cc.strip("+")
            if not clean.startswith(cc):
                clean = cc + clean
        clean = "+" + clean
    # Remove '+' for the WhatsApp Web URL (only digits required)
    return clean.replace("+", "")

def load_contacts(contacts_file):
    if not os.path.exists(contacts_file):
        print(f"[!] Error: {contacts_file} not found!")
        return []
    contacts = []
    with open(contacts_file, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            contacts.append(row)
    return contacts

def load_template(template_file):
    if not os.path.exists(template_file):
        return "Hello {name}, hope you are doing well!"
    with open(template_file, mode="r", encoding="utf-8") as f:
        return f.read()

def prepare_message(template, contact_row):
    """Replaces placeholders like {name}, {phone}, etc., case-insensitively."""
    msg = template
    for key, value in contact_row.items():
        if key:
            placeholder = "{" + key.strip() + "}"
            msg = msg.replace(placeholder, str(value).strip())
            # Also try lowercased placeholder
            placeholder_lower = "{" + key.strip().lower() + "}"
            msg = msg.replace(placeholder_lower, str(value).strip())
    return msg

def save_result(contact_row, status, reason=""):
    file_exists = os.path.exists(RESULTS_FILE)
    fieldnames = list(contact_row.keys())
    for extra in ["status", "reason", "timestamp"]:
        if extra not in fieldnames:
            fieldnames.append(extra)

    row_data = dict(contact_row)
    row_data["status"] = status
    row_data["reason"] = reason
    row_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(RESULTS_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_data)

def wait_for_whatsapp_login(page):
    print("\n" + "=" * 60)
    print("[*] Opening WhatsApp Web...")
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

    print("[*] Checking login status...")
    print("[*] If this is your first run, please SCAN the QR code in the browser window!")
    print("=" * 60 + "\n")

    # Wait until either the side chat pane or search box appears
    logged_in_selectors = [
        "div#pane-side",
        "div[data-tab='3']",
        "header",
        "div[contenteditable='true'][data-tab='3']"
    ]

    while True:
        for selector in logged_in_selectors:
            if page.locator(selector).count() > 0 and page.locator(selector).first.is_visible():
                print("[+] Successfully logged into WhatsApp Web!\n")
                return True
        time.sleep(2)

def send_message_to_number(page, phone_digits, message_text, timeout_seconds=35):
    """
    Sends WhatsApp message inside active session WITHOUT reloading the page.
    """
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

    # 2. Try in-page 'New chat' / search to avoid reloading WhatsApp Web
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
    except Exception:
        pass

    # 3. Check if chat input is visible
    input_box = page.locator("footer div[contenteditable='true']")
    if not opened_chat or input_box.count() == 0 or not input_box.first.is_visible():
        # Fallback to direct send URL ONLY if in-page search didn't open chat
        encoded_text = urllib.parse.quote(message_text)
        url = f"https://web.whatsapp.com/send?phone={phone_digits}&text={encoded_text}"
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            return False, f"Navigation failed: {str(e)}"

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            dialog = page.locator("div[role='dialog']")
            if dialog.count() > 0 and dialog.first.is_visible():
                dialog_text = dialog.first.inner_text().lower()
                if "invalid" in dialog_text or "not on whatsapp" in dialog_text:
                    ok_btn = dialog.locator("button")
                    if ok_btn.count() > 0:
                        try: ok_btn.first.click()
                        except: pass
                    return False, "Number not registered on WhatsApp or invalid"

            input_box = page.locator("footer div[contenteditable='true']")
            if input_box.count() > 0 and input_box.first.is_visible():
                time.sleep(1.0)
                send_btn = page.locator("footer button span[data-icon='send'], footer button span[data-icon='send-refreshed'], footer span[data-icon='send']").first
                if send_btn.count() > 0 and send_btn.is_visible():
                    try:
                        send_btn.click()
                        time.sleep(2.0)
                        return True, "Delivered"
                    except Exception: pass
                try:
                    input_box.first.press("Enter")
                    time.sleep(2.0)
                    return True, "Delivered via Enter key"
                except Exception as ex:
                    return False, f"Failed pressing Enter: {str(ex)}"
            time.sleep(1)
        return False, "Timeout waiting for chat box or send button"

    # 4. If opened via in-page search (Zero reload!)
    try:
        input_box.first.click()
        time.sleep(0.3)
        page.keyboard.insert_text(message_text)
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

    return False, "Timeout waiting for chat box or send button"

def main():
    print("""
======================================================
  WHATSAPP BULK MESSENGER AUTOMATION (NO API REQUIRED)
======================================================
    """)
    config = load_config()
    contacts = load_contacts(CONTACTS_FILE)
    template = load_template(TEMPLATE_FILE)

    if not contacts:
        print(f"[!] No contacts found in {CONTACTS_FILE}. Please add rows and run again.")
        return

    print(f"[*] Loaded {len(contacts)} contact(s) from {CONTACTS_FILE}.")
    print(f"[*] Session storage: {config['user_data_dir']} (Login will be saved)")
    print(f"[*] Browser engine: {config.get('browser_channel', 'msedge')} (No download required)")
    print(f"[*] Delays between messages: {config['min_delay_seconds']}s - {config['max_delay_seconds']}s")
    print("-" * 60)

    # Let user confirm before sending
    confirm = input("Are you ready to start sending? (yes/no): ").strip().lower()
    if confirm not in ["y", "yes"]:
        print("[!] Aborted by user.")
        return

    browser_channel = config.get("browser_channel", "msedge")
    user_data_dir = os.path.abspath(config["user_data_dir"])
    headless = config.get("headless", False)

    with sync_playwright() as p:
        try:
            # Launch persistent context using local Microsoft Edge or Chrome
            print(f"[*] Launching browser ({browser_channel})...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel=browser_channel,
                headless=headless,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
        except Exception as e:
            print(f"[!] Failed to launch using channel '{browser_channel}': {e}")
            print("[*] Trying default Chrome channel...")
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",
                    headless=headless,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
                )
            except Exception as e2:
                print(f"[!] Critical Error: Could not launch Edge or Chrome: {e2}")
                return

        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})

        # Step 1: Ensure user is logged in
        wait_for_whatsapp_login(page)

        total = len(contacts)
        success_count = 0
        fail_count = 0

        # Step 2: Loop through contacts
        for index, contact in enumerate(contacts, start=1):
            phone_raw = contact.get("phone") or contact.get("Phone") or contact.get("mobile") or contact.get("Mobile")
            name = contact.get("name") or contact.get("Name") or "Customer"

            if not phone_raw:
                print(f"[{index}/{total}] Skipping row with missing phone number: {contact}")
                save_result(contact, "Skipped", "Missing phone number")
                fail_count += 1
                continue

            phone_digits = format_phone_number(phone_raw, config.get("default_country_code", "+91"))
            personalized_msg = prepare_message(template, contact)

            print(f"[{index}/{total}] Sending to {name} (+{phone_digits})...")
            success, reason = send_message_to_number(page, phone_digits, personalized_msg)

            if success:
                print(f"    [+] SUCCESS: Message sent to {name}")
                save_result(contact, "Success", reason)
                success_count += 1
            else:
                print(f"    [-] FAILED: {reason}")
                save_result(contact, "Failed", reason)
                fail_count += 1

            # Safety delay between messages (avoids spam ban)
            if index < total:
                # Check for batch pause
                batch_size = config.get("batch_size", 25)
                if index % batch_size == 0:
                    batch_pause = config.get("batch_pause_seconds", 120)
                    print(f"\n[*] Reached batch limit of {batch_size}. Pausing for {batch_pause} seconds to protect account...")
                    time.sleep(batch_pause)
                else:
                    delay = random.uniform(config["min_delay_seconds"], config["max_delay_seconds"])
                    print(f"[*] Sleeping {delay:.1f}s to mimic human behavior...\n")
                    time.sleep(delay)

        print("\n" + "=" * 60)
        print(f"[*] Bulk messaging completed!")
        print(f"[*] Total Contacts: {total}")
        print(f"[*] Sent: {success_count} | Failed/Skipped: {fail_count}")
        print(f"[*] Detailed log saved to: {RESULTS_FILE}")
        print("=" * 60)

        # Keep browser open briefly
        time.sleep(3)
        context.close()

if __name__ == "__main__":
    main()
