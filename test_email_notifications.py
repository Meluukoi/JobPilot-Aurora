"""Stage 5C email-only offline regression tests; never touches real accounts."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import email_notifications as e


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, user, password):
        self.values[(service, user)] = password

    def get_password(self, service, user):
        return self.values.get((service, user))


class EmailTests(unittest.TestCase):
    def setUp(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.folder = self.root / 'monitoring_reports'
        self.folder.mkdir()
        self.config = dict(smtp_host='smtp.gmail.com', smtp_port=587,
                           security='starttls', smtp_user='sender@example.com',
                           sender='sender@example.com', recipient='recipient@example.com',
                           start_after_scan_id=11)
        e._atomic_json(e.config_path(self.root), self.config)

    def archive(self, scan_id, rows):
        p = self.folder / f'first_seen_matches_scan_{scan_id:06d}.csv'
        with p.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['company', 'title', 'location',
                                                   'score', 'eligibility', 'eligibility_note', 'url',
                                                    'original_published_at',
                                                    'original_publication_verified'])
            writer.writeheader()
            writer.writerows(rows)
        return p

    def row(self, job_id, title='Tech Support'):
        published = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        return dict(company='Company', title=title, location='San Diego, CA',
                    score=72, eligibility='local', eligibility_note='Local office',
                    url=f'https://example.com/jobs/{job_id}',
                    original_published_at=published,
                    original_publication_verified=True)

    def test_uncertain_archived_relocation_does_not_email(self):
        row = self.row('outside')
        row.update(location='Buffalo, New York', eligibility='review',
                   eligibility_note='Nonlocal role: cannot verify salary of $75,000')
        self.archive(12, [row])
        sender = Mock()
        self.assertEqual(e.deliver(self.root, sender=sender), 0)
        sender.assert_not_called()
        self.assertFalse((self.folder / e.STATE_NAME).exists())

    def test_confirmed_relocation_still_emails(self):
        row = self.row('highpay')
        row.update(location='Seattle, Washington', eligibility='relocation')
        self.archive(12, [row])
        self.assertEqual(e.deliver(self.root, sender=Mock()), 1)

    def test_dedupe_tracking_variants_of_same_url_in_pending_queue(self):
        first = self.row('dup')
        other = self.row('dup')
        other['url'] += '?utm_source=indeed'
        self.archive(12, [first, other])
        self.assertEqual(len(e.pending_jobs(self.root)), 1)

    def test_no_archive_no_alert_and_no_state_write(self):
        sender = Mock()
        self.assertEqual(e.deliver(self.root, sender=sender), 0)
        sender.assert_not_called()
        self.assertFalse((self.folder / e.STATE_NAME).exists())

    def test_setup_baseline_excludes_existing_archives(self):
        self.archive(10, [self.row(10)])
        self.archive(11, [self.row(11)])
        self.assertEqual(e.pending_jobs(self.root), [])
        self.archive(12, [self.row(12)])
        self.assertEqual(len(e.pending_jobs(self.root)), 1)

    def test_delivers_once_and_records_only_success(self):
        self.archive(12, [self.row(12)])
        send = Mock()
        self.assertEqual(e.deliver(self.root, sender=send), 1)
        self.assertEqual(send.call_count, 1)
        message = send.call_args[0][1]
        self.assertIn('1 new job lead', message['Subject'])
        self.assertIn('https://example.com/jobs/12', message.get_content())
        self.assertIn('not hiring probability', message.get_content())
        self.assertEqual(e.deliver(self.root, sender=send), 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(e.load_state(self.root)['sent_ids']), 1)

    def test_mail_error_retries_after_next_scan(self):
        self.archive(12, [self.row(12)])
        with self.assertRaisesRegex(RuntimeError, 'SMTP down'):
            e.deliver(self.root, sender=Mock(side_effect=RuntimeError('SMTP down')))
        self.assertFalse((self.folder / e.STATE_NAME).exists())
        self.archive(13, [self.row(13)])
        self.assertEqual(e.deliver(self.root, sender=Mock()), 2)

    def test_no_new_listings_no_spam_even_if_report_latest_empty(self):
        self.archive(12, [self.row(12)])
        e.deliver(self.root, sender=Mock())
        (self.folder / 'latest_first_seen_matches.csv').write_text('company,title\n', encoding='utf-8')
        sender = Mock()
        self.assertEqual(e.deliver(self.root, sender=sender), 0)
        sender.assert_not_called()

    def test_at_most_twenty_alerts_per_delivery(self):
        self.archive(12, [self.row(x) for x in range(23)])
        messages = []
        self.assertEqual(e.deliver(self.root, sender=lambda _c, msg: messages.append(msg)), 20)
        self.assertEqual(len(messages), 1)
        self.assertEqual(e.deliver(self.root, sender=lambda _c, msg: messages.append(msg)), 3)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(e.load_state(self.root)['sent_ids']), 23)

    def test_setup_stores_secret_in_keyring_and_no_secret_in_config(self):
        (self.folder / 'last_successful_scan.json').write_text(json.dumps({'scan_id': 27}), encoding='utf-8')
        e.config_path(self.root).unlink()
        responses = iter(['1', 'new@example.com', '', 'notify@example.com'])
        keyring = FakeKeyring()
        out = e.setup(self.root, prompt=lambda _s: next(responses),
                      secret_prompt=lambda _s: 'app-password-16', key_store=keyring)
        self.assertEqual(out['start_after_scan_id'], 27)
        self.assertEqual(out['recipient'], 'notify@example.com')
        self.assertEqual(keyring.get_password(e.KEYRING_SERVICE, 'new@example.com'), 'app-password-16')
        self.assertNotIn('app-password-16', e.config_path(self.root).read_text())
        self.assertFalse((self.folder / e.STATE_NAME).exists())

    def test_reseting_up_preserves_existing_baseline_and_state(self):
        self.archive(12, [self.row(12)])
        e.deliver(self.root, sender=Mock())
        before = e.load_state(self.root)
        response = iter(['1', 'new@example.com', '', ''])
        e.setup(self.root, prompt=lambda _s: next(response), secret_prompt=lambda _s: 'new-password',
                key_store=FakeKeyring())
        self.assertEqual(e.load_config(self.root)['start_after_scan_id'], 11)
        self.assertEqual(e.load_state(self.root), before)

    def test_bad_email_headers_and_outlook_settings_rejected(self):
        for value in ('a@example.com\nBcc: bad@example.com', 'Not an email', 'Person <a@example.com>'):
            with self.subTest(value=value):
                c = dict(self.config, recipient=value)
                with self.assertRaisesRegex(ValueError, 'email'):
                    e.validate_config(c)
        with self.assertRaisesRegex(ValueError, 'OAuth2'):
            e.validate_config(dict(self.config, smtp_host='smtp.office365.com'))

    def test_email_has_only_explicit_job_fields(self):
        weird = self.row(1, 'Coworker\nNew Line')
        message = e.make_email(self.config, [('id', weird)])
        self.assertIn('Coworker New Line', message.get_content())
        self.assertNotIn('candidate_profile', message.get_content())

    def test_smtp_requires_tls_and_never_sends_without_secret(self):
        with self.assertRaisesRegex(RuntimeError, 'No SMTP credential'):
            e.smtp_send(self.config, e.make_email(self.config, [], is_test=True),
                        key_store=FakeKeyring())
        with self.assertRaisesRegex(ValueError, 'SMTP port'):
            e.validate_config(dict(self.config, smtp_port=25))

    def test_glob_ignores_invalid_archive_names(self):
        self.archive(12, [self.row(12)])
        (self.folder / 'first_seen_matches_scan_foo.csv').write_text('bad', encoding='utf-8')
        self.assertEqual(len(e.pending_jobs(self.root)), 1)

    def test_runner_preserves_scan_and_mail_without_mutating_project_data(self):
        runner = (Path(__file__).resolve().parent / 'run_jobpilot_daily.cmd').read_text(encoding='utf-8')
        self.assertIn('scheduled_scan.py', runner)
        self.assertIn('email_notifications.py', runner)
        self.assertIn('SCAN_EXIT', runner)
        self.assertIn('EMAIL_EXIT', runner)
        self.assertNotIn('del jobpilot.db', runner.lower())


if __name__ == '__main__':
    unittest.main()
