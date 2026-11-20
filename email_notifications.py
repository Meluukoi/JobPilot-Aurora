"""JobPilot v0.2 Stage 5C: optional, local-first email notifications.

Only relevant first-seen jobs archived by Stage 5B are eligible for email.
A queue lives in monitoring_reports/email_delivery_state.json; failed sends remain
pending and are retried on later runs. Credentials are stored in Windows
Credential Manager using the optional `keyring` dependency, never in files.

Commands:
  python email_notifications.py --check    # no network/writes
  python email_notifications.py --setup    # configure SMTP and secret locally
  python email_notifications.py --test     # send ONE test email
  python email_notifications.py --deliver  # send any pending new leads

Supports SMTP with app-specific credentials over STARTTLS (587) or SSL (465),
NOT Outlook.com/Microsoft 365 OAuth2. No mail is sent on --setup or --check.
"""

import argparse
import csv
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit, urlunsplit

from alert_policy import email_eligible
from jobpilot_config import load_policy

ROOT = Path(__file__).resolve().parent
REPORT_SUBDIR = 'monitoring_reports'
CONFIG_NAME = 'email_settings.json'
STATE_NAME = 'email_delivery_state.json'
KEYRING_SERVICE = 'JobPilot v0.2 SMTP notifications'
ARCHIVE_RE = re.compile(r'^first_seen_matches_scan_(\d+)\.csv$')
MAX_JOBS_PER_EMAIL = 20


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def valid_email(value):
    """Simple safe address validation (not full RFC validation)."""
    if not isinstance(value, str) or '\r' in value or '\n' in value or any(c.isspace() for c in value):
        return False
    name, addr = parseaddr(value)
    if name or addr != value or addr.count('@') != 1:
        return False
    local, domain = addr.rsplit('@', 1)
    return bool(local and '.' in domain and domain.split('.')[-1])


def validate_config(config):
    for key in ('smtp_user', 'sender', 'recipient'):
        if not valid_email(config.get(key)):
            raise ValueError(f'Invalid email address in {key}')
    host = config.get('smtp_host', '')
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{1,252}', host):
        raise ValueError('Invalid SMTP host')
    if host.lower() in {'smtp-mail.outlook.com', 'smtp.office365.com', 'outlook.office365.com'}:
        raise ValueError('Outlook/Microsoft 365 require OAuth2; this SMTP app-password patch does not support them')
    port = config.get('smtp_port')
    if type(port) is not int or port not in (465, 587):
        raise ValueError('SMTP port must be 465 (SSL) or 587 (STARTTLS)')
    if config.get('security') != ('ssl' if port == 465 else 'starttls'):
        raise ValueError('SMTP transport security setting does not match port')
    baseline = config.get('start_after_scan_id')
    if type(baseline) is not int or baseline < 0:
        raise ValueError('Invalid initial scan baseline')
    return config


def config_path(root):
    return Path(root) / CONFIG_NAME


def report_dir(root):
    return Path(root) / REPORT_SUBDIR


def load_config(root):
    path = config_path(root)
    if not path.is_file():
        raise FileNotFoundError('Email is not configured. Run: python email_notifications.py --setup')
    return validate_config(json.loads(path.read_text(encoding='utf-8')))


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix='.tmp_email_', delete=False) as file:
            temp_path = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write('\n')
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def current_scan_id(root):
    path = report_dir(root) / 'last_successful_scan.json'
    if not path.is_file():
        return 0
    value = json.loads(path.read_text(encoding='utf-8')).get('scan_id', 0)
    return value if type(value) is int and value >= 0 else 0


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError('Missing dependency. Install with: python -m pip install keyring') from exc
    return keyring


