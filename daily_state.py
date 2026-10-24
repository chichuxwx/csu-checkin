"""在补触发之间传递最小执行结果；不保存认证信息或位置。"""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TERMINAL = {'submitted', 'already_checked', 'submitted_unconfirmed'}
PHASES = TERMINAL | {'failed', 'outside_period', 'out_of_range', 'window_closed'}


def read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def valid(record, day):
    return record.get('date') == day and record.get('phase') in PHASES


def before(root, day):
    record = read(root / '.daily-state/result.json')
    skip = valid(record, day) and record['phase'] in TERMINAL
    if skip:
        write(root / '.local-state.json', {'date': day, 'phase': record['phase']})
    return {'skip': skip, 'blocked': skip and record['phase'] == 'submitted_unconfirmed'}


def capture(root, day):
    previous = read(root / '.daily-state/result.json')
    state = read(root / '.local-state.json')
    if state.get('date') == day and state.get('phase') in {'waiting', 'planned', 'checking'}:
        # 尚未进入提交请求，可以留给下一次补触发重试。
        state = {'date': day, 'phase': 'failed', 'message': '等待或查询阶段中断，尚未进入提交，可由后续任务重试。'}
        write(root / '.local-state.json', state)
    elif state.get('date') == day and state.get('phase') == 'submitting':
        state = {'date': day, 'phase': 'submitted_unconfirmed'}
        write(root / '.local-state.json', state)
    if not valid(state, day):
        state = {'date': day, 'phase': 'failed'}
        write(root / '.local-state.json', state)
    notified = (valid(previous, day) and previous['phase'] == state['phase']
                and previous.get('notified') is True)
    record = {'date': day, 'phase': state['phase'], 'notified': notified}
    write(root / '.daily-state/result.json', record)
    return {'notify': not notified}


def recover_waiting(root, day, source_run, matched_key):
    """仅供操作员核实源运行未提交后恢复旧版误判；不自动解除真实提交保护。"""
    expected = rf'csu-daily-v1-{re.escape(day)}-{re.escape(source_run)}-\d+-(?:final|checkpoint)'
    record = read(root / '.daily-state/result.json')
    if (not source_run.isdigit() or not re.fullmatch(expected, matched_key)
            or not valid(record, day) or record['phase'] != 'submitted_unconfirmed'):
        raise ValueError('缓存来源或状态不匹配，保留原有提交保护。')
    write(root / '.daily-state/result.json', {'date': day, 'phase': 'failed', 'notified': False})
    print('已恢复经人工核实仅在等待阶段中断的旧记录；本操作没有提交打卡或发送邮件。')


def mark_mail(root, day, sent):
    record = read(root / '.daily-state/result.json')
    if valid(record, day):
        write(root / '.daily-state/result.json', {
            'date': day, 'phase': record['phase'],
            'notified': sent or record.get('notified') is True,
        })


def main():
    action = sys.argv[1]
    day = os.environ.get('CHECKIN_DAY') or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if action == 'before':
        result = before(ROOT, day)
        if result['skip']:
            print('已有今日完成记录或待人工确认记录，本轮不再提交。')
    elif action == 'capture':
        result = capture(ROOT, day)
    elif action == 'mail':
        mark_mail(ROOT, day, os.environ.get('CHECKIN_MAIL_SENT') == 'true')
        result = {}
    elif action == 'recover-waiting':
        recover_waiting(ROOT, day, os.environ.get('CHECKIN_RECOVER_RUN', ''),
                        os.environ.get('CHECKIN_CACHE_KEY', ''))
        result = {}
    else:
        raise ValueError('未知状态操作')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            for key, value in result.items():
                stream.write(f'{key}={str(value).lower()}\n')
    return 1 if result.get('blocked') else 0


if __name__ == '__main__':
    raise SystemExit(main())
