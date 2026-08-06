# Stratum V3 中转管理面板

这是用于 VPS 上的 Stratum 中转、端口转发、矿机连接观察和报警管理的一套 V3 面板。

核心目标：

- HAProxy 负责公网端口监听和上游矿池转发；
- `stratum-inspector-v3` 透明转发 Stratum 流量，同时观察 Worker、Job、Share；
- 管理面板用于查看矿机状态、服务器资源、端口监听、当前转发地址、报警、日志和配置；
- 企业微信机器人用于发送异常告警；
- 代码可以放入 GitHub 私有仓库，半年后或更换 VPS 后可重新一键部署。

## 端口规划

| 用途 | 公网端口 |
| --- | --- |
| 自用端口组 | `9999`, `10001`, `10002` |
| 备用端口组 1 | `10010`, `10011`, `10012` |
| 备用端口组 2 | `10020`, `10021`, `10022` |
| 备用端口组 3 | `10030`, `10031`, `10032` |
| LitecoinPool 固定端口 | `11001-11003` |
| ViaBTC 固定端口 | `11101-11103` |
| F2Pool 固定端口 | `11201-11203` |
| LongPool 固定端口 | `11301-11303` |

`10000` 和 `10003` 保留，不生成监听。旧版 `120xx` 端口不属于 V3 配置。

## 管理面板重点功能

登录后首页会优先显示：

- 当前连接数；
- 在线、最近断开、离线、失效矿工数量；
- Share 提交、接受、拒绝率；
- 服务器负载、内存、磁盘、运行时间；
- 核心服务状态；
- 各端口是否监听；
- 当前端口转发到哪个上游矿池；
- 矿机可填写的 `stratum+tcp://VPS公网IP:端口` 地址，并支持一键复制。

注意：复制给矿机的转发地址必须使用 VPS 当前公网 IPv4。系统会从 VPS 默认出公网路由自动识别公网 IP，不会使用 Tailscale 域名或管理面板访问域名兜底。如果无法识别公网 IP，首页会显示“当前IP无法获取”，复制按钮禁用，并通过企业微信发告警。

## 地址稳定性探测

矿池可能会屏蔽频繁的独立 `mining.subscribe` 探测，所以自动探测是可配置的：

- 可在“设置 -> 地址稳定性”关闭自动探测；
- 也可以设置成 240 分钟、360 分钟等低频探测；
- 小时/每日批量采样单独控制，矿池开始限流时建议关闭；
- 手动“立即检测”按钮仍然保留。

## 新 VPS 一键部署

推荐把 `monitor-panel/` 上传到 GitHub 私有仓库。更换 VPS 后，在新 VPS 上 clone 仓库，然后执行：

```bash
cd /root
git clone git@github.com:<你的GitHub用户名>/<你的私有仓库>.git stratum-v3
cd /root/stratum-v3/monitor-panel
chmod +x bootstrap-vps.sh
./bootstrap-vps.sh
```

`bootstrap-vps.sh` 会完成：

1. 安装 HAProxy、Python Flask 等依赖；
2. 创建管理面板密码配置；
3. 写入企业微信机器人 Webhook；
4. 安装 V3 中转、检查器、报警和管理面板服务；
5. 启用 systemd 自启动；
6. 初始化受保护文件基线。

## 重新部署时如何设置密码和企业微信

执行 `./bootstrap-vps.sh` 时会提示：

```text
Set admin panel password, at least 12 characters:
Enterprise WeChat webhook URL, leave empty to skip:
```

### 管理面板密码

你输入的新密码不会明文保存。脚本会生成密码哈希和 session secret，写入：

```bash
/etc/stratum-admin.env
```

这个文件只保存在 VPS 本机，不应提交到 GitHub。

### 企业微信机器人 Webhook

部署时粘贴企业微信机器人的 Webhook 地址即可。脚本会写入：

```bash
/etc/stratum-v3.env
```

