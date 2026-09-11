# Stratum V3 中转部署仓库

这个公开仓库用于保存 Stratum V3 中转管理面板的源码、部署脚本和恢复说明。

主要代码在：

```text
monitor-panel/
```

详细部署说明见：

```text
monitor-panel/README.md
```

## 矿场电脑加密入口

`secure-relay/` 提供 Windows 图形客户端和 VPS TLS 入口。矿机先连接矿场局域网内的值守电脑，再由客户端加密传输到现有 V3 中转服务器。

Windows 成品位于：

```text
secure-relay/client/木林森中转.exe
```

安装和配置说明见 [secure-relay/README.md](secure-relay/README.md)。

## 新 VPS 一键部署

```bash
cd /root
git clone https://github.com/JAX290/stratum-v3-relay.git stratum-v3
cd /root/stratum-v3/monitor-panel
chmod +x bootstrap-vps.sh
./bootstrap-vps.sh
```

部署时脚本会要求设置：

- 管理面板密码；
- 企业微信机器人 Webhook，可跳过；
- V3 服务、HAProxy、报警服务和管理面板自启动。

## 进入控制面板

SSH 隧道方式：

```powershell
ssh -N -L 8789:127.0.0.1:8789 -p <SSH端口> root@<VPS公网IP>
```

然后打开：

```text
http://127.0.0.1:8789
```

Tailscale 方式：

```bash
tailscale up
tailscale serve --bg http://127.0.0.1:8789
tailscale serve status
```

手机登录同一个 Tailscale 账号后，打开 `tailscale serve status` 显示的 `https://...ts.net/` 地址。

## 不要提交的内容

不要把以下内容放入 GitHub：

- `/etc/stratum-admin.env`
- `/etc/stratum-v3.env`
- 企业微信 Webhook
- VPS 双向同步密钥和 `/etc/stratum-v3-peer.json`
- 面板密码哈希
- SSH 私钥
- VPS 备份包
- 服务日志

完整说明请看 [monitor-panel/README.md](monitor-panel/README.md)。