def setup(root=ROOT, *, prompt=input, secret_prompt=getpass, key_store=None):
    """Interactive config; secrets only through keyring. Existing baseline preserved."""
    root = Path(root)
    print('JobPilot email setup. Use an app-specific SMTP password, NOT your normal mailbox password.')
    print('This supports Gmail and compatible SMTP providers; Outlook/Microsoft 365 require OAuth2 and are not supported.')
    kind = prompt('Provider [1=Gmail, 2=other app-password SMTP]: ').strip()
    if kind == '1':
        host, port = 'smtp.gmail.com', 587
    elif kind == '2':
        host = prompt('SMTP hostname: ').strip()
        port = int(prompt('SMTP port [465 or 587]: ').strip())
    else:
        raise ValueError('Choose 1 or 2')
    user = prompt('SMTP login email address: ').strip()
    sender = prompt(f'Sender email [{user}]: ').strip() or user
    recipient = prompt(f'Where should alerts go [{user}]? ').strip() or user
    old = None
    if config_path(root).is_file():
        old = load_config(root)
    baseline = old['start_after_scan_id'] if old else current_scan_id(root)
    config = validate_config(dict(smtp_host=host, smtp_port=port,
                                  security=('ssl' if port == 465 else 'starttls'),
                                  smtp_user=user, sender=sender, recipient=recipient,
                                  start_after_scan_id=baseline))
    if key_store is None:
        key_store = _keyring()
    password = secret_prompt('SMTP app-specific password (hidden): ')
    if not password.strip():
        raise ValueError('Empty app password; setup was not saved')
    # Must store secret successfully before writing config.
    key_store.set_password(KEYRING_SERVICE, user, password.strip())
    _atomic_json(config_path(root), config)
    print('Saved email addresses/settings locally. Password saved in Windows Credential Manager.')
    print('No mail sent yet. Run: python email_notifications.py --test')
    return config


def load_state(root):
    path = report_dir(root) / STATE_NAME
    if not path.exists():
        return {'version': 1, 'sent_ids': [], 'last_sent_at_utc': None}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('sent_ids'), list):
        raise ValueError('Unrecognized email delivery state; refusing to reset it')
    return data


def _safe_line(value, limit=260):
    s = ' '.join(str(value or '').split())
    return s[:limit] + ('...' if len(s) > limit else '')


def _url_key(value):
    """Ignore fragments and tracking query strings for per-run deduplication."""
    try:
        p = urlsplit(str(value or "").strip())
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))
    except ValueError:
        return str(value or "").strip()


def pending_jobs(root, config=None, state=None):
    """Use durable report archives so a later zero-match scan can't lose alerts."""
    config = config or load_config(root)
    state = state or load_state(root)
    sent = set(state['sent_ids'])
    queued = []
    seen_links = set()
    freshness_hours = load_policy(root)["publication_freshness_hours"]
    for archive in sorted(report_dir(root).glob('first_seen_matches_scan_*.csv')):
        match = ARCHIVE_RE.fullmatch(archive.name)
        if not match or int(match.group(1)) <= config['start_after_scan_id']:
            continue
        with archive.open(newline='', encoding='utf-8-sig') as f:
            for row_index, item in enumerate(csv.DictReader(f), start=1):
                if not item.get('company') or not item.get('title') or not item.get('url'):
                    continue
                if not email_eligible(item, freshness_hours=freshness_hours):
                    continue
                url_key = _url_key(item["url"])
                if url_key in seen_links:
                    continue
                raw = f'{archive.name}|{row_index}|{item["url"]}'
                alert_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()
                if alert_id not in sent:
                    queued.append((alert_id, item))
                    seen_links.add(url_key)
    return queued