如果部署时先跳过，后续也可以手动补：

```bash
nano /etc/stratum-v3.env
```

加入或修改：

```bash
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
```

然后重启会发送通知的服务：

```bash
systemctl restart stratum-endpoint-monitor stratum-security-monitor stratum-admin
```

不要把下面内容提交到 GitHub：

- `/etc/stratum-admin.env`
- `/etc/stratum-v3.env`
- 面板密码哈希
- 企业微信 Webhook
- SSH 私钥
- VPS 备份包
- 服务日志

## 新 VPS 如何进入控制面板

管理面板默认只监听 VPS 本机：

```text
127.0.0.1:8789
```

默认不直接暴露到公网。

### 方式一：SSH 隧道，适合电脑临时管理

在 Windows PowerShell 执行：

```powershell
ssh -N -L 8789:127.0.0.1:8789 -p <SSH端口> root@<新VPS公网IP>
```

然后浏览器打开：

```text
http://127.0.0.1:8789
```

### 方式二：Tailscale，适合手机随时访问

新 VPS 登录 Tailscale 后执行：

```bash
tailscale up
tailscale serve --bg http://127.0.0.1:8789
tailscale serve status
```

`tailscale serve status` 会显示一个类似：

```text
https://xxxxx.ts.net/
```

手机安装 Tailscale 并登录同一个账号后，打开这个地址即可进入面板。

## GitHub 私有仓库建议

建议只提交 `monitor-panel/` 目录，不要提交外层目录里的压缩包、备份、迁移包、本地测试页面等文件。

首次上传：

```powershell
cd C:\Users\小米\Documents\miner

git add monitor-panel
git commit -m "Initial Stratum V3 relay panel deployment"
git branch -M main
git remote add origin git@github.com:<你的GitHub用户名>/<你的私有仓库>.git
git push -u origin main
```

以后更新代码：

```powershell
cd C:\Users\小米\Documents\miner
git add monitor-panel
git commit -m "Update Stratum V3 relay panel"
git push
```

## 升级已有 V3 服务器

如果 VPS 已经部署过 V3，只想升级代码，不想全新安装，可以上传最新文件后执行：

```bash
cd /root/stratum-v3
chmod +x upgrade-v3-panel.sh
./upgrade-v3-panel.sh
```

如果只是升级探测设置、首页关键看板、公网 IP 检测等轻量更新，可以执行：

```bash
cd /root/stratum-v3
chmod +x upgrade-probe-settings.sh
./upgrade-probe-settings.sh
```

升级脚本会备份旧文件，并刷新安全监控基线。

## 回滚

安装和升级时会生成备份路径。完整 V3 回滚：

```bash
/root/stratum-v3/rollback-v3.sh
```

也可以根据升级脚本输出的 backup 路径手动恢复对应文件。

## 部署后检查

检查服务状态：

```bash
systemctl status haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-security-monitor stratum-admin --no-pager
```

检查端口监听：

```bash
ss -lnt | grep -E ':(9999|10001|10002|10010|10011|10012|11001|11101|11201|11301)\b'
```

查看报警/探测日志：

```bash
journalctl -u stratum-endpoint-monitor -n 100 --no-pager
tail -n 100 /var/lib/stratum-monitor/endpoint-events.jsonl
```

查看配置审计：

```bash
tail -n 50 /var/log/stratum-audit.jsonl
```

## 本地测试

在 `monitor-panel/` 目录运行：

```powershell
python -m unittest discover -v
```

如果本机没有系统 Python，可以使用 Codex 自带 Python 或在 VPS 上运行测试。

## 识别边界

服务器只能检查经过它转发的矿机流量。如果矿机固件绕过中转服务器、直接连接外部矿池，这套系统无法直接看到。要防止绕过，需要在矿场路由器或防火墙上限制矿机只能访问指定 VPS 中转地址和必要管理服务。
