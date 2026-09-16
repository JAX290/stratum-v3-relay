# 木林森中转：域名与 Cloudflare 新手说明

本页说明怎样用一个域名管理多台 VPS，以及以后更换 VPS 时怎样只改 Cloudflare，不再逐台修改矿场电脑。

以下使用当前域名 `mulinsen.win` 举例。换成自己的域名时，操作完全相同。

## 1. 先理解域名有什么用

公网 IP 是 VPS 当前的实际地址，例如：

```text
172.245.91.54
```

域名是这个 IP 的固定名称，例如：

```text
relay1.mulinsen.win → 172.245.91.54
```

木林森中转填写 `relay1.mulinsen.win`。以后更换 VPS，只需在 Cloudflare 把右侧的旧 IP 改成新 IP，矿场电脑仍然使用原来的域名。

域名主要解决集中管理和更换 IP 的问题。它本身不会加密流量，也不会自动隐藏 VPS 的真实 IP；中转内容的加密仍由木林森中转的 TLS 完成。

## 2. 推荐的域名规划

不要把域名直接命名成“主用”和“备用”。主、备角色以后可能互换，使用固定编号更容易维护。

| 用途 | 示例域名 | 木林森中转填写位置 | Cloudflare 代理状态 |
| --- | --- | --- | --- |
| 第一台 VPS 中转 | `relay1.mulinsen.win` | 主 VPS 或备用 VPS 地址 | **仅 DNS（灰色云朵）** |
| 第二台 VPS 中转 | `relay2.mulinsen.win` | 主 VPS 或备用 VPS 地址 | **仅 DNS（灰色云朵）** |
| 第一台只读面板 | `status1.mulinsen.win` | 浏览器地址 | **已代理（橙色云朵）** |
| 第二台只读面板 | `status2.mulinsen.win` | 浏览器地址 | **已代理（橙色云朵）** |

`relay1` 永远代表第一台服务器位置，`relay2` 永远代表第二台服务器位置。哪一台在木林森客户端中排在“主 VPS”，由客户端设置决定。

## 3. 为什么灰色云朵和橙色云朵不同

### 中转域名必须使用灰色云朵

木林森中转使用的是自定义 TCP/TLS 端口，例如 `452`。Cloudflare 免费版的普通网站代理不能转发这种任意 TCP 服务。

因此：

```text
relay1.mulinsen.win    仅 DNS（灰色云朵）
relay2.mulinsen.win    仅 DNS（灰色云朵）
```

如果误开橙色云朵，客户端可能无法连接 VPS。灰色云朵会公开 VPS 的真实 IP，这是正常现象。

### 只读面板使用橙色云朵

只读面板是普通 HTTPS 网站，可以使用 Cloudflare 代理：

```text
status1.mulinsen.win   已代理（橙色云朵）
status2.mulinsen.win   已代理（橙色云朵）
```

橙色云朵可以隐藏面板源站地址，并提供 HTTPS 和基本的网站防护。仓库已经提供独立只读面板和 HTTPS 安装脚本；只添加 DNS 记录仍不会自动产生网页。

完整管理后台仍建议只通过 Tailscale 打开。公开的 `status1`、`status2` 只展示状态，不允许修改矿池、线路、证书、密钥或系统设置。

## 4. 第一次在 Cloudflare 添加记录

进入 Cloudflare 后依次打开：

```text
选择域名 → DNS → 记录 → 添加记录
```

第一台 VPS 的中转记录填写：

| 设置项 | 填写内容 |
| --- | --- |
| 类型 | `A` |
| 名称 | `relay1` |
| IPv4 地址 | 第一台 VPS 的公网 IP |
| 代理状态 | **仅 DNS** |
| TTL | 自动 |

第一台 VPS 的只读面板记录填写：

| 设置项 | 填写内容 |
| --- | --- |
| 类型 | `A` |
| 名称 | `status1` |
| IPv4 地址 | 第一台 VPS 的公网 IP |
| 代理状态 | **已代理** |
| TTL | 自动 |

第二台 VPS 使用 `relay2` 和 `status2`，其余步骤相同，只把 IP 换成第二台 VPS 的公网 IP。

### 在 VPS 安装 HTTPS 只读面板

`status1` 可以保持“已代理（橙色云朵）”。请确认 Cloudflare 记录内容确实是这台 VPS 的公网 IP，并在 VPS 安全组放行 TCP `80` 和 `443`。如果 VPS 启用了 UFW，还要执行：