def make_email(config, jobs, is_test=False):
    message = EmailMessage()
    message['From'] = config['sender']
    message['To'] = config['recipient']
    message['Subject'] = ('JobPilot email test — setup confirmed' if is_test else
                          f'JobPilot: {len(jobs)} new job lead{"s" if len(jobs) != 1 else ""} for review')
    if is_test:
        body = ('This is a test email from your local JobPilot v0.2 notification setup.\n'
                'No applications were submitted.\n')
    else:
        lines = [f'JobPilot found {len(jobs)} first-seen jobs matching your review filters.',
                 'These are not verified new employer postings or guaranteed job matches.',
                 'Check California remote eligibility, qualifications, salary and whether the role is still open.\n']
        for i, (_, job) in enumerate(jobs, 1):
            lines.extend([f'{i}. {_safe_line(job.get("company"))} — {_safe_line(job.get("title"))}',
                          f'   Location: {_safe_line(job.get("location"))}',
                          f'   Heuristic score: {_safe_line(job.get("score"), 15)}% (not hiring probability)',
                          f'   Eligibility: {_safe_line(job.get("eligibility"))}',
                          f'   Note: {_safe_line(job.get("eligibility_note"), 300)}',
                          f'   Link: {_safe_line(job.get("url"), 900)}', ''])
        lines.append('Unverified remote and relocation listings remain in the full review report, not email alerts.')
        lines.append('Previously applied positions were excluded when JobPilot generated these leads.')
        lines.append('These leads are saved in JobPilot_v0.2/monitoring_reports/.')
        body = '\n'.join(lines)
    message.set_content(body)
    return message


def smtp_send(config, message, *, key_store=None):
    if key_store is None:
        key_store = _keyring()
    password = key_store.get_password(KEYRING_SERVICE, config['smtp_user'])
    if not password:
        raise RuntimeError('No SMTP credential found in Windows Credential Manager for configured user; rerun --setup')
    context = ssl.create_default_context()
    host, port = config['smtp_host'], config['smtp_port']
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=25) as client:
            client.login(config['smtp_user'], password)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=25) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(config['smtp_user'], password)
            client.send_message(message)


def deliver(root=ROOT, *, sender=smtp_send, config=None):
    """One digest per run, sending at most 20 leads; advance state ONLY on success.

    A crash after SMTP accepts the message but before state writes may cause a
    duplicate email. This is preferable to permanently losing an alert.
    """
    config = config or load_config(root)
    state = load_state(root)
    pending = pending_jobs(root, config=config, state=state)
    if not pending:
        print('No pending matching new job leads; no email sent.')
        return 0
    batch = pending[:load_policy(root)["email_batch_size"]]
    message = make_email(config, batch)
    sender(config, message)
    state['sent_ids'].extend(alert_id for alert_id, _ in batch)
    state['last_sent_at_utc'] = utc_now()
    _atomic_json(report_dir(root) / STATE_NAME, state)
    print(f'Email sent: {len(batch)} new leads. {len(pending) - len(batch)} remain queued.')
    return len(batch)


def status(root=ROOT):
    path = config_path(root)
    if not path.exists():
        print('Email not configured yet. Your existing daily JobPilot scan is unchanged.')
        return
    config = load_config(root)
    state = load_state(root)
    print(f'Email configured: {config["recipient"]} via {config["smtp_host"]}:{config["smtp_port"]}')
    print(f'Notifications begin after scan ID {config["start_after_scan_id"]}.')
    print(f'Pending job leads: {len(pending_jobs(root, config=config, state=state))}; '
          f'sent records: {len(state["sent_ids"])}. No network requests made.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='JobPilot v0.2 Stage 5C email notifications')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='show setup/pending status without network or writes')
    mode.add_argument('--setup', action='store_true', help='configure secure SMTP app credential locally')
    mode.add_argument('--test', action='store_true', help='send a single test email')
    mode.add_argument('--deliver', action='store_true', help='email any archived unsent first-seen matches')
    args = parser.parse_args(argv)
    try:
        if args.setup:
            setup()
        elif args.check:
            status()
        elif args.test:
            config = load_config(ROOT)
            smtp_send(config, make_email(config, [], is_test=True))
            print(f'Test message sent to {config["recipient"]}. Check inbox/spam.')
        else:
            deliver()
        return 0
    except (Exception) as exc:
        # Deliberately never echo secret or any configuration JSON.
        print(f'JobPilot email step failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
