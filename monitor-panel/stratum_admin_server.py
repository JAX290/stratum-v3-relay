#!/usr/bin/env python3
import html
import json
import os
import secrets
import socket
import subprocess
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash

from metrics import collector


ENV_FILE = Path("/etc/stratum-monitor.env")
LOG_FILE = Path("/var/log/stratum-monitor.log")
CRON_FILE = Path("/etc/cron.d/stratum-monitor")
INSPECTOR_STATE_FILE = Path("/var/lib/stratum-inspector/state.json")
PORTS = (("欧洲", 9999), ("俄罗斯", 10001), ("白俄罗斯", 10002))
UPSTREAMS = (
    ("欧洲", "eu.pool.hash-hut.net", 9999),
    ("俄罗斯", "ru.pool.hash-hut.net", 9999),
    ("白俄罗斯", "by.pool.hash-hut.net", 9999),
)

app = Flask(__name__)
app.secret_key = os.environ["PANEL_SECRET_KEY"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")
collector.start()


def read_env():
    values = {}
    if not ENV_FILE.exists():
        return values
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def write_env(updates):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    seen = set()
    output = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    fd, temp_name = tempfile.mkstemp(prefix="stratum-monitor.", dir=str(ENV_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, ENV_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def require_login():
    return session.get("authenticated") is True


def csrf_ok():
    return secrets.compare_digest(session.get("csrf", ""), request.form.get("csrf", ""))


def tcp_check(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def service_active():
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", "haproxy"],
        check=False,
        capture_output=True,
        timeout=3,
    )
    return result.returncode == 0


def monitor_enabled():
    if not CRON_FILE.exists():
        return False
    return any(
        line.strip() and not line.lstrip().startswith("#") and "stratum-monitor.sh" in line
        for line in CRON_FILE.read_text(encoding="utf-8").splitlines()
    )


def set_monitor_enabled(enabled):
    content = (
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        + ("* * * * * root /root/stratum-monitor.sh\n" if enabled else "# Monitoring paused from web panel\n")
    )
    CRON_FILE.write_text(content, encoding="utf-8")
    os.chmod(CRON_FILE, 0o644)


def recent_log():
    if not LOG_FILE.exists():
        return "暂无日志"
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
    return "\n".join(lines) or "暂无日志"


def inspector_state():
    try:
        data = json.loads(INSPECTOR_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


PAGE = r"""
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>中转监控</title><style>
:root{color-scheme:light;--bg:#f2f5f7;--panel:#fff;--ink:#14212b;--muted:#66747e;--line:#dce3e8;--blue:#146ef5;--green:#168b55;--red:#c53b3b;--amber:#a96a12}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0}.top{background:#17242d;color:#fff}.wrap{width:min(1080px,calc(100% - 28px));margin:auto}.top .wrap{display:flex;align-items:center;justify-content:space-between;min-height:64px}.top h1{font-size:19px;margin:0}.top-actions{display:flex;align-items:center;gap:16px}.top a{color:#fff;text-decoration:none;font-size:13px}.muted{color:var(--muted)}.small{font-size:12px}main{padding:20px 0 44px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px}.wide{grid-column:1/-1}h2{font-size:16px;margin:0 0 14px}.statuses{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.status{padding:10px 0;border-right:1px solid var(--line)}.status:last-child{border:0}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;background:var(--red)}.ok .dot{background:var(--green)}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0}.metric{padding:7px 14px;border-left:1px solid var(--line);min-width:0}.metric:nth-child(4n+1){border-left:0}.metric .value{display:block;font-size:20px;font-weight:700;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.metric .label{color:var(--muted);font-size:12px}.hardware{margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:700px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-size:12px;font-weight:600}tbody tr:last-child td{border-bottom:0}.good{color:var(--green);font-weight:600}.bad{color:var(--red);font-weight:600}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}label{display:block;color:var(--muted);font-size:13px;margin-bottom:5px}input{width:100%;padding:10px;border:1px solid #bac5cc;border-radius:5px;font:inherit}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}button{border:0;border-radius:5px;padding:10px 14px;background:var(--blue);color:#fff;font:600 14px inherit;cursor:pointer}.secondary{background:#52616b}.danger{background:#a83434}.flash{padding:10px 12px;background:#e7f4ec;color:#116b42;border-radius:5px;margin-bottom:14px}pre{margin:0;background:#101a20;color:#dce8ee;border-radius:6px;padding:13px;overflow:auto;max-height:320px;font:12px/1.55 ui-monospace,monospace;white-space:pre-wrap}.empty{padding:20px 8px;color:var(--muted);text-align:center}
@media(max-width:760px){.grid{grid-template-columns:1fr}.fields{grid-template-columns:1fr}.statuses,.metrics{grid-template-columns:repeat(2,1fr)}.status:nth-child(2){border:0}.metric:nth-child(odd){border-left:0}.panel{padding:15px}main{padding-top:14px}.metric .value{font-size:17px}}
</style></head><body><header class="top"><div class="wrap"><h1>Hash-Hut 中转监控</h1><div class="top-actions"><a href="{{url_for('index')}}">刷新</a><a href="{{url_for('logout')}}">退出</a></div></div></header>
<main class="wrap">{% with messages=get_flashed_messages() %}{% for message in messages %}<div class="flash">{{message}}</div>{% endfor %}{% endwith %}
<div class="grid"><section class="panel wide"><h2>运行状态</h2><div class="statuses"><div class="status {{'ok' if haproxy else ''}}"><span class="dot"></span>HAProxy {{'正常' if haproxy else '异常'}}</div>{% for name,port,ok in local_ports %}<div class="status {{'ok' if ok else ''}}"><span class="dot"></span>{{name}} :{{port}} {{'监听中' if ok else '不可用'}}</div>{% endfor %}</div></section>

<section class="panel wide"><h2>服务器资源</h2>{% if metrics.server %}<div class="metrics">
<div class="metric"><span class="label">CPU 使用率</span><span class="value">{{metrics.server.cpu_percent}}%</span></div><div class="metric"><span class="label">系统负载 1/5/15</span><span class="value">{{metrics.server.load}}</span></div><div class="metric"><span class="label">内存</span><span class="value">{{metrics.server.memory_percent}}%</span><span class="small muted">{{metrics.server.memory}}</span></div><div class="metric"><span class="label">磁盘</span><span class="value">{{metrics.server.disk_percent}}%</span><span class="small muted">{{metrics.server.disk}}</span></div>
<div class="metric"><span class="label">实时下载</span><span class="value">{{metrics.server.down_rate}}</span></div><div class="metric"><span class="label">实时上传</span><span class="value">{{metrics.server.up_rate}}</span></div><div class="metric"><span class="label">累计接收/发送</span><span class="value">{{metrics.server.received}}</span><span class="small muted">发送 {{metrics.server.sent}}</span></div><div class="metric"><span class="label">运行时间</span><span class="value">{{metrics.server.uptime}}</span><span class="small muted">Swap {{metrics.server.swap}}</span></div>
</div><div class="hardware small muted">{{metrics.server.cpu_model}} · {{metrics.server.cpu_cores}} vCPU · 数据更新于 {{metrics.updated}}</div>{% else %}<div class="empty">正在采集服务器数据，请稍后刷新</div>{% endif %}</section>

<section class="panel wide"><h2>线路与连接统计</h2><div class="table-wrap"><table><thead><tr><th>线路</th><th>转发目标</th><th>延迟</th><th>当前连接</th><th>来源 IP</th><th>今日接入</th><th>今日断开</th><th>今日峰值</th></tr></thead><tbody>{% for line in metrics.lines or [] %}<tr><td><strong>{{line.name}}</strong> :{{line.port}}</td><td>{{line.target}}</td><td class="{{'good' if line.latency is not none else 'bad'}}">{{line.latency ~ ' ms' if line.latency is not none else '不可达'}}</td><td>{{line.current}}</td><td>{{line.unique_ips}}</td><td>{{line.connects}}</td><td>{{line.disconnects}}</td><td>{{line.peak}}</td></tr>{% endfor %}</tbody></table></div><p class="small muted">“今日接入/断开”由本面板每 10 秒观察一次，从本次升级运行后开始累计。</p></section>

<section class="panel wide"><h2>当前矿机 TCP 连接</h2>{% if metrics.connections %}<div class="table-wrap"><table><thead><tr><th>线路</th><th>来源 IP</th><th>源端口</th><th>观察时长</th><th>接收队列</th><th>发送队列</th></tr></thead><tbody>{% for connection in metrics.connections %}<tr><td>{{connection.line}} :{{connection.port}}</td><td>{{connection.ip}}</td><td>{{connection.source_port}}</td><td>{{connection.duration}}</td><td>{{connection.recv_q}} B</td><td>{{connection.send_q}} B</td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="empty">当前没有观察到矿机连接</div>{% endif %}<p class="small muted">流量卡可能经过运营商 NAT，来源 IP 不能稳定代表单台矿机。观察时长会在面板服务重启后重新计算。</p></section>

{% for pool in inspector.pools or [] %}<section class="panel wide"><h2>{{pool.name}}</h2><div class="table-wrap"><table><thead><tr><th>用途</th><th>区域</th><th>矿机填写地址</th><th>上游目标</th><th>上游状态</th><th>当前连接</th></tr></thead><tbody>{% for route in pool.routes %}<tr><td><strong>{{route.role}}</strong></td><td>{{route.region}}</td><td><VPS_PUBLIC_IP>:{{route.listen_port}}</td><td>{{route.upstream_host}}:{{route.upstream_port}}</td><td class="{{'good' if route.upstream_ok else 'bad'}}">{{route.upstream_latency_ms ~ ' ms' if route.upstream_ok else '不可达'}}</td><td>{{route.connections}}</td></tr>{% endfor %}</tbody></table></div>{% if not pool.external %}<div class="metrics hardware"><div class="metric"><span class="label">当前连接</span><span class="value">{{pool.summary.connections}}</span></div><div class="metric"><span class="label">活跃 Worker</span><span class="value">{{pool.summary.workers}}</span></div><div class="metric"><span class="label">接受 Share</span><span class="value">{{pool.summary.accepted}}</span></div><div class="metric"><span class="label">拒绝 Share</span><span class="value">{{pool.summary.rejected}}</span></div></div>
{% if pool.workers %}<div class="table-wrap hardware"><table><thead><tr><th>Worker</th><th>使用线路</th><th>状态</th><th>矿机软件</th><th>来源 IP</th><th>难度</th><th>提交/接受/拒绝</th><th>拒绝率</th><th>估算算力</th><th>响应</th><th>最近 Share</th></tr></thead><tbody>{% for worker in pool.workers %}<tr><td><strong>{{worker.name}}</strong></td><td>{{worker.route}} :{{worker.listen_port}}</td><td class="{{'good' if worker.active else 'muted'}}">{{worker.active ~ ' 个连接' if worker.active else '离线'}}</td><td>{{worker.agent}}</td><td>{{worker.sources|join(', ')}}</td><td>{{worker.difficulty}}</td><td>{{worker.submitted}} / {{worker.accepted}} / {{worker.rejected}}</td><td class="{{'bad' if worker.reject_percent > 1 else 'good'}}">{{worker.reject_percent}}%</td><td>{{worker.hashrate}}</td><td>{{worker.latency_ms}} ms</td><td>{{worker.last_share}}</td></tr>{% if worker.last_error %}<tr><td colspan="11" class="small bad">最近拒绝原因：{{worker.last_error}}</td></tr>{% endif %}{% endfor %}</tbody></table></div>{% else %}<div class="empty">三条线路已就绪，等待矿机连接并提交 Share</div>{% endif %}{% else %}<p class="small muted hardware">该矿池沿用原 HAProxy 端口，Worker 与 Share 仍由上方 TCP 连接统计展示；端口和转发映射未修改。</p>{% endif %}</section>{% else %}<section class="panel wide"><h2>矿池模块</h2><div class="empty">多矿池采集器尚未安装或正在启动</div></section>{% endfor %}<p class="wide small muted">所有地址均通过香港 VPS 的 20 轮 TCP 与 Stratum 双重验证。估算算力基于最近 30 分钟接受的 Share。数据更新于 {{inspector.updated or '等待采集'}}。</p>

<section class="panel"><h2>报警设置</h2><form method="post" action="{{url_for('save')}}"><input type="hidden" name="csrf" value="{{csrf}}"><div class="fields"><div><label for="min_connections">最低连接数</label><input id="min_connections" name="min_connections" type="number" min="0" max="100000" required value="{{settings.MIN_CONNECTIONS}}"></div><div><label for="alert_interval">同类报警间隔（分钟）</label><input id="alert_interval" name="alert_interval" type="number" min="1" max="1440" required value="{{interval_minutes}}"></div><div><label for="disk_threshold">磁盘报警阈值（%）</label><input id="disk_threshold" name="disk_threshold" type="number" min="1" max="99" required value="{{settings.DISK_THRESHOLD}}"></div><div><label for="mem_threshold">内存报警阈值（%）</label><input id="mem_threshold" name="mem_threshold" type="number" min="1" max="99" required value="{{settings.MEM_THRESHOLD}}"></div></div><div class="actions"><button type="submit">保存设置</button></div></form></section>
<section class="panel"><h2>监控控制</h2><p class="muted">当前：{{'每分钟检查一次' if enabled else '已暂停'}}</p><div class="actions"><form method="post" action="{{url_for('toggle')}}"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="enabled" value="{{0 if enabled else 1}}"><button class="{{'danger' if enabled else ''}}" type="submit">{{'暂停监控' if enabled else '恢复监控'}}</button></form><form method="post" action="{{url_for('test_wechat')}}"><input type="hidden" name="csrf" value="{{csrf}}"><button class="secondary" type="submit">发送测试通知</button></form></div></section>
<section class="panel wide"><h2>最近日志</h2><pre>{{log}}</pre></section></div></main></body></html>
"""

LOGIN_PAGE = r"""
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录</title><style>
body{margin:0;background:#eef2f4;color:#17242d;font:15px system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;place-items:center;min-height:100vh;letter-spacing:0}.box{width:min(380px,calc(100% - 30px));background:#fff;border:1px solid #dbe2e6;border-radius:8px;padding:25px}h1{font-size:20px;margin:0 0 18px}label{display:block;color:#65727d;font-size:13px;margin-bottom:6px}input{width:100%;box-sizing:border-box;padding:11px;border:1px solid #bac5cc;border-radius:5px;font:inherit}button{width:100%;margin-top:14px;border:0;border-radius:5px;padding:11px;background:#146ef5;color:#fff;font-weight:600}.err{color:#b42e2e;margin-bottom:12px}
</style></head><body><form class="box" method="post"><h1>中转监控登录</h1>{% if error %}<div class="err">{{error}}</div>{% endif %}<label for="password">管理密码</label><input id="password" name="password" type="password" autocomplete="current-password" autofocus required><button>登录</button></form></body></html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        expected = os.environ["PANEL_PASSWORD_HASH"]
        if check_password_hash(expected, request.form.get("password", "")):
            session.clear()
            session["authenticated"] = True
            session["csrf"] = secrets.token_urlsafe(24)
            return redirect(url_for("index"))
        error = "密码不正确"
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not require_login():
        return redirect(url_for("login"))
    settings = read_env()
    defaults = {"MIN_CONNECTIONS": "1", "ALERT_INTERVAL": "900", "DISK_THRESHOLD": "85", "MEM_THRESHOLD": "85"}
    settings = {**defaults, **settings}
    local_ports = [(name, port, tcp_check("127.0.0.1", port)) for name, port in PORTS]
    metrics_data = collector.get()
    inspector_data = inspector_state()
    line_connections = {line.get("port"): line.get("current", 0) for line in metrics_data.get("lines", [])}
    for pool in inspector_data.get("pools", []):
        if pool.get("external"):
            for route in pool.get("routes", []):
                route["connections"] = line_connections.get(route.get("listen_port"), 0)
            pool["summary"]["connections"] = sum(route["connections"] for route in pool.get("routes", []))
    return render_template_string(
        PAGE,
        settings=settings,
        interval_minutes=max(1, int(settings["ALERT_INTERVAL"]) // 60),
        enabled=monitor_enabled(),
        haproxy=service_active(),
        local_ports=local_ports,
        metrics=metrics_data,
        inspector=inspector_data,
        log=recent_log(),
        csrf=session["csrf"],
    )


@app.route("/save", methods=["POST"])
def save():
    if not require_login() or not csrf_ok():
        return "Forbidden", 403
    try:
        minimum = int(request.form["min_connections"])
        minutes = int(request.form["alert_interval"])
        disk = int(request.form["disk_threshold"])
        memory = int(request.form["mem_threshold"])
        if not (0 <= minimum <= 100000 and 1 <= minutes <= 1440 and 1 <= disk <= 99 and 1 <= memory <= 99):
            raise ValueError
    except (KeyError, ValueError):
        flash("参数不合法，设置未保存")
        return redirect(url_for("index"))
    write_env({
        "MIN_CONNECTIONS": str(minimum),
        "ALERT_INTERVAL": str(minutes * 60),
        "DISK_THRESHOLD": str(disk),
        "MEM_THRESHOLD": str(memory),
    })
    flash("报警设置已保存，下次检查立即生效")
    return redirect(url_for("index"))


@app.route("/toggle", methods=["POST"])
def toggle():
    if not require_login() or not csrf_ok():
        return "Forbidden", 403
    enabled = request.form.get("enabled") == "1"
    set_monitor_enabled(enabled)
    flash("监控已恢复" if enabled else "监控已暂停")
    return redirect(url_for("index"))


@app.route("/test-wechat", methods=["POST"])
def test_wechat():
    if not require_login() or not csrf_ok():
        return "Forbidden", 403
    webhook = read_env().get("WECHAT_WEBHOOK", "")
    if not webhook.startswith("https://"):
        flash("未找到有效的企业微信 Webhook")
        return redirect(url_for("index"))
    payload = json.dumps({"msgtype": "text", "text": {"content": f"Hash-Hut 中转监控测试\n时间：{datetime.now():%F %T}"}}).encode()
    try:
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read())
        flash("测试通知发送成功" if result.get("errcode") == 0 else "企业微信返回错误")
    except Exception:
        flash("测试通知发送失败，请检查网络或 Webhook")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8789)
