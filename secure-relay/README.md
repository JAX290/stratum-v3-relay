# 木林森中转 2.0

这套组件在值守 Windows 电脑和 Stratum V3 VPS 之间建立 TLS 1.2 加密连接。局域网矿机仍使用普通 `stratum+tcp`，但这段明文只存在于矿场局域网内；流量卡网络只能看到电脑到 VPS 的 TLS 连接。

```text
矿机 --局域网明文 Stratum--> 值守电脑 --TLS--> VPS --现有 V3 检查器--> 矿池
```

## 1. VPS 安装

先部署原有 Stratum V3，再执行：

```bash
cd /root/stratum-v3/secure-relay/server
chmod +x install-secure-relay.sh
./install-secure-relay.sh
```

脚本默认监听 TCP 443，生成自签名证书和第一组 64 位共享密钥，并输出客户端需要填写的证书 SHA-256 指纹。重复执行会保留已有证书和全部客户端密钥。

如果已有正规域名证书：

```bash
./install-secure-relay.sh \
  --cert /etc/letsencrypt/live/relay.example.com/fullchain.pem \
  --key /etc/letsencrypt/live/relay.example.com/privkey.pem
```

还需在云厂商安全组中放行脚本所用的 TCP 端口。检查服务：

```bash
systemctl status stratum-secure-relay --no-pager
journalctl -u stratum-secure-monitor -n 100 --no-pager
journalctl -u stratum-secure-relay -n 100 --no-pager
```

### 每台值守电脑使用独立密钥

不要在多个矿场重复使用同一密钥。为每台值守电脑创建一组密钥：

```bash
stratum-relay-client add mine-a "一号矿场"
stratum-relay-client add mine-b "二号矿场"
stratum-relay-client list
```

`add` 输出的 `Shared key` 只填到对应电脑。某台电脑需要换密钥时执行：

```bash
stratum-relay-client rotate mine-a
```

旧密钥会立即失效。电脑停用时执行 `stratum-relay-client disable mine-a`，恢复时执行 `stratum-relay-client enable mine-a`。服务端会自动重新读取密钥文件，不需要重启。

### 矿场离线告警

客户端每 30 秒发送一次经过认证的心跳。连续 180 秒没有心跳时，`stratum-secure-monitor` 会向原 V3 使用的企业微信机器人发送离线告警；恢复后发送一次恢复通知。可以编辑 `/etc/stratum-secure-relay.json` 中的 `offline_after_seconds` 改变等待时间，修改后重启监控：

```bash
systemctl restart stratum-secure-monitor
```

### 更改 TLS 端口

例如把 TLS 入口改为 `8443`：

```bash
cd /root/stratum-v3/secure-relay/server
./install-secure-relay.sh --port 8443
```

脚本会保留原来的共享密钥和自签名证书，更新端口并重启服务。随后在云厂商安全组和 VPS 防火墙中放行 TCP `8443`，再把 Windows 客户端的“TLS 端口”改成 `8443`。确认新端口正常后，才关闭旧端口的安全组规则。

## 2. Windows 客户端

运行 `client/木林森中转.exe`，填写：

- VPS 地址和 TLS 端口；
- 自签名证书填写安装脚本输出的 SHA-256 指纹；使用正规证书时填写证书域名；
- 安装脚本输出的共享密钥；
- 本地监听地址保持 `0.0.0.0`；
- 只保留实际需要的转发端口。

点击“启动中转”。Windows 防火墙第一次提示时仅允许专用网络。然后将矿池地址改为界面“矿机填写地址”显示的完整地址。

2.0 增加了主 VPS 和两个备用 VPS。点击“备用 VPS 设置”，分别填写各台 VPS 的地址、TLS 端口、证书和这台电脑在该 VPS 上的独立密钥。新矿机连接会先尝试主 VPS；主 VPS 无法连接、证书不符、密钥无效或没有对应路线时，依次尝试备用 VPS。主 VPS 恢复后，之后建立的新连接会重新优先使用主 VPS，已经稳定运行的连接不会被强制切断。

### Windows 自动启动