```bash
ufw allow 80/tcp
ufw allow 443/tcp
```

完成 V3 安装后执行中文向导：

```bash
cd /root/stratum-v3/monitor-panel
chmod +x install-public-status.sh
./install-public-status.sh
```

向导会先要求设置独立的值守账号和至少 12 个字符的登录密码。询问域名时，第一台 VPS 输入 `status1.mulinsen.win`，第二台输入 `status2.mulinsen.win`。不要输入 `https://`、端口或斜杠。橙色代理开启时，域名会返回 Cloudflare 的代理 IP，而不是 VPS IP；向导会说明这一点，并请管理员核对 Cloudflare 记录内容后继续。

以前安装命令中的 `--email` 是 Certbot 的旧版证书账户联系邮箱，不是只读面板或管理面板的账号，也不会显示在网页上。Let's Encrypt 已于 2025 年停止发送证书到期提醒邮件，因此新版安装不要求填写邮箱；公网只读网站的证书由 Certbot 自动续期。为了兼容旧的自动部署命令，脚本仍接受 `--email`，但日常安装不需要使用。

脚本会安装 Nginx 和 Let's Encrypt 证书，把这个域名唯一转发到 `127.0.0.1:8790` 的只读服务，并检查 HTTPS 页面和自动续期服务。显示“安装成功”后，用浏览器确认下面地址正常。Cloudflare 可以继续保持“已代理（橙色云朵）”，SSL/TLS 模式建议使用“完全（严格）”：

```text
https://status1.mulinsen.win/
```

第二台 VPS 使用 `status2.mulinsen.win` 重复上述步骤。完整管理面板继续通过 Tailscale 访问 `127.0.0.1:8789`。安全组不要直接放行 `8789` 或 `8790`。

公网值守服务需要账号密码登录。登录后显示矿机 IP、Worker、Share、矿池地址、端口、线路健康、服务状态和经过凭据脱敏的事件原因；它不读取或展示共享密钥、密码哈希、Webhook、证书私钥、配置审计和未经脱敏的原始日志，页面没有线路或配置修改按钮。`/healthz` 仅返回是否健康和更新时间，供服务器检查使用，不返回内部信息。

需要更换公网值守账号或密码时运行：

```bash
cd /root/stratum-v3/monitor-panel
./install-public-status.sh --domain status1.mulinsen.win --reset-login
```

## 5. Windows 木林森中转怎样填写

主 VPS 或备用 VPS 的“VPS 地址”填写域名，不要带协议、端口或斜杠：

```text
relay1.mulinsen.win
```

下面这些写法不正确：

```text
https://relay1.mulinsen.win
relay1.mulinsen.win:452
relay1.mulinsen.win/
```

TLS 端口在单独的“TLS 端口”栏填写，例如 `452`。证书指纹和共享密钥仍填写对应 VPS 的实际内容。

保存后点击“测试主 VPS”或“测试这台 VPS”。必须同时通过 TCP、TLS 证书和共享密钥三项验证，才能正式启用。

## 6. 以后更换 VPS 的标准流程

假设要把 `relay1.mulinsen.win` 从旧 VPS 换到新 VPS。

### 第一步：先准备新 VPS

在新 VPS 上完整部署：

- V3 转发和管理面板；
- TLS 加密入口；
- 与现用服务器一致的端口和矿池线路；
- Tailscale；
- 对应的证书和客户端共享密钥。

此时不要关闭旧 VPS，也不要急着修改正式域名。

### 第二步：用临时域名测试

在 Cloudflare 添加：

```text
relay-test.mulinsen.win → 新 VPS 公网 IP
```

它同样必须使用“仅 DNS（灰色云朵）”。在木林森中转的备用 VPS 页面临时填写 `relay-test.mulinsen.win`，点击“测试这台 VPS”，再用少量矿机进行实际测试。

测试内容至少包括：

- TCP、TLS 证书、共享密钥全部通过；
- VPS 端口与矿池线路一致；
- 矿机能提交并收到接受 Share；
- 管理面板能看到矿机和线路数据；
- 主、备 VPS 线路同步正常。

### 第三步：修改正式记录

确认新 VPS 正常后，进入 Cloudflare 的 DNS 记录：

1. 编辑 `relay1.mulinsen.win`；
2. 把旧 IP 改成新 VPS 的公网 IP；
3. 保持“仅 DNS”；
4. 保存；
5. 编辑 `status1.mulinsen.win`，改成同一个新 IP；
6. 保持“已代理”并保存。

