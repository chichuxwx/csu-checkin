# CSU 平安打卡

基于 [IAMNAVY/fuck-csu-checkin](https://github.com/IAMNAVY/fuck-csu-checkin) 的 Python 3.12 项目，使用 GitHub Actions 定时运行，完成后通过 QQ SMTP 发送结果邮件。云端部署后无需个人电脑开机或浏览器常驻。

## 复用与部署

### 1. Fork 并启用 Actions

Fork 到自己的 GitHub 账号，在自己的仓库打开 **Actions**，按页面提示启用工作流。配置在自己的仓库完成，无需修改 Python 源码。

Fork 不会复制原仓库的 Secrets；下面的学校凭据、邮箱和开关都要自行配置，不能复用其他人的认证信息。

### 2. 配置 Secrets 和 Variables

进入自己仓库的 **Settings → Secrets and variables → Actions**，按表格分别添加：

| 名称 | 添加位置 | 内容 |
| --- | --- | --- |
| `CHECKIN_AUTH_JSON` | **Secrets → New repository secret** | 自己的学校认证与实际位置 JSON，格式见下文 |
| `MAIL_SMTP_USERNAME` | **Secrets → New repository secret** | 自己的完整 QQ 邮箱地址，例如 `your-address@qq.com` |
| `MAIL_SMTP_PASSWORD` | **Secrets → New repository secret** | QQ 邮箱 SMTP 授权码，不是 QQ 登录密码 |
| `CHECKIN_ENABLED` | **Variables → New repository variable** | 初次配置填 `false`，验证通过后改为 `true` |

**`CHECKIN_ENABLED` 必须是仓库变量（Variable），填在 Secrets 中不会启用定时打卡。** 值使用小写 `true`，不要加引号、空格，也不要把整句 `CHECKIN_ENABLED=true` 填进值框。未配置、填 `false` 或其他值时，定时任务跳过；手动只查询验证仍可运行。

### 3. 配置自己的学校认证

将下面 JSON 修改后，整体粘贴到 `CHECKIN_AUTH_JSON` 的值中。示例账号、密码和坐标均为占位内容；`110.0, 30.0` 是虚构坐标，必须替换为自己的实际位置。

```json
{
  "credentials": {
    "username": "your-student-id",
    "password": "your-school-password"
  },
  "cookies": [],
  "business": {},
  "settings": {
    "lng": 110.0,
    "lat": 30.0,
    "coord": "gcj02",
    "dklb": "PA"
  }
}
```

- `credentials`：自己的统一认证学号和密码。
- `settings`：`lng` 是经度，`lat` 是纬度；`coord` 与坐标来源一致，支持 `gcj02`（高德）、`wgs84`、`bd09`（百度）；`dklb: PA` 表示平安打卡。位置变化时需更新，云端不会读取手机实时 GPS。
- `business`：可先保持 `{}`，脚本会尝试正常登录获取业务凭据；已有自己的有效配置时，可填 `{"token": "自己的业务token", "casual": "与其配套的casual"}`。凭据失效会尝试重新登录，并不代表到期后永久无法使用。
- `cookies`：无可用记录时可先用 `[]`。如果学校要求二次认证，需本人先在学校官方页面完成认证，再更新自己的登录记录；只有账号密码不保证每次都能通过二次认证。

已有本机认证文件时，对应关系为：`.local-credentials.json` → `credentials`，`.cas-session.json` → `cookies`，`checkin_config.json` → `business`；实际位置填入 `settings`。这些文件包含敏感信息，只能存入自己的 Secret，不应上传仓库。

Cookie 使用对象数组，每项保留 `name`、`value`、`domain`、`path`。只加载 `ca.csu.edu.cn` 和 `zhxg.csu.edu.cn` 的记录；同名 Cookie 也要保留各自域名和路径，不能合并。认证摘要或显示“已配置”的核对报告不能替代这些真实配置。

### 4. 配置 QQ SMTP 并验证

在自己的 QQ 邮箱网页版设置中找到 POP3/IMAP/SMTP 服务，开启 SMTP 并生成授权码（通常位于账号与安全相关设置，入口名称可能调整）。将完整邮箱和授权码分别存入上表两个邮件 Secrets。

当前工作流使用 **QQ 邮箱 `smtp.qq.com:465`**，强制 TLS 和证书校验；邮件发往发信邮箱自身。直接填写 Gmail、163 邮箱不会切换 SMTP 服务商；当前无需设置 `SMTP_HOST`、`SMTP_PORT` 或 `MAIL_TO`。

进入 **Actions → 平安打卡 → Run workflow**，选择默认分支，保持 `mode=verify`，并勾选：

- `verify_login`：验证云端重新登录，默认开启。
- `notify_email`：发送测试邮件，默认关闭，首次部署请开启。

确认日志显示云端登录、查询成功，并实际收到标注“邮件测试与只查询验证”的邮件。**`mode=verify` 不提交打卡，测试邮件和绿色状态均不代表今日已打卡。** SMTP 接受发送也不等于已送达，首次需检查收件箱及垃圾邮件。

### 5. 启用或暂停每天自动运行

验证通过后，在 **Variables** 中将 `CHECKIN_ENABLED` 改为 `true`，保存后即启用后续定时任务；不需要改代码，也不需要保持电脑开机。暂停后续定时任务时改回 `false`；这不会取消已经开始的运行，如需停止当次运行请在 Actions 页面取消。

云端验证成功后，停用其他设备的同类定时任务，避免重复运行。

需要立即补打时，在 Run workflow 中明确选择 `mode=checkin`。此模式会真实提交，仍要求 `CHECKIN_ENABLED=true`，并检查今日状态和学校允许时段；已打卡或已有待人工确认记录时不重复提交。

## 执行时间与结果

- 每天北京时间 **20:13** 首次计划触发；正常启动时在 **20:13–20:23** 内随机执行一次。随后在 **20:43、21:13、21:43、22:13、22:43、23:03、23:13、23:23** 补触发，23:43 再核验一次（超出学校允许时段时只报告未完成），降低单次调度丢失的影响；后续触发仍可能延迟，是否允许提交以学校接口为准。
- **20:23 只是随机等待终点。** 延迟启动或恢复时立即尝试，仍以学校接口返回的允许时段和范围为准；跨日等待不会补交前一天。
- 使用配置的实际坐标；已打卡或时段不允许时跳过提交。普通平安打卡（`PA`，学校返回 `lx=0/1/3`）按当前官方 H5 流程直接上报实际位置，不调用留校考勤的范围接口；留校考勤及未识别类型仍做范围校验，失败时不提交。
- 提交接口返回成功后，再查询确认今日已打卡；未确认时不会自动重复提交。每日任务只有确认已打卡（或学校本来就显示已打）才显示成功，其余未完成状态显示失败。
- 通过 Actions 缓存保存当日最小结果（日期、状态、是否已发邮件），补触发已完成时跳过；同一状态邮件发送成功后不重复发送，失败后恢复成功会发送新结果。发信失败可在补触发时重试，提交后未确认会停止当天自动补交。
- 缓存不含凭据、Cookie 或坐标；缓存缺失时仍先向学校查询是否已打卡。缓存被清除或驱逐后，通知去重记录也会丢失，因此不保证跨运行的严格一次发送。
- 每日任务发邮件说明成功、尚未确认、未打卡或失败。邮件不包含学校凭据和坐标；发信错误单独显示，不会因此重新提交打卡。
- GitHub 调度可能延迟或未触发，任务也可能被取消、超时；不能保证固定时间执行或收到邮件。若任务根本没触发，该工作流也无法发邮件，未收到邮件不能视为成功。
- 定时任务在北京时间 20:13 之前才到达时，按可能跨日的过期触发处理，立即报告未提交，不等待到当天晚上。等待或查询阶段中断允许后续重试；只有真正进入提交请求后中断才锁定为待确认。
- 旧版可能把等待超时误记为待确认。仅在核查源运行日志、确认未进入提交后，才能在手动 `mode=verify` 中填写 `recover_waiting_run` 为该运行 ID，恢复匹配来源的当日缓存；默认留空。不确定是否提交过时不要恢复，先在学校页面核实。恢复操作本身不提交打卡，也不发邮件。
- 学校要求重新二次认证时，需本人完成并更新 `CHECKIN_AUTH_JSON`，之后重新进行只查询验证。
- 云端随机等待会计入任务运行时长；私有仓库需留意 Actions 分钟额度。代码不会主动开启付费预算。多次补触发会增加少量运行时长。

学校页面仍会获取位置；没有弹出权限提示不等于取消了定位。上述范围校验分支依据 [学校当前 H5 打卡页面代码](https://zhxg.csu.edu.cn/znzhxgpt_h5/static/js/ywViews-qxjlfx-dk-index.725fb923.js) 核对（2026-10-23）：只有 `lx=2` 的留校类型调用 `checkDkLocation`。不应把该接口对普通 `PA` 返回的 `NOT_ALLOWED` 直接理解为实际位置超出范围。

## 本地检查

使用 Python 3.12 安装依赖并运行离线测试：

```sh
python -m pip install -r requirements.txt
python -m unittest discover -v
```

已有自己的本机认证和位置配置后，可只查询验证：

```sh
python local_runner.py --dry-run
```

云端认证文件仅在临时 runner 中使用，执行结束时清理，不上传为产物。不要把账号密码、Cookie、token、SMTP 授权码或真实位置提交进源码、Issue 或公开日志。
