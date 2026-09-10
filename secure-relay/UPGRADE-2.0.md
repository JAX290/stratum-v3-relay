# 木林森中转 2.0 升级步骤

## 升级顺序

先升级 VPS，再在一台非生产值守电脑测试客户端，最后逐台替换生产电脑。旧版客户端仍可连接升级后的 VPS，因此服务端升级不会要求所有电脑同时更换。

## 1. 升级主 VPS

```bash
cd /root/stratum-v3
git pull
cd secure-relay/server
chmod +x install-secure-relay.sh
./install-secure-relay.sh --port 452
```

也可以省略 `--port 452`，脚本会自动读取并保留当前 TLS 端口。脚本会同时更新服务端程序、systemd 权限和状态目录，并保留已有证书、端口路线和客户端密钥。不要只复制单个 Python 文件，否则新程序与旧服务权限可能不匹配。检查结果：

```bash
systemctl is-active stratum-secure-relay stratum-secure-monitor
ss -lntp | grep ':452'
```

## 2. 给值守电脑创建独立密钥

```bash
stratum-relay-client add mine-a "一号矿场"
```

保存命令输出的 `Shared key`。每台值守电脑执行一次，客户端编号不要重复。

## 3. 准备备用 VPS

备用 VPS 需要部署与主 VPS 相同的 Stratum V3 端口路线，再执行同一个安全中转安装脚本。然后在每台备用 VPS 上为这台值守电脑创建独立密钥。记录每台服务器的公网 IP、TLS 端口、证书 SHA-256 和 Shared key。

## 4. 测试 Windows 2.0 客户端

1. 关闭测试电脑上的旧版客户端。
2. 运行 `木林森中转2.1.3测试版.exe`。
3. 填写矿场名称和主 VPS 设置。
4. 点击“备用 VPS 设置”，填写并启用已经准备好的备用服务器。
5. 本地端口不变时继续填写 `9999,10001,...`；需要改本地端口时填写 `10041=10001`。
6. 点击“启动中转”，确认状态栏显示主 VPS 正常。
7. 用一台矿机连接界面生成的 `stratum+tcp://局域网IP:本地端口` 地址。
8. 确认矿池端算力和管理面板矿机记录正常。

## 5. 测试自动切换

只在测试窗口进行。临时阻断测试电脑到主 VPS 的 TLS 端口，等待矿机重新连接，确认运行记录显示改用备用 VPS。恢复主 VPS 后重新建立一条矿机连接，确认新连接重新使用主 VPS。不要通过关闭生产 VPS 来测试。

## 6. 设置自动启动

勾选“开机自动启动”并保存，然后重新登录 Windows 验证程序能够自动打开并开始中转。2.0.1 暂不创建 Windows 系统服务，以避免 Defender 将自我复制、提权和创建服务的组合判定为持久化威胁。

## 7. 验证心跳和告警

VPS 上检查最近心跳：

```bash
cat /var/lib/stratum-secure-relay/sites.json
journalctl -u stratum-secure-monitor -n 50 --no-pager
```

在计划好的测试时间停止一台测试电脑的服务。超过 180 秒后应收到一次离线告警；恢复服务后应收到一次恢复通知。

## 回退

如果测试版出现问题，停止 2.0 后重新打开原来的 `木林森中转.exe`。VPS 2.0 服务兼容旧客户端，不需要回退 VPS。生产切换前保留旧 EXE 和原配置备份。