2.0.1 使用与稳定版相同的“Windows 用户登录后自动启动”方式。勾选“开机自动启动”并保存即可。曾经测试过由 EXE 自行提权、复制自身和创建系统服务的方案，但该行为组合会触发 Defender 的 `Behavior:Win32/Persistence.A!ml` 行为规则，因此已从发布程序中移除。真正的系统服务版本将在独立安装程序和代码签名准备完成后再提供。

“开机自动启动”和“点击关闭最小化”可以分别启用或关闭。设置完成后点击“保存设置”。软件内的“各项说明”按钮也可以随时查看通俗解释。

软件会自动识别值守电脑当前的局域网 IP，并根据“本地端口”生成完整的矿机填写地址。例如电脑 IP 是 `192.168.3.5`、本地端口是 `9999`，界面会显示：

```text
stratum+tcp://192.168.3.5:9999
```

多个端口会生成多个地址，可以在下拉框中选择并点击“复制地址”。IP 每30秒自动刷新，也可以手动点击“刷新”。

## 各个设置是什么意思

- **VPS 地址**：你的云服务器公网 IP，也就是加密数据要送到哪里。
- **TLS 端口**：VPS 接收加密连接的门牌号，通常使用 `443`。
- **证书名称**：如果 VPS 使用正规域名证书，这里填写证书对应的域名。只使用 IP 和自签名证书时留空。
- **证书 SHA-256**：服务器证书的唯一指纹。它帮助软件确认对面确实是你的 VPS，防止连接被冒充。
- **共享密钥**：客户端和 VPS 共同知道的密码。密钥错误时，VPS 会拒绝连接。
- **本地监听地址**：保持 `0.0.0.0`，表示允许同一局域网内的矿机连接这台值守电脑。
- **端口或端口映射**：`9999` 表示矿机连接本地 `9999`，VPS 也走 `9999` 路线；`10041=10001` 表示矿机连接本地 `10041`，VPS 按 `10001` 路线转发。多个项目仍用英文逗号分隔，本地端口不能重复。
- **当前局域网 IP**：程序自动识别值守电脑连接矿机局域网的 IPv4 地址，并排除 Tailscale、代理和常见虚拟网卡。
- **矿机填写地址**：将局域网 IP、本地端口和 `stratum+tcp://` 自动组合成完整地址，可以直接复制到矿机后台。
- **开机自动启动**：值守电脑登录 Windows 后，软件自动打开并启动中转。
- **关闭时最小化到托盘**：点击窗口右上角关闭按钮后，软件继续在后台运行；取消此项后，点击关闭会退出软件并停止中转。
- **运行记录**：显示哪些矿机连上、断开或发生连接错误，方便值守人员判断运行状态。
- **状态栏**：显示当前连接数、累计连接、失败次数、运行时间、上下行流量以及每台 VPS 的可用状态和延迟。

## 三台 VPS 的准备方法

主 VPS 和备用 VPS 都必须先部署相同的 Stratum V3 路线，再安装本目录的安全中转服务。三台 VPS 的端口路线应保持一致，这样客户端切换线路时，同一个 VPS 路线端口仍指向同一个矿池。每台 VPS 分别为这台值守电脑创建独立密钥，客户端的三个 VPS 页面分别填写各自服务器输出的密钥和证书指纹。

如果暂时只有一台 VPS，备用线路保持未启用即可，不影响现有用法。

例如原地址为 `stratum+tcp://203.0.113.10:9999`，值守电脑地址为 `192.168.1.20`，矿机改填：

```text
stratum+tcp://192.168.1.20:9999
```

## 安全和识别边界

- 共享密钥在 Windows 上由当前用户的 DPAPI 加密保存，VPS 配置权限为 `0600`。
- 客户端强制验证正规证书或固定 SHA-256 指纹，不提供跳过证书验证的选项。
- 未认证连接只得到普通 HTTPS 404 响应；矿池协议和认证字段始终位于 TLS 内。
- 网络仍能观察 VPS IP、连接时长、流量大小和时序。因此 TLS 能隐藏 Stratum 内容，但不能保证任何网络环境都无法识别或限制该连接。
- 自签名证书可以安全固定身份，但正规域名证书的 TLS 外观更常见。域名的 SNI 仍可能被网络看到。
