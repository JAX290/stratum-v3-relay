# Stratum V3 中转管理面板

> 功能概览和一键安装见[项目首页](../README.md)。完整配置步骤见[部署与维护指南](../docs/deployment-guide.md)，日常管理见[管理员操作指南](../docs/administrator-guide.md)。本页补充管理面板的功能和维护细节。

这是用于 VPS 上的 Stratum 中转、端口转发、矿机连接观察和报警管理的一套 V3 面板。

核心目标：

- HAProxy 负责公网端口监听和上游矿池转发；
- `stratum-inspector-v3` 透明转发 Stratum 流量，同时观察 Worker、Job、Share；
- 管理面板用于查看矿机状态、服务器资源、端口监听、当前转发地址、报警、日志和配置；
- 企业微信、钉钉或邮件用于发送异常告警；
- 代码放在 GitHub 公开仓库中，更换 VPS 后可重新一键部署。

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

设置页提供企业微信、钉钉和邮件通知配置，每种方式可设置 0–3 个接收目标。邮件既可使用邮箱服务商 SMTP，也可由 VPS 直接投递；直投只需填写收件邮箱。敏感值保存后不回显，可分别测试并逐项删除；配置只允许通过 Tailscale 修改。矿池、线路测试和安全事件会发送到全部已配置目标，发送失败不会中断转发。

矿机页优先显示当前有连接或活跃 Worker 的矿池，并显示正在使用的端口；没有连接的矿池默认折叠，仍可展开查看历史 Worker 和 Share。

### 管理员总览

首页首先显示“当前是否正常生产、预计影响多少台矿机、管理员现在应该做什么”。同一客户端密钥作为一个矿场分组，同一个局域网 IP 无论建立多少条连接都只计算为一台矿机。新版 Windows 客户端会把版本号随加密心跳上报，VPS 首页可直接核对各矿场客户端和加密入口的版本。

“故障与处理时间线”把矿场离线与恢复、单机测试、线路切换、双 VPS 同步、安全异常和到期提醒放在一起。原始日志仍然保留，首页只显示管理员需要理解的事件、影响范围和处理结果。

在“设置 → 管理员到期提醒”填写 VPS 和域名的购买到期日期。TLS 证书日期由 VPS 自动读取。进入提醒天数后，首页会变黄或红，并按每天最多一次写入事件记录；已经配置企业微信时会同时发送通知。

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

推荐从仓库根目录使用统一部署脚本。它会继续安装 TLS 加密入口、可选 Tailscale、公网只读值守面板，并完成服务验收：

```bash
cd /root/stratum-v3
chmod +x deploy.sh
./deploy.sh
```

下面的 `bootstrap-vps.sh` 适合只安装 V3 面板组件或进行单独排障：

更换 VPS 后，在新 VPS 上 clone 公开仓库，然后执行：

```bash
cd /root
git clone https://github.com/JAX290/stratum-v3-relay.git stratum-v3
cd /root/stratum-v3/monitor-panel
chmod +x bootstrap-vps.sh
./bootstrap-vps.sh
```

`bootstrap-vps.sh` 会完成：

1. 安装 HAProxy、Python Flask 等依赖；
2. 创建管理面板身份验证配置，应急密码可以留空；
3. 写入企业微信机器人 Webhook；
4. 安装 V3 中转、检查器、报警和管理面板服务；
5. 启用 systemd 自启动；
6. 安装每分钟运行一次的 VPS 本机健康看门狗；
7. 初始化受保护文件基线。

这个脚本只安装 V3 转发和管理面板。需要 Windows 值守电脑通过 TLS 加密接入时，部署完成后还要继续执行根目录说明中的“安装 TLS 加密入口”。

## 重新部署时如何设置密码和企业微信

执行 `./bootstrap-vps.sh` 时会提示：

