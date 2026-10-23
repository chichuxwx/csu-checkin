"""通过 QQ SMTP 发送最少必要的执行结果，不附带认证文件或位置。"""
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import json
import os
from pathlib import Path
import smtplib
import ssl
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LABELS = {
    'submitted': '打卡成功（已复查）',
    'already_checked': '今日已打卡',
    'submitted_unconfirmed': '提交后尚未确认，请检查',
    'window_closed': '任务等待期间跨日，未打卡',
    'outside_period': '学校当前不允许打卡，未打卡',
    'out_of_range': '位置校验未通过，未打卡',
    'failed': '任务失败，请检查',
}


def build_message(sender, recipient, state, *, event, job_status, run_url, current=None, execution_mode='verify'):
    current = current or datetime.now(ZoneInfo('Asia/Shanghai'))
    if event == 'workflow_dispatch' and execution_mode != 'checkin':
        label = '邮件测试与只查询验证' if job_status == 'success' else '手动验证失败'
        detail = '这是手动测试通知，本次没有提交打卡。'
    else:
        phase = state.get('phase', '')
        label = LABELS.get(phase, '任务未完成，请检查')
        if job_status != 'success' and phase in {'submitted', 'already_checked'}:
            label += '；后续工作流步骤异常'
        detail = '结果：' + LABELS.get(phase, '任务没有留下完成记录，请查看运行日志。')
    message = EmailMessage()
    message['Subject'] = f'[平安打卡] {current:%Y-%m-%d} {label}'
    message['From'] = sender
    message['To'] = recipient
    message['Date'] = formatdate(localtime=False)
    message['Message-ID'] = make_msgid(domain='qq.com')
    lines = [f'北京时间：{current:%Y-%m-%d %H:%M:%S}', detail]
    if (event == 'schedule' or execution_mode == 'checkin') and state.get('planned_at'):
        lines.append('计划时间：' + str(state['planned_at']))
    lines += ['GitHub 工作流状态：' + job_status, '运行记录：' + run_url,
              '', '此邮件不包含学校账号、密码、Cookie、token 或经纬度。']
    message.set_content('\n'.join(lines))
    return message


def main():
    username = os.environ.get('SMTP_USERNAME', '').strip()
    password = os.environ.get('SMTP_PASSWORD', '').strip()
    recipient = os.environ.get('MAIL_TO', '').strip() or username
    if not username or not password:
        print('::error::缺少邮件 Secret MAIL_SMTP_USERNAME 或 MAIL_SMTP_PASSWORD。')
        return 1
    for secret in (username, password, recipient):
        print('::add-mask::' + secret.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'), flush=True)
    try:
        state_path = ROOT / '.local-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    run_url = f'https://github.com/{repository}/actions/runs/{run_id}'
    try:
        message = build_message(username, recipient, state,
                                event=os.environ.get('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                                execution_mode=os.environ.get('CHECKIN_EXECUTION_MODE', 'verify'),
                                job_status=os.environ.get('CHECKIN_JOB_STATUS', 'unknown'), run_url=run_url)
        # 固定 QQ 官方 SMTP 服务，强制 TLS 与证书校验。
        with smtplib.SMTP_SSL('smtp.qq.com', 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(username, password)
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        print('邮件已被 QQ SMTP 服务器接受，请检查收件箱。')
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as file:
                file.write('\n邮件通知：QQ SMTP 服务器已接受发送。\n')
        return 0
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        # SMTP 异常内容可能含地址和服务器返回的私人信息，只显示类别。
        print('::error::邮件发送失败（' + type(exc).__name__ + '），请检查 SMTP 服务与授权码。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
