# 部署与维护指南

本指南用于手动部署、组件维护和故障排查。首次安装或整套升级推荐使用项目首页的[一键部署与升级](../README.md#快速部署与升级)。示例域名和地址需要替换为实际配置。

## 一、开始前准备

准备以下内容：

| 项目 | 说明 |
| --- | --- |
| VPS | Ubuntu，拥有 `root` 权限；推荐独立公网 IPv4 |
| 开放端口 | SSH 端口、TLS 入口端口；本文示例使用 TCP `452` |
| 管理设备 | Windows 电脑或手机，安装 Tailscale |
| 值守电脑 | Windows 10/11，和矿机位于同一局域网，长期运行 |
| GitHub 仓库 | `https://github.com/JAX290/stratum-v3-relay.git` |
| 企业微信 Webhook | 可选；不配置也会在面板首页和日志中记录事件 |
| 域名 | 可选但推荐；用于集中管理 VPS 地址，示例为 `example.com` |

建议为两台 VPS 分别准备一张记录表。安装完成后把这些信息记下来：

| 信息 | VPS A | VPS B |
| --- | --- | --- |
| 公网 IP |  |  |
| SSH 端口 |  |  |
| Tailscale 面板地址 |  |  |
| TLS 端口 | `452` | `452` |
| 证书 SHA-256 |  |  |
| Windows 客户端共享密钥 |  |  |

证书指纹和共享密钥不是同一个东西。每台 VPS 会生成自己的证书和密钥，Windows 客户端的主、备 VPS 页面分别填写对应信息即可。

## 二、登录新 VPS

在 Windows PowerShell 中执行：

```powershell
ssh -p <SSH端口> root@<VPS公网IP>
```

例如 SSH 端口是 `34326`：

```powershell
ssh -p 34326 root@203.0.113.10
```

第一次登录会询问是否信任主机指纹，确认 IP 是自己的 VPS 后输入 `yes`。SSH 不能写成 `root@IP:端口`。

## 三、下载代码

以下命令在 VPS 中执行：

```bash
apt-get update
apt-get install -y git curl ca-certificates
cd /root
git clone https://github.com/JAX290/stratum-v3-relay.git stratum-v3
cd /root/stratum-v3
```

如果提示 `stratum-v3 already exists`，说明目录已经存在。不要重复克隆，先按后文“升级已有 VPS”处理。

确认目录完整：

```bash
ls -l /root/stratum-v3/monitor-panel/bootstrap-vps.sh
ls -l /root/stratum-v3/secure-relay/server/install-secure-relay.sh
```

两条命令都应显示文件信息。

## 四、安装 V3 中转和管理面板

```bash
cd /root/stratum-v3/monitor-panel
chmod +x bootstrap-vps.sh
./bootstrap-vps.sh
```

脚本会安装 HAProxy、Stratum 检查器、线路切换服务、安全监控和管理面板，并设置开机自启。

安装时会遇到两个输入项：

1. 应急管理密码：只在 **Tailscale 暂时不可用，但仍能 SSH 登录 VPS** 时使用。管理员先建立 SSH 本地隧道，再用此密码进入完整面板；它不是只读面板密码、VPS 密码、矿池密码或客户端共享密钥。只准备通过 Tailscale 日常管理时直接回车，表示关闭这个备用密码入口。详细场景和操作见[管理员完整使用说明](administrator-guide.md#应急管理密码)。
2. 企业微信 Webhook：企业微信机器人地址，不需要时直接回车。

这里安装的是 V3 转发和管理面板，加密入口还需要完成第六步。

安装后检查：

```bash
systemctl is-active haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-route-switch-monitor stratum-security-monitor stratum-admin stratum-public-status
systemctl is-active stratum-vps-watchdog.timer
```

正常时每一行都应显示 `active`。`stratum-vps-watchdog.timer` 是 VPS 本机看门狗：每分钟检查一次核心服务；进程崩溃仍由 systemd 在几秒内拉起，进程存在但加密入口、矿机状态采集或面板连续三次无响应时，看门狗只重启对应服务。每个服务重启后有 5 分钟冷却，避免故障循环。

## 五、通过 Tailscale 打开管理面板

### 5.1 在 VPS 安装并登录 Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

`tailscale up` 通常会显示一个登录网址。复制到浏览器，在自己的 Tailscale 账号中确认这台 VPS。

如果命令没有显示网址，先运行：

```bash
tailscale status
```

只要列表中能看到这台 VPS，说明已经登录。

### 5.2 发布面板

```bash
tailscale serve --bg http://127.0.0.1:8789
tailscale serve status
```

记录输出中的地址，例如：

```text
https://your-vps-name.xxxxx.ts.net/
```

管理电脑或手机也要安装 Tailscale，并登录同一个账号。连接 Tailscale 后，在浏览器打开上述地址。通过 Tailscale Serve 进入时会验证 Tailscale 身份，可以不设置面板密码。

面板只监听 VPS 本机的 `127.0.0.1:8789`，不需要把 `8789` 暴露到公网。

通过 Tailscale 进入面板后，可以打开“设置 → 客户端接入资料”。这里会显示 Windows 木林森中转需要填写的 VPS 地址、TLS 端口、证书 SHA-256 指纹和各客户端共享密钥。共享密钥默认隐藏，查看或复制都会写入审计日志；证书私钥只留在 VPS，网页不会显示或下载。这个页面要求当前访问本身来自 Tailscale，通过应急密码或 SSH 隧道登录也无法打开。

面板的“日志”页是面向非技术管理员的“运维问题中心”。它会检查当前服务状态和最近 24 小时 VPS 日志，把端口占用、证书、权限、磁盘、配置、网络、超时和认证问题转换成中文处理建议。已经明确恢复的问题不会继续列为待处理；需要技术人员协助时，可以展开并复制原始日志。

### 5.3 Tailscale 暂时不可用时

可以从 Windows 建立 SSH 隧道：

```powershell
ssh -N -L 8789:127.0.0.1:8789 -p <SSH端口> root@<VPS公网IP>
```

保持这个 PowerShell 窗口不要关闭，然后打开 `http://127.0.0.1:8789`。这种方式需要安装时设置的应急管理密码；如果安装时留空，应急密码登录处于关闭状态。不要为了使用应急入口而在安全组放行 `8789`。

### 5.4 不使用 Tailscale 的 HTTPS 只读面板

日常只看运行状态时，可以为每台 VPS 配置一个普通 HTTPS 域名，例如 `status1.example.com`。公网页面和完整管理面板是两个独立服务：

- `127.0.0.1:8790`：登录后的公网只读值守面板，显示矿场、矿机 IP、Worker、Share、矿池地址、端口、线路健康、服务和脱敏故障信息；
- `127.0.0.1:8789`：完整管理面板，仍只通过 Tailscale 或 SSH 隧道进入。

只读页面使用独立的值守账号和密码。登录后可查看日常判断故障需要的运行信息，但没有配置修改接口，也不会显示共享密钥、密码哈希、企业微信 Webhook、证书私钥、配置审计或未经脱敏的原始日志。

#### 安装前先完成三项准备

1. 在 Cloudflare 的“DNS → 记录”中添加 `A` 记录。名称填写 `status1`，IPv4 地址填写这台 VPS 的公网 IP。
2. HTTPS 状态面板可以直接使用 **已代理（橙色云朵）**。脚本发现域名返回 Cloudflare 代理 IP 时，会要求核对记录中的 VPS IP 后继续。
3. 在 VPS 服务商的安全组中放行 TCP `80` 和 `443`。如果 VPS 启用了 UFW，再执行 `ufw allow 80/tcp` 和 `ufw allow 443/tcp`。

不要在安全组中放行 `8789` 或 `8790`。这两个端口只供 VPS 本机、Tailscale 或 SSH 隧道使用。

#### 在 VPS 运行中文安装向导

完成 V3 安装后执行：

```bash
cd /root/stratum-v3/monitor-panel
chmod +x install-public-status.sh
./install-public-status.sh
```

脚本会先要求设置独立的值守账号和至少 12 个字符的登录密码，再询问“只读面板域名”。第一台 VPS 填写 `status1.example.com`，第二台填写 `status2.example.com`。只填写域名，不要带 `https://`、端口或结尾斜杠。脚本会自动：

- 检查域名是否已经指向当前 VPS；
- 安装并配置 Nginx；
- 申请浏览器信任的 Let's Encrypt HTTPS 证书；
- 开启证书自动续期；
- 验证只读页面能否正常响应。

账号和密码只用于这个公网只读面板，不是 Tailscale 管理账号。密码只以不可逆哈希保存在 VPS 的 `/etc/stratum-public-status.env`，网页和日志不会显示原密码。连续输错 5 次会暂停登录 15 分钟。以后需要更换账号或密码时执行：

```bash
./install-public-status.sh --domain status1.example.com --reset-login
```

也可以直接指定域名：

```bash
./install-public-status.sh --domain status1.example.com
```

`--email` 是可选的证书账户联系邮箱，与面板登录账号和运行告警无关。安装向导不要求邮箱，网站证书由 Certbot 定时任务自动续期。

脚本显示“安装成功”后，用浏览器打开 `https://status1.example.com/`，输入刚才设置的值守账号和密码。Cloudflare 可以保持 **已代理（橙色云朵）**，SSL/TLS 模式建议使用“完全（严格）”。橙色代理会让域名返回 Cloudflare IP，这是正常现象；安装向导会要求核对 Cloudflare 记录中的 VPS IP，确认后继续申请证书。

完整 Cloudflare 操作和换 VPS 步骤见[域名与 Cloudflare 新手说明](cloudflare-domain-guide.md)。

### 5.5 矿场与客户端管理

通过 Tailscale 打开完整管理面板，进入“设置 → 客户端接入资料”。在“新增矿场 / 新增客户端”填写名称，点击“新增矿场并生成接入密钥”。系统自动生成独立编号和密钥，随后在对应行复制密钥，连同本页的 VPS 地址、TLS 端口、证书指纹填写到新电脑的木林森中转中。首次连接前，矿场总览显示“等待首次连接”。

已有矿场点击对应名称下方的“编辑矿场名称”，填写新名称并保存。改名不会更换密钥或断开现有连接，运行总览及 HTTPS 值守面板会显示新名称。每台值守电脑建议使用独立密钥；同一矿场多台电脑可以在名称中注明电脑编号。两台 VPS 的客户端配置分别管理，需要分别添加并在客户端填写对应密钥。所有新增和改名操作记入审计日志，日志不记录密钥。公网 HTTPS 只读值守面板不提供这些管理入口。

不再使用的矿场可以在对应行点击“删除矿场”。确认页会显示名称、编号、连接数和最近来源；必须完整输入矿场名称，并勾选确认已停用或迁移，才能删除。确认页有效期为 5 分钟，名称或密钥被修改后需要重新确认。**删除后原密钥不能建立新连接或重连，可能导致整个矿场中转掉线；已有连接可能暂时继续，请先完成停用或迁移。** 删除不会重启其他矿场的中转，也不会删除另一台 VPS 的配置。操作会记录到审计日志，配置保存前会保留一份仅 root 可读的恢复副本；重新新增会生成新密钥，需要重新配置电脑。最后一个矿场也可删除，加密入口会保持运行并等待重新添加矿场。

## 六、安装 TLS 加密入口

本文建议使用 TCP `452`，避免和 Tailscale Serve 使用的 HTTPS 端口混淆：

```bash
cd /root/stratum-v3/secure-relay/server
chmod +x install-secure-relay.sh
./install-secure-relay.sh --port 452
```

脚本结束时会输出三项 Windows 客户端配置：

```text
TLS port: 452
Shared key: 一串64位字符
Certificate SHA-256: 一串64位字符
```

立即把三项内容记录到第一步的表格中。共享密钥不要发到群聊，也不要提交到 GitHub。

在云厂商安全组中放行 TCP `452`。先用 `ufw status` 检查 VPS 防火墙；如果状态为 `active`，再执行：

```bash
ufw allow 452/tcp
```

检查服务和端口：

```bash
systemctl is-active stratum-secure-relay stratum-secure-monitor
ss -lntp | grep ':452'
```

应看到两个 `active`，并看到 `452` 正在监听。

如果 VPS 使用共享公网 IP，需要在服务商控制台建立一个 TCP 公网端口映射到 VPS 内部的 `452`。Windows 客户端填写服务商分配的公网端口；VPS 安装时仍使用内部监听端口 `452`。

## 七、部署第二台备用 VPS

在 VPS B 上完整重复第二至第六步。两台 VPS 安装相同程序，不固定谁只能当主机或备用机。

两台 VPS 的证书指纹和客户端共享密钥可以不同，而且建议不同。Windows 客户端会在每台备用 VPS 的设置页保存该 VPS 自己的 TLS 端口、证书指纹和共享密钥。

只有线路端口规划需要保持一致。例如两台 VPS 的 `11301` 都应代表同一条逻辑线路，客户端发生切换时才不会跑到错误矿池。

## 八、配置两台 VPS 的线路同步

先确认两台 VPS 都能通过各自的 `https://...ts.net/` 地址打开面板。

分别进入两台面板的“设置 → VPS 双向同步”：

1. 两台都开启同步。
2. 在 VPS A 中填写 VPS B 的 Tailscale 面板地址。
3. 在 VPS B 中填写 VPS A 的 Tailscale 面板地址。
4. 两台填写完全相同的“同步密钥”，长度至少 32 个字符。第一次可在一台留空保存，让系统生成，再复制到另一台。
5. 两台都保存。
6. 只在当前线路配置正确的那台 VPS 上，点击一次“立即同步本机全部线路”。
7. 等待页面显示“同步成功”；如果显示失败，页面会说明原因和待重试数量。

此后在任意一台 VPS 修改、试切、全量切换或恢复线路，页面都会立即连接另一台并等待确认，然后明确反馈“同步成功”或失败原因。对端临时离线时会保留任务并在后台重试；最近结果、反馈时间和待重试数量显示在“设置 → VPS 双向同步”。同步成功或失败也会写入日志并显示在首页。

“VPS 同步密钥”只用于两台 VPS 的管理面板互相认证；“Windows 客户端共享密钥”用于值守电脑连接加密入口。两者不能混用。

### 使用域名管理 VPS（推荐）

Windows 客户端可以填写域名代替固定 IP，例如 `relay1.example.com`。以后更换 VPS，只需在 Cloudflare 修改域名指向的新 IP，矿场电脑不需要逐台修改。

中转域名使用自定义 TCP/TLS 端口，Cloudflare 必须设置为“仅 DNS（灰色云朵）”；公开只读面板属于 HTTPS 网站，设置为“已代理（橙色云朵）”。不要把中转域名误设成橙色云朵，否则可能无法连接。

第一次添加记录、更换 VPS、临时域名测试、证书处理和故障排查，请完整阅读：[域名与 Cloudflare 新手说明](cloudflare-domain-guide.md)。

## 九、设置 Windows 值守电脑

下载并运行：

```text
secure-relay/client/木林森中转2.2.1正式版.exe
```

客户端版本可在窗口标题查看。主、备用 VPS 连接测试包含超时保护；日志和 Share 历史有容量限制。普通软件异常会写入本机 `crash.log`，程序退出3秒后自动启动；10分钟内最多自动恢复3次，避免故障循环。勾选“开机自动启动”后，恢复启动时也会自动开始中转。

正式版使用内部代码证书签名。每台矿场电脑第一次部署前，把 `secure-relay/client/src/signing` 文件夹和正式版 EXE 一起下载，以管理员身份运行：

```powershell
powershell -ExecutionPolicy Bypass -File ".\signing\安装木林森内部签名证书.ps1"
```

安装脚本显示成功后，再运行正式版 EXE。公开证书的固定指纹为 `D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB`，只用于让自有电脑信任发布者“木林森中转”，不包含签名私钥。右键 EXE 打开“属性 → 数字签名”，应看到签名有效。没有安装公开证书的电脑仍会把内部自签名显示为未知发布者。

主界面按下面填写：

| 设置项 | 填写内容 |
| --- | --- |
| 矿场名称 | 用于识别这台值守电脑，可自行填写 |
| 主 VPS 地址 | VPS A 的公网 IP 或可用域名 |
| TLS 端口 | 第六步输出的端口，示例为 `452` |
| 证书名称 | 自签名证书时留空；正规域名证书时填证书域名 |
| 证书 SHA-256 | VPS A 安装脚本输出的指纹 |
| 共享密钥 | VPS A 安装脚本输出的 Shared key |
| 本地监听地址 | 保持 `0.0.0.0` |
| 端口或端口映射 | 填实际需要的端口，例如 `11301,11302,11303` |
| 自动探测间隔 | 建议 `5` 分钟 |

端口写法：

- `11301`：矿机连接值守电脑 `11301`，VPS 也按 `11301` 线路转发。
- `12001=11301`：矿机连接值守电脑 `12001`，VPS 按 `11301` 线路转发。
- 多个端口使用英文逗号分隔，本地端口不能重复。

填写主 VPS 后点击“测试主 VPS”。必须同时通过 TCP、TLS 证书和共享密钥验证。

再点击“备用 VPS 设置”，为 VPS B：

1. 勾选启用。
2. 填写 VPS B 的公网地址和 TLS 端口。
3. 填写 VPS B 自己的证书指纹和共享密钥。
4. 点击“测试这台 VPS”。
5. 测试成功后保存备用 VPS 设置。

返回主界面点击“保存设置”，再点击“启动中转”。第一次弹出 Windows 防火墙提示时，只允许“专用网络”。

客户端的“一键诊断”会检查局域网 IP、本地端口、中转运行状态、在线矿机、主备 VPS 的 TCP/TLS/共享密钥连接和程序资源，并生成通俗的管理员结论。报告自动保存在当前 Windows 用户的木林森中转数据目录中，可以复制或另存为；报告不会保存共享密钥、证书私钥或矿池密码。

“开机自动启动”表示 Windows 用户登录后自动运行；“点击关闭最小化”表示点击窗口右上角关闭按钮后程序继续在托盘运行。异常自动恢复可以处理普通程序异常，但不能处理整机断电、Windows蓝屏、杀毒软件删除程序、任务管理器强制结束或进程仍存在的完全卡死。真正无人值守的电脑还应设置来电自动开机和 Windows 自动登录。

## 十、设置矿机

木林森中转主界面会自动显示“当前局域网 IP”和“矿机填写地址”。直接复制完整地址到矿机后台，例如：

```text
stratum+tcp://192.168.8.3:11301
```

这里必须填写值守电脑的局域网 IP，不能填写 VPS 公网 IP、Tailscale IP、面板地址或 TLS 端口。

每个本地端口对应 VPS 上的同号线路或显式映射线路。哪个矿机使用哪个矿池，由矿机后台填写的端口决定；矿机的多块算力板或多条 Stratum 连接仍按同一个局域网 IP 合并为一台矿机。

矿机保存后，在客户端主界面确认：

- 状态为“运行中”；
- 当前矿机和当前连接开始增加；
- 线路显示 VPS 正常；
- 运行记录出现“已通过 VPS 加密连接”；
- “矿机状态”中能按局域网 IP 看到矿工名、连接、Share 和估算算力。

## 十一、部署验收清单

按顺序检查，全部通过才算部署完成：

- [ ] 两台 VPS 的核心服务均为 `active`。
- [ ] 两台 VPS 的 TLS 端口已监听，云安全组已放行。
- [ ] 两台 Tailscale 面板地址都能打开。
- [ ] VPS A 与 VPS B 的线路同步状态正常。
- [ ] Windows 的“测试主 VPS”成功。
- [ ] Windows 的“测试备用 VPS”成功。
- [ ] Windows 显示的局域网 IP 是矿机能够访问的地址。
- [ ] 一台矿机试填地址后能产生接受 Share。
- [ ] 主 VPS 暂时停止或断网时，新连接能够使用备用 VPS。
- [ ] 恢复主 VPS 后，新连接重新优先使用主 VPS。

客户端的 VPS 测试证明 Windows 到 VPS 的 TCP、TLS 证书和共享密钥正常。矿池线路是否真正可用，还要以单机试切后出现接受 Share 为准。

## 十二、日常切换矿池

管理面板首页的“当前活跃转发线路”会显示类似：

```text
11301 → ltc-doge-usw-01.longpool.org:8443
```

修改新地址时建议按以下顺序：

1. 选择地址库中的目标，或填写新矿池域名、端口、币种和算法。
2. 点击“只检测，不切换”。
3. 选择一台正在使用该线路的在线矿机，开始 10 分钟单机测试。
4. 测试通过后系统自动全量切换，并加入已验证地址库。
5. 测试失败时，该矿机自动回到原线路。
6. 查看首页提示、事件日志和企业微信通知。

已验证地址可以直接切换。最近最多保留 10 次线路改动，可选择任意记录恢复。主、备 VPS 开启同步后，线路改动会自动同步。

VPS 无法可靠地从 Stratum V1 自动判断所有算法，因此自定义矿池时仍要正确选择算法。最可靠的判断是使用真实矿池账号完成单机测试，并确认产生接受 Share。

## 十三、升级已有 VPS

推荐直接使用统一入口：

```bash
cd /root/stratum-v3
./deploy.sh
```

以下是需要单独控制每个组件时的手动升级方法。

先进入仓库并拉取指定分支：

```bash
cd /root/stratum-v3
git fetch origin
git checkout main
git pull --ff-only origin main
```

升级 V3 面板和相关服务：

```bash
cd /root/stratum-v3/monitor-panel
chmod +x upgrade-v3-panel.sh
./upgrade-v3-panel.sh
```

升级加密入口：

```bash
cd /root/stratum-v3/secure-relay/server
chmod +x install-secure-relay.sh
./install-secure-relay.sh
```

重复运行加密入口安装脚本会保留现有端口、证书和客户端密钥。只有需要变更 TLS 端口时才传入 `--port <端口>`。

两台 VPS 都要执行升级。升级会重启相关服务，现有矿机连接可能短暂断开并自动重连，建议逐台升级：先升级备用 VPS 并验证，再升级另一台。

升级后可查看最近的本机自恢复记录：

```bash
cat /var/lib/stratum-monitor/vps-watchdog.json
systemctl list-timers stratum-vps-watchdog.timer
```

其中 `restarted` 表示看门狗确认连续异常后执行过恢复，`restart_failed` 表示恢复命令本身失败。看门狗的本地 TLS 探测不会更新矿场心跳，因此不会掩盖真正的值守电脑离线。

## 十四、常用检查与故障处理

### 查看所有关键服务

```bash
systemctl status haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-route-switch-monitor stratum-security-monitor stratum-admin stratum-secure-relay stratum-secure-monitor --no-pager
```

### 查看 TLS 加密入口日志

```bash
journalctl -u stratum-secure-relay -n 100 --no-pager
journalctl -u stratum-secure-monitor -n 100 --no-pager
```

### 查看管理和线路日志

```bash
journalctl -u stratum-admin -n 100 --no-pager
journalctl -u stratum-route-switch-monitor -n 100 --no-pager
tail -n 100 /var/lib/stratum-monitor/endpoint-events.jsonl
tail -n 50 /var/log/stratum-audit.jsonl
```

### 忘记应急管理密码

```bash
/opt/stratum-admin/reset-panel-password.sh
```

只通过 Tailscale 管理并希望关闭密码登录：

```bash
/opt/stratum-admin/reset-panel-password.sh --disable-password
```

### 修改 TLS 端口

```bash
cd /root/stratum-v3/secure-relay/server
./install-secure-relay.sh --port <新端口>
```

随后放行新端口，修改 Windows 主、备 VPS 设置并测试。确认新端口正常后再关闭旧端口。

### 回滚 V3 面板

```bash
cd /root/stratum-v3/monitor-panel
./rollback-v3.sh
```

安装或升级脚本也会输出备份目录，可以按输出路径手动恢复。

## 十五、安全资料和备份

以下内容只保存在 VPS 或值守电脑，不要上传 GitHub：

- `/etc/stratum-admin.env`
- `/etc/stratum-v3.env`
- `/etc/stratum-v3-peer.json`
- `/etc/stratum-secure-relay.json`
- 企业微信 Webhook
- VPS 同步密钥和 Windows 客户端共享密钥
- 面板密码哈希、SSH 私钥、日志和服务器备份包

GitHub 保存的是程序和部署方法，不保存每台服务器的秘密配置。更换 Windows 电脑时，可通过 Tailscale 在面板“设置 → 客户端接入资料”找回该 VPS 的客户端配置。重建 VPS 时仍应由安装脚本生成新证书和新密钥，再把新信息填入 Windows 客户端；证书私钥不能从网页导出。