木林森中转不需要修改 VPS 地址。

### 第四步：等待连接迁移

修改 DNS 后，已经建立的矿机连接不会立刻迁移。旧连接可以继续使用旧 VPS；断线重连或重启木林森中转后，新连接才会重新查询域名并进入新 VPS。

建议保留旧 VPS 一段时间，确认新 VPS 已持续收到连接和 Share 后再停用旧 VPS。如果旧 IP 已完全不可用，可以修改 DNS 后重启木林森中转，使连接尽快重新解析域名。

### 第五步：清理临时记录

正式切换完成并确认稳定后，可以在 Cloudflare 删除 `relay-test`。不要提前删除仍在使用的正式记录。

## 7. 证书和共享密钥必须怎样处理

修改 DNS 只改变“域名指向哪台 VPS”，不会自动复制证书、密钥和线路配置。

如果新 VPS 使用原 VPS 的证书、私钥和客户端共享密钥，木林森中转可以保持原设置。复制时必须使用安全方式，证书私钥和共享密钥不能上传 GitHub或发到群聊。

如果新 VPS 生成了新证书或新共享密钥，必须在木林森中转的对应 VPS 页面更新：

- 证书 SHA-256 指纹；
- 共享密钥；
- TLS 端口（如果发生变化）。

否则即使域名已经指向新 IP，客户端也会因为证书或密钥不匹配而拒绝连接。

## 8. 主、备 VPS 应怎样切换

木林森中转中的“主 VPS”表示连接优先级，并不由 Cloudflare决定。

例如：

```text
主 VPS：relay1.mulinsen.win
备用 VPS：relay2.mulinsen.win
```

如果希望第二台成为优先线路，可以直接在木林森客户端交换主、备顺序。也可以保持客户端不变，只把 `relay1` 指向准备作为第一优先的新服务器。

更推荐保留每个域名与服务器位置的固定对应关系，通过木林森客户端调整优先级，避免以后忘记域名实际指向哪台机器。

## 9. 常见问题

### 域名能正常解析，但木林森测试失败

依次检查：

1. `relay` 记录是不是灰色云朵；
2. VPS 公网 IP 是否填写正确；
3. 云服务商安全组是否放行 TLS 端口；
4. VPS 防火墙是否放行该端口；
5. `stratum-secure-relay` 是否运行；
6. 客户端证书指纹和共享密钥是否属于这台 VPS；
7. Cloudflare 记录是否刚修改，电脑仍在使用旧解析结果。

### 浏览器打开 status 域名显示错误

DNS 记录只负责把域名指向 VPS。先执行上面的 `install-public-status.sh`，再检查：

```bash
systemctl status stratum-public-status nginx
curl -I http://127.0.0.1:8790/
certbot certificates
```

如果证书签发失败，先确认 Cloudflare 中 `status` 记录的内容是当前 VPS IP，并检查安全组和 VPS 防火墙是否放行 TCP `80`、`443`。仍然失败时，可以暂时改成“仅 DNS”重试，成功后再恢复橙色云朵。

### 修改 IP 后为什么还有矿机连接旧 VPS

DNS 只影响之后建立的连接。已经存在的 TCP 长连接不会被 Cloudflare 强制切换。等待自然重连，或者在确认新 VPS 正常后重启木林森中转。

### 使用域名后，运营商还能看到 VPS IP 吗

能。`relay` 使用灰色云朵，域名查询结果就是 VPS 的真实 IP。TLS 可以隐藏 Stratum 内容，但网络仍可能看到目标 IP、连接时长和流量大小。域名的主要作用是更换 IP 时统一修改，而不是让 IP 隐身。

### 域名被干扰怎么办

可以准备备用中转域名，例如 `relay2`，并在木林森中转中配置备用 VPS。必要时创建新的子域名并完成连接测试。仅更换子域名不能解决 VPS IP 本身已经被阻断的问题，此时还需要更换 VPS 公网 IP。

## 10. 当前示例记录

截至本说明编写时，第一台示例 VPS 使用：

```text
relay1.mulinsen.win  → 172.245.91.54  → 仅 DNS
status1.mulinsen.win → 172.245.91.54  → 已代理
```

这里不记录证书指纹、共享密钥、面板密码或企业微信 Webhook。此类秘密信息必须保存在自己的密码管理器或离线记录中。
