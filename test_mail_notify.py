from contextlib import redirect_stdout
from io import StringIO
import os
import smtplib
import unittest
from unittest.mock import patch, Mock
import mail_notify as mail


class MailTests(unittest.TestCase):
    def test_success_and_unconfirmed_are_distinct(self):
        for phase, text in [('submitted', '打卡成功'), ('submitted_unconfirmed', '尚未确认'),
                            ('window_closed', '未打卡'), ('out_of_range', '未打卡')]:
            state = {'phase': phase, 'password': 'not-for-email', 'message': 'private-detail'}
            message = mail.build_message('sender@example.com', 'target@example.com', state,
                                         event='schedule', job_status='success', run_url='https://github.com/example/repo/actions/runs/1')
            self.assertIn(text, message['Subject'])
            self.assertNotIn('not-for-email', message.as_string())
            self.assertNotIn('private-detail', message.as_string())

    def test_manual_success_is_explicitly_only_a_test(self):
        message = mail.build_message('sender@example.com', 'target@example.com', {'phase': 'submitted'},
                                     event='workflow_dispatch', job_status='success', run_url='test')
        self.assertIn('邮件测试', message['Subject'])
        self.assertIn('没有提交打卡', message.get_content())
        self.assertNotIn('打卡成功', message['Subject'])

    def test_explicit_manual_checkin_uses_real_outcome(self):
        message = mail.build_message('sender@example.com', 'target@example.com', {'phase': 'already_checked'},
                                     event='workflow_dispatch', execution_mode='checkin',
                                     job_status='success', run_url='test')
        self.assertIn('今日已打卡', message['Subject'])
        self.assertNotIn('邮件测试', message['Subject'])

    def test_smtp_uses_tls_auth_and_checks_refusal(self):
        env = {'SMTP_USERNAME': 'sender@example.com', 'SMTP_PASSWORD': 'test-credential'}
        for refused, expected in [({}, 0), ({'target@example.com': (550, b'no')}, 1)]:
            connection = Mock()
            connection.send_message.return_value = refused
            with patch.dict(os.environ, env, clear=True), patch.object(mail.smtplib, 'SMTP_SSL') as factory, redirect_stdout(StringIO()):
                factory.return_value.__enter__.return_value = connection
                self.assertEqual(mail.main(), expected)
            self.assertEqual(factory.call_args.args, ('smtp.qq.com', 465))
            self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
            connection.login.assert_called_once_with('sender@example.com', 'test-credential')
            self.assertEqual(connection.send_message.call_args.args[0]['To'], 'sender@example.com')

    def test_missing_config_never_connects(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(mail.smtplib, 'SMTP_SSL') as factory, redirect_stdout(StringIO()):
            self.assertEqual(mail.main(), 1)
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
