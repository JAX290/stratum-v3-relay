# VPS 共存边界（所有开发者和自动化工具必须遵守）

在修改 VPS 网络、反向代理、Tailscale、systemd、防火墙、Docker 端口或服务目录之前，必须先阅读 [`docs/vps-service-boundaries.md`](docs/vps-service-boundaries.md)。这些规则也适用于与本仓库无关、但部署在同一台 VPS 上的新服务。

## 不得直接改动的资源

- 不得让其他服务修改或接管 `/etc/haproxy/haproxy.cfg`。HAProxy 当前由 Stratum 中转独占并自动生成。
- 不得占用 TCP `452`、`8789`、`8790`、`9999`、`10001-10002`、`10010-10012`、`10020-10022`、`10030-10032`、`11001-11003`、`11101-11103`、`11201-11203`、`11301-11303` 或 `20000-20299`。
- 不得执行 `tailscale serve reset`。不得使用新的默认根路径 `tailscale serve --bg <target>` 覆盖当前管理面板。
- 不得让新服务直接监听公网 `0.0.0.0:80`、`0.0.0.0:443`、`[::]:80` 或 `[::]:443`。公网 HTTP/HTTPS 必须进入现有 Nginx，再代理到独立的 `127.0.0.1` 端口。
- Docker/Podman 新服务不得使用未限定地址的端口发布；必须使用 `127.0.0.1:<host-port>:<container-port>`，除非变更经过明确的公网暴露审查。
- 不得复用 `stratum-admin`、`stratum-proxy`、`stratum-relay` 用户或它们的私有目录和环境文件。
- 不得用新安装脚本覆盖整个 UFW、Nginx、Tailscale Serve 或 systemd 配置。

## 新服务的默认做法

- Web/API 后端从 `12000-12999` 选择未占用端口，并仅监听 `127.0.0.1`。
- 内部 TCP 服务从 `13000-13999` 选择未占用端口，并默认仅监听 `127.0.0.1` 或专用 Tailscale 地址。
- 公网网站使用唯一子域名和独立 Nginx `server` 文件，共享现有公网 `80/443`。
- Tailscale 私有服务优先使用唯一的 `svc:<name>` Tailscale Service；使用普通 Serve 时必须保留现有配置并选择不冲突的路径或端口。
- 每个服务必须有独立系统用户、systemd 单元、`/etc/<service>`、`/opt/<service>` 和 `/var/lib/<service>`。

## 任何部署前必须检查

```bash
ss -lntup
tailscale serve get-config --all
nginx -T
ufw status numbered
systemctl --failed
```

若使用容器，还必须检查 `docker ps --format '{{.Names}}\t{{.Ports}}'`。发现端口、域名、Tailscale 路由或配置文件所有权冲突时必须停止部署，先提交明确的迁移方案。修改任何保留资源时，应同步更新共存边界文档和部署回归测试。
