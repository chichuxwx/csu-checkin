"""本机平安打卡：每日随机计划、登录续期与重复提交保护。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, time as daytime, timedelta
import fcntl
import json
import os
import re
from pathlib import Path
import secrets
import time
from zoneinfo import ZoneInfo

import requests

from api import CheckinClient
from auth import LoginError, _token_expired, cas_login
from config import load_config
from coordinates import to_gcj02

ROOT = Path(__file__).resolve().parent
ZONE = ZoneInfo('Asia/Shanghai')
STATE = ROOT / '.local-state.json'
CREDENTIALS = ROOT / '.local-credentials.json'
COOKIES = ROOT / '.cas-session.json'
SUCCESS = {'submitted', 'already_checked'}
NO_RESUBMIT = SUCCESS | {'submitted_unconfirmed'}


def now():
    return datetime.now(ZONE)


def log(message):
    print(f'[{now().isoformat(timespec="seconds")}] {message}', flush=True)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {} if default is None else default


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    path.chmod(0o600)


def load_environment():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator or not key.startswith('CHECKIN_') or not key.replace('_', '').isalnum():
                raise ValueError('.env 中存在不支持的配置行')
            os.environ.setdefault(key, value.strip().strip('\"\''))


def true(value):
    return value is True or str(value).lower() in {'1', 'true'}


def window(current):
    return (datetime.combine(current.date(), daytime(20, 13), ZONE),
            datetime.combine(current.date(), daytime(20, 23), ZONE))


def make_plan(current, previous, choose=secrets.randbelow):
    date = current.date().isoformat()
    start, end = window(current)
    if previous.get('date') == date and previous.get('phase') in NO_RESUBMIT:
        return previous
    if previous.get('date') == date and previous.get('planned_at'):
        selected = datetime.fromisoformat(previous['planned_at'])
        if start <= selected <= max(end, current):
            return previous
    earliest = max(start, current.replace(microsecond=0) + timedelta(seconds=bool(current.microsecond)))
    # 20:23 仅是随机等待的终点；延迟启动不再因超过它而放弃。
    selected = (earliest if earliest >= end else
                earliest + timedelta(seconds=choose(int((end - earliest).total_seconds()) + 1)))
    return {'date': date, 'planned_at': selected.isoformat(), 'phase': 'planned'}


def get_auth(force=False):
    config = load_config()
    if not force and config.get('token') and config.get('casual') and not _token_expired(config['token']):
        return config['token'], config['casual']
    credentials = read_json(CREDENTIALS)
    if not credentials.get('username') or not credentials.get('password'):
        raise LoginError('缺少本地登录配置，请重新配置学校认证。')
    session = requests.Session()
    for cookie in read_json(COOKIES, []):
        if cookie.get('domain', '').lstrip('.') in {'ca.csu.edu.cn', 'zhxg.csu.edu.cn'}:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path', '/'))
    log('登录态已失效，使用本机保存的已验证浏览器记录重新登录。')
    token, casual, _, student = cas_login(credentials['username'], credentials['password'], session=session)
    if student != credentials['username']:
        raise LoginError('登录账号与本地配置不一致，已停止。')
    write_json(ROOT / 'checkin_config.json', {'token': token, 'casual': casual})
    write_json(COOKIES, [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
                        for c in session.cookies if c.domain.lstrip('.') in {'ca.csu.edu.cn', 'zhxg.csu.edu.cn'}])
    return token, casual


def error_message(exc):
    if isinstance(exc, requests.RequestException):
        return type(exc).__name__ + '：网络请求失败，请检查学校服务和网络。'
    return type(exc).__name__ + '：' + re.sub(r'https?://\S+', '[请求地址已隐藏]', str(exc))


def run_once(submit=False):
    load_environment()
    dklb = os.environ.get('CHECKIN_DKLB') or 'PA'
    lng, lat = float(os.environ['CHECKIN_LNG']), float(os.environ['CHECKIN_LAT'])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError('经纬度超出合法范围')
    lng, lat = to_gcj02(lng, lat, os.environ.get('CHECKIN_COORD', 'gcj02'))
    client = CheckinClient(*get_auth())
    info = client.query_dkbc(dklb)
    if str(info.get('code')) in {'401', '403', '331'}:
        client = CheckinClient(*get_auth(force=True))
        info = client.query_dkbc(dklb)
    if str(info.get('code')) != '200' or not isinstance(info.get('data'), dict):
        raise RuntimeError(f'班次查询失败，接口状态 {info.get("code")}')
    data = info['data']
    log(f'学校打卡时段：{data.get("dksjfw")}；可打：{data.get("kdk")}；已打：{data.get("sfydk")}')
    if true(data.get('sfydk')):
        return {'phase': 'already_checked', 'message': '学校显示已打卡，跳过重复提交。'}
    if not true(data.get('kdk')):
        return {'phase': 'outside_period', 'message': '学校当前不允许打卡，未提交。'}
    if not data.get('dkbc'):
        raise RuntimeError('学校未返回有效打卡班次，未提交。')
    # 学校 H5 仅对 lx=2 的留校考勤调用范围接口；普通 PA 仍上报实际位置。
    # 未识别类型继续校验，避免把接口字段变化当成无需校验。
    check_scope = dklb != 'PA' or str(data.get('lx')) not in {'0', '1', '3'}
    if check_scope:
        checked = client.check_range(lng, lat, dklb)
        if str(checked.get('code')) != '200' or not isinstance(checked.get('data'), dict):
            raise RuntimeError('位置校验失败，未提交。')
        if not true(checked['data'].get('canDk')):
            return {'phase': 'out_of_range', 'message': '配置坐标未通过学校范围校验，未提交。'}
    else:
        log('学校返回普通平安打卡类型，按官方页面流程直接上报实际位置，不调用留校范围接口。')
    if not submit:
        return {'phase': 'dry_run', 'message': '查询及所需校验通过；检查模式未提交。'}
    # 必须在请求发出前落盘；只有到达此处，中断后才存在重复上报风险。
    write_json(STATE, {'date': now().date().isoformat(), 'phase': 'submitting'})
    try:
        result = client.submit(lng, lat, data['dkbc'], dkdz=os.environ.get('CHECKIN_DKDZ', ''))
    except (requests.RequestException, ValueError):
        return {'phase': 'submitted_unconfirmed', 'message': '提交请求异常，结果无法确认；请人工检查，不自动重复提交。'}
    if str(result.get('code')) != '200':
        raise RuntimeError(f'打卡接口未返回成功，状态 {result.get("code")}；不自动重复提交。')
    # 提交只执行一次；后续请求仅用于确认学校确实记录为已打卡。
    for attempt in range(3):
        try:
            confirmation = client.query_dkbc(dklb)
            details = confirmation.get('data')
            if str(confirmation.get('code')) == '200' and isinstance(details, dict) and true(details.get('sfydk')):
                return {'phase': 'submitted', 'message': '学校接口返回成功，复查确认今日已打卡。'}
        except (requests.RequestException, ValueError):
            pass
        if attempt < 2:
            time.sleep(1)
    return {'phase': 'submitted_unconfirmed', 'message': '提交接口返回成功，但复查尚未确认已打卡；请人工检查，不自动重复提交。'}


@contextmanager
def exclusive():
    with open(ROOT / '.local-state.lock', 'a', encoding='utf-8') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一个本地打卡任务正在运行。')
        yield


def scheduled():
    with exclusive():
        current = now()
        if os.environ.get('GITHUB_EVENT_NAME') == 'schedule' and current < window(current)[0]:
            write_json(STATE, {'date': current.date().isoformat(), 'phase': 'window_closed',
                              'message': '定时任务在当晚计划时段之前到达，可能已延迟跨日；不等待到下一晚，也未提交。'})
            log('定时任务到达时间不在当晚执行时段，停止等待，未提交。')
            return 1
        state = make_plan(current, read_json(STATE))
        write_json(STATE, state)
        if state.get('phase') in NO_RESUBMIT:
            log('今日已有提交记录，不再重复提交。')
            return 0 if state['phase'] in SUCCESS else 1
        target = datetime.fromisoformat(state['planned_at'])
        state['phase'] = 'waiting'
        write_json(STATE, state)
        log('今日随机执行时间：' + target.isoformat(timespec='seconds'))
        while now() < target:
            time.sleep(min(30, max(0, (target - now()).total_seconds())))
        if now().date().isoformat() != state['date']:
            state.update(phase='window_closed', message='任务等待期间已跨日，不提交上一日的打卡。')
        else:
            state['phase'] = 'checking'
            write_json(STATE, state)
            try:
                state.update(run_once(submit=True))
            except (LoginError, requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
                state.update(phase='failed', message=error_message(exc))
        state['finished_at'] = now().isoformat()
        write_json(STATE, state)
        log(state.get('message', state['phase']))
        return 0 if state['phase'] in SUCCESS else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='本机平安打卡：默认只查询，不提交')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--scheduled', action='store_true', help='等待今日随机时间，执行一次真实打卡')
    mode.add_argument('--plan', action='store_true', help='仅生成/查看今日随机计划，不访问接口')
    mode.add_argument('--status', action='store_true', help='查看最近运行状态')
    mode.add_argument('--submit', action='store_true', help='立即尝试真实打卡，仍检查学校时段和范围')
    mode.add_argument('--dry-run', action='store_true', help='只查询和校验，不提交（默认）')
    args = parser.parse_args()
    try:
        if args.status:
            print(json.dumps(read_json(STATE), ensure_ascii=False, indent=2))
            return 0
        if args.plan:
            with exclusive():
                state = make_plan(now(), read_json(STATE))
                write_json(STATE, state)
                print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        if args.scheduled:
            return scheduled()
        if args.submit:
            with exclusive():
                previous = read_json(STATE)
                if previous.get('date') == now().date().isoformat() and previous.get('phase') in NO_RESUBMIT:
                    log('本地记录显示今日已经完成，不重复提交。')
                    return 0
                result = run_once(submit=True)
                write_json(STATE, dict(previous if previous.get('date') == now().date().isoformat() else {},
                                       date=now().date().isoformat(), finished_at=now().isoformat(), **result))
        else:
            result = run_once()
        log(result['message'])
        return 0
    except (LoginError, requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
        # 不输出请求 URL、响应体或凭据。
        log(error_message(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
