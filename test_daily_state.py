import tempfile
from pathlib import Path
import unittest
import daily_state as state


class DailyStateTests(unittest.TestCase):
    day = '2026-10-23'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cached(self, phase, *, day=None, notified=False):
        state.write(self.root / '.daily-state/result.json', {
            'date': day or self.day, 'phase': phase, 'notified': notified,
            'token': 'must-not-be-cached-again', 'lng': 110.0,
        })

    def current(self, phase):
        state.write(self.root / '.local-state.json', {
            'date': self.day, 'phase': phase, 'message': 'private detail',
        })

    def test_terminal_record_prevents_another_attempt(self):
        for phase in ('submitted', 'already_checked', 'submitted_unconfirmed'):
            with self.subTest(phase=phase):
                self.cached(phase)
                result = state.before(self.root, self.day)
                self.assertTrue(result['skip'])
                self.assertEqual(result['blocked'], phase == 'submitted_unconfirmed')

    def test_previous_day_and_failures_do_not_block_retry(self):
        self.cached('submitted', day='2026-10-22')
        self.assertFalse(state.before(self.root, self.day)['skip'])
        self.cached('failed')
        self.assertFalse(state.before(self.root, self.day)['skip'])

    def test_repeated_outcome_does_not_repeat_mail(self):
        self.cached('submitted', notified=True)
        state.before(self.root, self.day)
        self.assertFalse(state.capture(self.root, self.day)['notify'])

    def test_success_after_failure_sends_new_result(self):
        self.cached('failed', notified=True)
        self.current('submitted')
        self.assertTrue(state.capture(self.root, self.day)['notify'])

    def test_failed_mail_is_retried_without_resubmitting(self):
        self.cached('submitted', notified=False)
        self.assertTrue(state.before(self.root, self.day)['skip'])
        self.assertTrue(state.capture(self.root, self.day)['notify'])
        state.mark_mail(self.root, self.day, False)
        self.assertTrue(state.capture(self.root, self.day)['notify'])
        state.mark_mail(self.root, self.day, True)
        self.assertFalse(state.capture(self.root, self.day)['notify'])

    def test_interrupted_submission_blocks_automatic_repost(self):
        self.current('submitting')
        state.capture(self.root, self.day)
        self.assertTrue(state.before(self.root, self.day)['blocked'])

    def test_interrupted_waiting_or_query_allows_later_retry(self):
        for phase in ('waiting', 'planned', 'checking'):
            with self.subTest(phase=phase):
                self.current(phase)
                state.capture(self.root, self.day)
                self.assertFalse(state.before(self.root, self.day)['skip'])

    def test_verified_legacy_recovery_requires_exact_source(self):
        self.cached('submitted_unconfirmed', notified=True)
        for key in ('csu-daily-v1-2026-10-23-123-1-final', 'csu-daily-v1-2026-10-22-456-1-final', ''):
            with self.assertRaises(ValueError):
                state.recover_waiting(self.root, self.day, '456', key)
        self.assertTrue(state.before(self.root, self.day)['blocked'])
        state.recover_waiting(self.root, self.day, '456', 'csu-daily-v1-2026-10-23-456-1-final')
        self.assertFalse(state.before(self.root, self.day)['skip'])

    def test_recovery_never_changes_confirmed_success(self):
        self.cached('submitted')
        with self.assertRaises(ValueError):
            state.recover_waiting(self.root, self.day, '456', 'csu-daily-v1-2026-10-23-456-1-final')
        self.assertTrue(state.before(self.root, self.day)['skip'])

    def test_cache_whitelist_excludes_private_values(self):
        self.cached('submitted', notified=True)
        self.current('submitted')
        state.capture(self.root, self.day)
        result = state.read(self.root / '.daily-state/result.json')
        self.assertEqual(set(result), {'date', 'phase', 'notified'})

    def test_missing_result_records_failure_for_notification(self):
        self.assertTrue(state.capture(self.root, self.day)['notify'])
        self.assertEqual(state.read(self.root / '.daily-state/result.json')['phase'], 'failed')


if __name__ == '__main__':
    unittest.main()
