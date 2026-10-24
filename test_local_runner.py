from datetime import datetime
import os
import unittest
from unittest.mock import Mock, patch

import local_runner as runner


class LocalRunnerTests(unittest.TestCase):
    def test_plan_covers_full_window_and_reuses_chosen_time(self):
        current = datetime(2026, 10, 23, 9, 0, tzinfo=runner.ZONE)
        first = runner.make_plan(current, {}, choose=lambda n: 0)
        last = runner.make_plan(current, {}, choose=lambda n: n - 1)
        self.assertEqual(first['planned_at'], '2026-10-23T20:13:00+08:00')
        self.assertEqual(last['planned_at'], '2026-10-23T20:23:00+08:00')
        self.assertEqual(runner.make_plan(current, last), last)

    def test_late_wakeup_never_schedules_in_past(self):
        current = datetime(2026, 10, 23, 20, 18, 3, 500000, tzinfo=runner.ZONE)
        plan = runner.make_plan(current, {}, choose=lambda n: 0)
        self.assertGreaterEqual(datetime.fromisoformat(plan['planned_at']), current)

    def test_late_start_runs_immediately_and_success_is_preserved(self):
        current = datetime(2026, 10, 23, 23, 0, tzinfo=runner.ZONE)
        plan = runner.make_plan(current, {}, choose=Mock(side_effect=AssertionError('不应继续随机等待')))
        self.assertEqual(datetime.fromisoformat(plan['planned_at']), current)
        done = {'date': '2026-10-23', 'phase': 'submitted'}
        self.assertEqual(runner.make_plan(current, done), done)

    def invoke(self, *, submit=False, can=True, kdk=True, already=0, confirmed=True, lx=None, dklb='PA', submit_error=None):
        client = Mock()
        initial = {'code': '200', 'data': {'dkbc': 'test-shift', 'kdk': kdk, 'sfydk': already}}
        if lx is not None:
            initial['data']['lx'] = lx
        client.query_dkbc.side_effect = [initial] + [{'code': '200', 'data': {'sfydk': int(confirmed)}}] * 3
        client.check_range.return_value = {'code': '200', 'data': {'canDk': can}}
        client.submit.return_value = {'code': '200'}
        client.submit.side_effect = submit_error
        with patch.dict(os.environ, {'CHECKIN_LNG': '110.0', 'CHECKIN_LAT': '30.0', 'CHECKIN_COORD': 'gcj02', 'CHECKIN_DKLB': dklb}), \
                patch.object(runner.time, 'sleep'), patch.object(runner, 'write_json'), patch.object(runner, 'load_environment'), patch.object(runner, 'get_auth', return_value=('test', 'test')), \
                patch.object(runner, 'CheckinClient', return_value=client):
            result = runner.run_once(submit=submit)
        return result, client

    def test_dry_run_checks_supplied_coordinates_and_never_submits(self):
        result, client = self.invoke()
        self.assertEqual(result['phase'], 'dry_run')
        client.check_range.assert_called_once_with(110.0, 30.0, 'PA')
        client.submit.assert_not_called()

    def test_range_failure_never_submits(self):
        result, client = self.invoke(submit=True, can=False)
        self.assertEqual(result['phase'], 'out_of_range')
        client.submit.assert_not_called()

    def test_regular_pa_does_not_use_inapplicable_range_endpoint(self):
        for lx in (0, 1, 3, '3'):
            with self.subTest(lx=lx):
                result, client = self.invoke(submit=True, can=False, lx=lx)
                self.assertEqual(result['phase'], 'submitted')
                client.check_range.assert_not_called()
                client.submit.assert_called_once_with(110.0, 30.0, 'test-shift', dkdz='')

    def test_regular_pa_dry_run_never_submits(self):
        result, client = self.invoke(lx=3, can=False)
        self.assertEqual(result['phase'], 'dry_run')
        client.check_range.assert_not_called()
        client.submit.assert_not_called()

    def test_stay_on_campus_still_enforces_range(self):
        result, client = self.invoke(submit=True, lx=2, dklb='LX', can=False)
        self.assertEqual(result['phase'], 'out_of_range')
        client.check_range.assert_called_once_with(110.0, 30.0, 'LX')
        client.submit.assert_not_called()

    def test_unknown_pa_type_still_enforces_range(self):
        for lx in (None, '', 99):
            with self.subTest(lx=lx):
                result, client = self.invoke(submit=True, lx=lx, can=False)
                self.assertEqual(result['phase'], 'out_of_range')
                client.check_range.assert_called_once_with(110.0, 30.0, 'PA')
                client.submit.assert_not_called()

    def test_already_checked_never_submits(self):
        result, client = self.invoke(submit=True, already='1')
        self.assertEqual(result['phase'], 'already_checked')
        client.submit.assert_not_called()

    def test_string_false_does_not_allow_checkin(self):
        result, client = self.invoke(submit=True, kdk='false')
        self.assertEqual(result['phase'], 'outside_period')
        client.submit.assert_not_called()

    def test_success_submits_once_at_supplied_location(self):
        result, client = self.invoke(submit=True)
        self.assertEqual(result['phase'], 'submitted')
        client.submit.assert_called_once_with(110.0, 30.0, 'test-shift', dkdz='')

    def test_unconfirmed_submission_never_reposts(self):
        result, client = self.invoke(submit=True, confirmed=False)
        self.assertEqual(result['phase'], 'submitted_unconfirmed')
        self.assertEqual(client.query_dkbc.call_count, 4)
        self.assertEqual(client.submit.call_count, 1)
        current = datetime(2026, 10, 23, 20, 18, tzinfo=runner.ZONE)
        previous = {'date': current.date().isoformat(), **result}
        self.assertEqual(runner.make_plan(current, previous), previous)

    def test_submit_timeout_requires_confirmation_before_retry(self):
        result, client = self.invoke(submit=True, lx=3, submit_error=runner.requests.Timeout())
        self.assertEqual(result['phase'], 'submitted_unconfirmed')
        self.assertEqual(client.submit.call_count, 1)
        self.assertEqual(client.query_dkbc.call_count, 1)

    def run_scheduled_at(self, current, previous=None, result=None):
        from contextlib import nullcontext
        with patch.object(runner, 'exclusive', return_value=nullcontext()), \
                patch.object(runner, 'now', return_value=current), \
                patch.object(runner, 'read_json', return_value=previous or {}), \
                patch.object(runner, 'write_json'), patch.object(runner, 'run_once', return_value=result or {'phase': 'submitted'}) as run:
            status = runner.scheduled()
        return status, run

    def test_delayed_job_attempts_after_2023(self):
        current = datetime(2026, 10, 23, 20, 24, tzinfo=runner.ZONE)
        status, run = self.run_scheduled_at(current)
        self.assertEqual(status, 0)
        run.assert_called_once_with(submit=True)

    def test_old_window_closed_state_is_recoverable(self):
        current = datetime(2026, 10, 23, 21, 0, tzinfo=runner.ZONE)
        status, run = self.run_scheduled_at(current, {'date': '2026-10-23', 'phase': 'window_closed'})
        self.assertEqual(status, 0)
        run.assert_called_once_with(submit=True)

    def test_late_wakeup_executes_existing_plan(self):
        current = datetime(2026, 10, 23, 21, 0, tzinfo=runner.ZONE)
        previous = {'date': '2026-10-23', 'planned_at': '2026-10-23T20:20:00+08:00', 'phase': 'waiting'}
        status, run = self.run_scheduled_at(current, previous)
        self.assertEqual(status, 0)
        run.assert_called_once_with(submit=True)

    def test_delayed_job_respects_school_closed_result(self):
        current = datetime(2026, 10, 23, 23, 45, tzinfo=runner.ZONE)
        status, run = self.run_scheduled_at(current, result={'phase': 'outside_period'})
        self.assertEqual(status, 1)
        run.assert_called_once_with(submit=True)

    def test_waiting_across_midnight_never_submits(self):
        from contextlib import nullcontext
        start = datetime(2026, 10, 23, 20, 13, tzinfo=runner.ZONE)
        next_day = datetime(2026, 10, 24, 0, 0, tzinfo=runner.ZONE)
        previous = {'date': '2026-10-23', 'planned_at': '2026-10-23T20:20:00+08:00'}
        with patch.object(runner, 'exclusive', return_value=nullcontext()), \
                patch.object(runner, 'now', side_effect=[start, next_day, next_day, next_day]), \
                patch.object(runner, 'read_json', return_value=previous), \
                patch.object(runner, 'write_json') as write, patch.object(runner, 'log'), \
                patch.object(runner, 'run_once') as run:
            self.assertEqual(runner.scheduled(), 1)
        run.assert_not_called()
        self.assertEqual(write.call_args.args[1]['phase'], 'window_closed')

    def test_late_same_day_submission_is_not_repeated(self):
        current = datetime(2026, 10, 23, 21, 0, tzinfo=runner.ZONE)
        status, run = self.run_scheduled_at(current, {'date': '2026-10-23', 'phase': 'submitted'})
        self.assertEqual(status, 0)
        run.assert_not_called()

    def test_cross_day_cloud_schedule_never_waits_or_calls_school(self):
        for hour in (2, 3, 19):
            with self.subTest(hour=hour), patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule'}), \
                    patch.object(runner.time, 'sleep') as sleep:
                status, run = self.run_scheduled_at(datetime(2026, 10, 25, hour, 0, tzinfo=runner.ZONE))
                self.assertEqual(status, 1)
                run.assert_not_called()
                sleep.assert_not_called()

    def test_submission_marker_is_persisted_before_network(self):
        seen = []
        client = Mock()
        client.query_dkbc.return_value = {'code': 200, 'data': {'kdk': True, 'sfydk': 0, 'lx': 3, 'dkbc': 'test'}}
        def submit(*args, **kwargs):
            self.assertEqual(seen[-1]['phase'], 'submitting')
            raise runner.requests.Timeout()
        client.submit.side_effect = submit
        with patch.dict(os.environ, {'CHECKIN_LNG': '110', 'CHECKIN_LAT': '30', 'CHECKIN_DKLB': 'PA', 'CHECKIN_COORD': 'gcj02'}), \
                patch.object(runner, 'load_environment'), patch.object(runner, 'get_auth', return_value=('test', 'test')), \
                patch.object(runner, 'CheckinClient', return_value=client), \
                patch.object(runner, 'write_json', side_effect=lambda path, value: seen.append(dict(value))):
            self.assertEqual(runner.run_once(submit=True)['phase'], 'submitted_unconfirmed')


if __name__ == '__main__':
    unittest.main()