```text
Set emergency panel password (at least 12 characters), or leave empty for Tailscale-only access:
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

通过 Tailscale Serve 进入时，面板会使用 Tailscale 已验证的用户身份自动登录，不再询问面板密码。SSH 隧道访问仍可使用应急密码。如果只允许指定的 Tailscale 账号免密码登录，可在 `/etc/stratum-admin.env` 增加：

```text
TAILSCALE_ALLOWED_USERS=你的Tailscale登录邮箱
```

“日志”页会以“运维问题中心”的形式显示 VPS 问题。页面同时检查 HAProxy、协议检查器、矿池监控、线路切换、安全监控、加密入口、矿场在线监控、管理面板和自动恢复服务，并把最近 24 小时的技术日志转换成中文原因和处理建议。同类信息会合并，已经恢复的问题会自动从待处理清单中消失；原始日志仍可展开查看。

### 方式三：HTTPS 域名，只看日常状态

V3 会同时启动独立的登录只读服务 `stratum-public-status`，它只监听 `127.0.0.1:8790`。登录后可查看矿机 IP、Worker、Share、上游地址、端口、线路和故障信息，但不能修改配置，也不会读取或显示共享密钥、密码、Webhook 和证书私钥。为它配置 HTTPS 域名：

```bash
cd /root/stratum-v3/monitor-panel
chmod +x install-public-status.sh
./install-public-status.sh
```

脚本使用中文向导设置独立值守账号和至少 12 个字符的密码、询问只读面板域名，并自动检查 DNS、配置 Nginx、申请证书、启用 Certbot 自动续期和验证页面。登录连续失败 5 次会冷却 15 分钟，会话 Cookie 只允许 HTTPS 使用。状态面板域名可以直接使用 Cloudflare“已代理（橙色云朵）”；域名返回 Cloudflare 代理 IP 时，向导会请管理员核对记录中的 VPS IP 后继续。安全组需放行 TCP `80`、`443`。以前使用的 `--email` 只是 Certbot 的旧版证书账户联系邮箱，不是面板账号；Let's Encrypt 已在 2025 年停止到期提醒邮件，因此新版向导不要求邮箱。`8789` 和 `8790` 都不应在安全组中直接放行。更换值守账号或密码时重新运行脚本并增加 `--reset-login`。

### 找回木林森中转的客户端填写资料

只有通过 Tailscale Serve 打开完整管理面板后，“设置”页才会出现“客户端接入资料”入口。里面可以查看：

- VPS 地址和 TLS 端口；
- 证书名称及 SHA-256 指纹；
- 每个客户端自己的共享密钥，以及最近来源 IP、最后连接时间和当前连接数。

共享密钥默认只显示末四位。点击“查看 60 秒”或“复制密钥”时，面板会把操作人、时间和客户端编号写入“日志 → 配置审计”，审计内容不包含密钥。敏感页面禁止浏览器缓存。当前请求没有经过 VPS 本机的 Tailscale Serve 时，即使已经用应急密码登录，也会显示页面不存在。

证书私钥不会出现在页面或接口中，也不能通过面板下载。私钥只由 VPS 上的加密入口服务使用，Windows 客户端不需要它。未来增加的公网只读矿机页面也不得连接或复用这组敏感接口。

多个账号用英文逗号分隔，修改后执行 `systemctl restart stratum-admin`。

忘记应急密码时，在 VPS 中执行：

```bash
/opt/stratum-admin/reset-panel-password.sh
```

根据提示输入两次新密码即可。若确认以后只通过 Tailscale 管理，也可以关闭密码登录：

```bash
/opt/stratum-admin/reset-panel-password.sh --disable-password
```

应急密码在 5 分钟内连续输错 5 次后会暂停 15 分钟。通过 Tailscale 的已验证身份自动登录不受影响。HTTPS 会话使用 Secure Cookie，所有页面统一禁止外部嵌入并带有浏览器安全响应头；`PANEL_SECRET_KEY` 缺失时管理服务会直接拒绝启动并在日志中说明修复位置。

## GitHub 仓库维护建议

建议只提交 `README.md` 和 `monitor-panel/` 目录，不要提交外层目录里的压缩包、备份、迁移包、本地测试页面等文件。

首次上传：

```powershell
cd C:\Users\小米\Documents\miner

git add monitor-panel
git commit -m "Initial Stratum V3 relay panel deployment"
git branch -M main
git remote add origin https://github.com/JAX290/stratum-v3-relay.git
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

日常升级推荐执行：

```bash
cd /root/stratum-v3
./deploy.sh
```

统一脚本会先更新代码，再调用下方的组件升级程序并执行安装后验收。需要人工控制组件时再使用以下步骤。

如果 VPS 已经部署过 V3，拉取最新代码后执行：

```bash
cd /root/stratum-v3
git fetch origin
git checkout main
git pull --ff-only origin main
cd /root/stratum-v3/monitor-panel
chmod +x upgrade-v3-panel.sh
./upgrade-v3-panel.sh
```

统一使用完整升级脚本，避免只更新部分文件造成面板、检查器和服务配置版本不一致。升级脚本会备份旧文件，并刷新安全监控基线。

### 在面板安全切换矿池

升级后的“总览”会把当前使用中的端口和上游矿池放在显眼位置。点击某条线路右侧的“修改”后，按这个顺序操作：

1. 选择地址库中的目标矿池，或填写新矿池的域名、端口、算法和币种。
2. 可选填写矿池测试账号。账号和密码只用于这一次登录测试，不会写入配置或日志。
3. 点击“只检测，不切换”，确认 TCP、Stratum 响应和账号登录都正常。
4. 选择一台正在使用该端口的在线矿机，点击“开始10分钟自动测试”。系统只让这台矿机改道，其他矿机保持原线路。
5. 10分钟后，后台自动检查 Share。至少有 1 个接受份额且拒绝率不高于 5% 时，系统自动全量切换，并把地址加入“已验证地址库”。
6. 没有接受份额或拒绝率高于 5% 时，测试矿机自动返回原线路。所有结果都会写入日志并显示在总览首页；配置企业微信后还会同时通知。
7. 以后选择“已验证地址库”中的地址，可以点击“已验证地址直接切换”，不再重复10分钟测试。
8. 如果全量切换后效果不好，可在总览“最近10次线路改动”中选择任意一条记录恢复。

