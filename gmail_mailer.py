import csv
import json
import os
import random
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_CONFIG_FILE = "gmail_config.json"
GMAIL_CONTACTS_FILE = "gmail_contacts.csv"
GMAIL_TEMPLATE_FILE = "gmail_template.txt"
GMAIL_RESULTS_FILE = "gmail_results.csv"

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

def prepare_template_text(template, contact_row):
    msg = template
    for key, value in contact_row.items():
        if key:
            placeholder = "{" + key.strip() + "}"
            msg = msg.replace(placeholder, str(value).strip())
            placeholder_lower = "{" + key.strip().lower() + "}"
            msg = msg.replace(placeholder_lower, str(value).strip())
    return msg

class GmailEngine:
    def __init__(self, log_callback=None, progress_callback=None):
        self.is_running = False
        self.stop_requested = False
        self.worker_thread = None
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.total = 0
        self.sent = 0
        self.failed = 0

    def log(self, level, text):
        if self.log_callback:
            self.log_callback(level, text)
        else:
            print(f"[{level.upper()}] {text}")

    def verify_credentials(self, sender_email, app_password):
        """Tests SMTP authentication with smtp.gmail.com."""
        sender_email = sender_email.strip()
        app_password = app_password.strip().replace(" ", "")
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(sender_email, app_password)
            return True, "Gmail authentication successful!"
        except smtplib.SMTPAuthenticationError:
            return False, "Authentication failed! Check your Gmail address and 16-character App Password."
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def send_email(self, sender_email, app_password, to_email, subject, body, sender_name="", attachments=None):
        """Sends a single personalized email via Gmail SMTP."""
        sender_email = sender_email.strip()
        app_password = app_password.strip().replace(" ", "")
        to_email = to_email.strip()

        msg = MIMEMultipart()
        display_from = f"{sender_name} <{sender_email}>" if sender_name else sender_email
        msg["From"] = display_from
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")

        # Attach body (supports HTML or plain text)
        if "<html>" in body.lower() or "<div>" in body.lower() or "<p>" in body.lower() or "<br>" in body.lower():
            msg.attach(MIMEText(body, "html", "utf-8"))
        else:
            msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attachments
        if attachments:
            for file_path in attachments:
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "rb") as f:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename={os.path.basename(file_path)}"
                        )
                        msg.attach(part)
                    except Exception as e_att:
                        self.log("warning", f"Could not attach {file_path}: {e_att}")

        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(sender_email, app_password)
            server.sendmail(sender_email, [to_email], msg.as_string())

        return True, "Delivered"

    def _save_result(self, contact, status, reason=""):
        file_exists = os.path.exists(GMAIL_RESULTS_FILE)
        fieldnames = list(contact.keys())
        for extra in ["status", "reason", "timestamp"]:
            if extra not in fieldnames:
                fieldnames.append(extra)

        row_data = dict(contact)
        row_data["status"] = status
        row_data["reason"] = reason
        row_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(GMAIL_RESULTS_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row_data)

    def start_campaign(self, sender_email, app_password, sender_name, subject_template, body_template, contacts, min_delay=3, max_delay=8, attachments=None):
        if self.is_running:
            return False, "Campaign already running!"

        self.is_running = True
        self.stop_requested = False
        self.total = len(contacts)
        self.sent = 0
        self.failed = 0

        self.worker_thread = threading.Thread(
            target=self._run_campaign_thread,
            args=(sender_email, app_password, sender_name, subject_template, body_template, contacts, min_delay, max_delay, attachments),
            daemon=True
        )
        self.worker_thread.start()
        return True, "Campaign started"

    def stop_campaign(self):
        if self.is_running:
            self.stop_requested = True
            self.log("warning", "Stopping Gmail campaign...")

    def _run_campaign_thread(self, sender_email, app_password, sender_name, subject_template, body_template, contacts, min_delay, max_delay, attachments):
        self.log("info", f"Starting Gmail bulk campaign for {len(contacts)} recipients...")
        
        # Test credentials first
        ok, msg = self.verify_credentials(sender_email, app_password)
        if not ok:
            self.log("error", f"Gmail authentication failed: {msg}")
            self.is_running = False
            if self.progress_callback:
                self.progress_callback(0, self.total, self.sent, self.failed, "AUTH_FAILED")
            return

        self.log("success", f"Connected to smtp.gmail.com as {sender_email}")

        for index, contact in enumerate(contacts, start=1):
            if self.stop_requested:
                self.log("warning", "Campaign stopped by user.")
                break

            to_email = get_contact_field(contact, ["email", "mail"])
            name = get_contact_field(contact, ["name"]) or "Valued Recipient"

            if not to_email or "@" not in to_email:
                self.log("warning", f"[{index}/{self.total}] Skipped: Invalid email for {name} ({to_email})")
                self._save_result(contact, "Skipped", "Invalid or missing email")
                self.failed += 1
                if self.progress_callback:
                    self.progress_callback(index, self.total, self.sent, self.failed, "RUNNING")
                continue

            # Personalize subject & body
            personalized_subject = prepare_template_text(subject_template, contact)
            personalized_body = prepare_template_text(body_template, contact)

            self.log("info", f"[{index}/{self.total}] Sending to {name} <{to_email}>...")

            try:
                self.send_email(
                    sender_email=sender_email,
                    app_password=app_password,
                    to_email=to_email,
                    subject=personalized_subject,
                    body=personalized_body,
                    sender_name=sender_name,
                    attachments=attachments
                )
                self.log("success", f"[{index}/{self.total}] Sent to {name} <{to_email}>")
                self._save_result(contact, "Success", "Delivered")
                self.sent += 1
            except Exception as e_send:
                self.log("error", f"[{index}/{self.total}] Failed for {to_email}: {e_send}")
                self._save_result(contact, "Failed", str(e_send))
                self.failed += 1

            if self.progress_callback:
                self.progress_callback(index, self.total, self.sent, self.failed, "RUNNING")

            # Delay before next email to protect reputation
            if index < self.total and not self.stop_requested:
                delay = random.uniform(float(min_delay), float(max_delay))
                self.log("info", f"Waiting {delay:.1f}s before next email...")
                delay_start = time.time()
                while time.time() - delay_start < delay and not self.stop_requested:
                    time.sleep(0.4)

        status_final = "STOPPED" if self.stop_requested else "COMPLETED"
        self.log("info", f"Gmail Campaign {status_final}! Total: {self.total} | Sent: {self.sent} | Failed: {self.failed}")
        self.is_running = False
        if self.progress_callback:
            self.progress_callback(self.total, self.total, self.sent, self.failed, status_final)