面板校验的是矿池地址中已登记的算法资料，Stratum V1 本身通常不会可靠报告算法。因此，新建自定义矿池时必须正确选择算法。即使算法相同，也建议使用真实矿池账号检测，并完成单机试切后再全量切换。

10分钟自动测试由独立后台服务执行，管理页面可以关闭。测试期间也可以点击“提前停止并返回”。

### 两台 VPS 双向同步

两台 VPS 安装完全相同的版本，不固定谁是“主”、谁是“备用”。先让两台 VPS 都登录同一个 Tailscale 网络，并分别执行 `tailscale serve --bg http://127.0.0.1:8789`。然后在两台面板的“设置 -> VPS 双向同步”中：

1. 都选择“开启”；
2. 填写完全相同的共享同步密钥，至少32个字符；首次可在一台留空保存，让系统生成后复制到另一台；
3. VPS A 填写 VPS B 的 `https://...ts.net` 面板地址，VPS B 填写 VPS A 的地址；
4. 两台都保存后，只在当前配置正确的 VPS 上点击一次“同步本机全部线路”，完成初始对齐；
5. 此后从任意一台修改、自动切换或恢复线路，都会同步到另一台；对端暂时离线时任务会保留并自动重试。

同步密钥只用于两台 VPS 之间验证身份，不要提交到 GitHub。同步结果无论成功或失败都会写入日志并在总览首页显示。

这次升级会同步更新管理面板、协议检查器和加密入口。执行升级脚本时，矿机会短暂断开一次并自动重连；之后日常单机试切只影响选中的矿机，全量切换只影响选中的端口。

## 回滚

安装和升级时会生成备份路径。完整 V3 回滚：

```bash
cd /root/stratum-v3/monitor-panel
./rollback-v3.sh
```

也可以根据升级脚本输出的 backup 路径手动恢复对应文件。

## 部署后检查

检查服务状态：

```bash
systemctl status haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-route-switch-monitor stratum-security-monitor stratum-admin --no-pager
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

### VPS 内存持续增长

新版检查器会保留累计 Worker 和 Share 统计，但算力估算使用的已接受 Share 按 10 秒汇总，每个 Worker 只保留固定数量的时间桶，不再为每个 Share 长期创建一个内存对象。每个 Worker 最多保留 256 个历史来源 IP 和最近 20 条断线连接明细；未收到矿池响应的 Share 请求也有数量和时间上限。全部断线事件另外按天写入 `/var/lib/stratum-inspector/history/`，最多保留 7 天，可在面板“日志 -> 最近7天断线明细”下载。单个文件达到 32 MB 时自动分段，总容量最多 256 MB，防止异常重连写满磁盘。配置审计和事件文件达到 8 MB 后会自动保留最近 5000 条，配置文件历史最多保留 50 份。

查看各服务当前内存：

```bash
systemctl show haproxy stratum-inspector-v3 stratum-secure-relay stratum-admin \
  -p Id -p MainPID -p MemoryCurrent -p MemoryPeak
ps -eo pid,comm,rss,%mem,etime --sort=-rss | head -n 15
du -h /var/lib/stratum-inspector/state.json /var/lib/stratum-monitor/*.json* 2>/dev/null
```

如果 `stratum-inspector-v3` 持续增长，升级完整仓库后执行 `monitor-panel/upgrade-v3-panel.sh`。脚本会重启检查器并立即释放旧历史内存，矿机会短暂重连。

### 无人值守时的自动恢复

所有核心服务都设置为开机启动并持续自动重启，且不再因短时间连续失败而永久进入停止状态。加密入口和协议检查器的文件句柄上限提高到 65536，可承受上千条 TCP 连接所需的双向套接字。后台状态任务如果意外退出，会让主进程主动退出，随后由 systemd 重启，避免出现服务显示 `active`、状态却不再更新的假运行状态。

`stratum-vps-watchdog.timer` 每分钟执行本机检查。单次失败只记录，连续三次失败才重启对应模块；矿池本身不可达不会触发整个 VPS 重启。查看记录：

```bash
systemctl status stratum-vps-watchdog.timer
cat /var/lib/stratum-monitor/vps-watchdog.json
journalctl -u stratum-vps-watchdog.service -n 50 --no-pager
```

## 本地测试

在 `monitor-panel/` 目录运行：

```powershell
python -m unittest discover -v
```

如果本机没有系统 Python，可以使用 Codex 自带 Python 或在 VPS 上运行测试。

## 识别边界

服务器只能检查经过它转发的矿机流量。如果矿机固件绕过中转服务器、直接连接外部矿池，这套系统无法直接看到。要防止绕过，需要在矿场路由器或防火墙上限制矿机只能访问指定 VPS 中转地址和必要管理服务。
