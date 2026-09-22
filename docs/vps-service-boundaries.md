# VPS 多服务共存边界

> **必须先读：** 本文是同一台 VPS 上部署任何新服务前的基础约束。目标是防止新服务抢占中转端口、覆盖 Tailscale 路由、替换 HAProxy/Nginx 配置，或耗尽主机资源导致矿机中断。

## 一、资源归属

| 资源 | 当前归属 | 新服务规则 |
| --- | --- | --- |
| 公网 TCP `80/443` | Nginx 统一 HTTP/HTTPS 入口 | 不启动第二套公网 Web 入口；为新服务增加独立子域名和 Nginx `server` 文件 |
| Tailscale HTTPS `443` | Stratum 管理面板 Serve 入口 | 不覆盖默认根路由，不执行 `tailscale serve reset` |
| TCP `452` | Windows 客户端 TLS 加密入口 | 永久保留，不复用 |
| TCP `8789` | 完整管理面板，仅 `127.0.0.1` | 不复用、不对公网开放 |
| TCP `8790` | HTTPS 只读面板后端，仅 `127.0.0.1` | 不复用、不对公网开放 |
| TCP `9999`、`10001-10002`、`10010-10012`、`10020-10022`、`10030-10032` | 动态矿池转发 | 不复用 |
| TCP `11001-11003`、`11101-11103`、`11201-11203`、`11301-11303` | 固定矿池转发 | 不复用 |
| TCP `20000-20299` | 内部矿池检查器预留范围 | 不复用、不对公网开放 |
| `/etc/haproxy/haproxy.cfg` | Stratum 自动生成 | 其他服务不得写入；普通网站使用 Nginx |
| `stratum-admin`、`stratum-proxy`、`stratum-relay` | 中转专用系统账户 | 新服务创建自己的系统账户，不加入这些组 |
| `/opt/stratum-admin`、`/var/lib/stratum-*`、`/etc/stratum-*` | 中转程序、状态和配置 | 新服务使用独立目录，不在这些路径写文件 |

实际生产端口仍以 `/etc/stratum-v3.json`、`/etc/stratum-secure-relay.json` 和 `ss -lntup` 为准。新增中转线路端口时，必须同步更新本表。

管理面板 3.2.19 起会自动恢复上表中的中转程序、systemd 单元和 HAProxy 生成配置。新服务若改写这些文件，改动会在下一轮看门狗检查中被恢复；若占用保留端口，看门狗会记录冲突并通知管理员，但不会自动结束未知进程。新服务必须使用自己的文件、账户和端口。

## 二、新服务端口分配

- Web、API 和后台管理服务：从 `12000-12999` 选择未占用端口，只监听 `127.0.0.1`。
- 内部 TCP 服务：从 `13000-13999` 选择未占用端口，默认只监听 `127.0.0.1`；需要跨设备访问时优先通过独立 Tailscale Service 暴露。
- 不把应用直接绑定到公网 `80/443`。Nginx 可以按域名把同一组公网端口分配给多个网站。
- 新服务必须在安装前检查端口，不能仅依赖“默认端口应该没被使用”的假设。

示例后端：

```text
127.0.0.1:12001  app1.example.com
127.0.0.1:12002  api.example.com
127.0.0.1:12003  内部管理服务
```

## 三、域名和 Nginx

每个公网服务使用唯一子域名，例如 `status.example.com`、`app1.example.com`。为每个域名创建独立的 `/etc/nginx/sites-available/<service>`，链接到 `sites-enabled`，并代理到对应的 `127.0.0.1` 后端。

必须遵守：

1. 修改后先运行 `nginx -t`，成功后只执行 `systemctl reload nginx`。
2. 不覆盖 `/etc/nginx/nginx.conf`，不删除其他服务的站点文件。
3. 不使用通配地址的新进程抢占公网 `80/443`。
4. 证书必须对应各自域名；运行 Certbot 前先检查现有证书和 Nginx 配置。
5. 普通 HTTP/HTTPS 域名可以共用 `443`；原始 TCP 服务不能直接依赖 HTTP 域名分流，继续使用自己的端口。

## 四、Tailscale

当前完整管理面板由 Tailscale Serve 转发到 `127.0.0.1:8789`。Serve 的后台配置会跨重启保留，因此后续执行新的默认 Serve 命令可能改变现有入口。

新服务优先使用唯一的命名服务：

```bash
tailscale serve --service=svc:app1 --https=443 127.0.0.1:12001
```

若暂时不使用 Tailscale Services，应选择明确且不冲突的 HTTPS 端口或路径。修改前后都要执行：

```bash
tailscale serve get-config --all
tailscale serve status --json
```

禁止执行 `tailscale serve reset`。关闭单项映射时使用与创建时相同的参数加 `off`，不得清空整机配置。Serve 与 Funnel 不能在同一端口同时使用；除非明确需要公网访问，否则不要为内部管理服务启用 Funnel。

参考：[Tailscale Serve 命令](https://tailscale.com/docs/reference/tailscale-cli/serve)、[Tailscale Services](https://tailscale.com/docs/features/tailscale-services)。

## 五、容器、防火墙和公网暴露

Docker 或 Podman 服务必须显式绑定回环地址：

```yaml
ports:
  - "127.0.0.1:12001:8080"
```

禁止默认的 `8080:8080` 形式，因为它通常会监听所有主机地址。Docker 会创建自己的包过滤规则，不能仅凭 UFW 页面判断容器没有暴露到公网。

防火墙规则采用增量修改：不重置 UFW，不删除不属于当前服务的规则。新增公网端口必须同时记录用途、调用方、协议和删除条件；内部端口不应加入云安全组或 UFW 公网放行列表。

参考：[Docker 防火墙与端口发布](https://docs.docker.com/engine/network/packet-filtering-firewalls/)。

## 六、账户、目录和资源隔离

每个新服务至少需要：

- 独立的无登录系统用户；
- 独立的 systemd 单元；
- `/opt/<service>` 程序目录；
- `/etc/<service>` 配置目录；
- `/var/lib/<service>` 状态目录；
- 独立环境文件和最小文件权限。

不得复用中转账户或把新服务加入中转私有组。根据服务规模设置 `MemoryHigh`、`MemoryMax`、`TasksMax`、`CPUQuota` 或独立 systemd slice，防止后来服务耗尽内存、进程数和 CPU。限额必须经过实际负载观察，不能直接照抄固定值。

## 七、部署前后检查

部署前保存结果，发现冲突立即停止：

```bash
ss -lntup
tailscale serve get-config --all
tailscale serve status --json
nginx -T
certbot certificates
ufw status numbered
systemctl --failed
df -h
df -i
free -h
```

使用容器时额外执行：

```bash
docker ps --format '{{.Names}}\t{{.Ports}}'
```

部署后重新执行同一组检查，并验证：

- 原有矿机转发端口仍由 HAProxy 监听；
- `452` 仍由加密入口监听；
- Tailscale 管理面板仍可打开；
- `nginx -t` 成功，原有状态域名正常；
- 新服务只占用登记的端口；
- `systemctl --failed` 没有新增失败项。

## 八、必须先提交迁移方案的变更

以下操作不能作为普通安装步骤直接执行：

- 更换公网 `80/443` 的入口程序；
- 修改或拆分 HAProxy 全局配置；
- 改变 Tailscale 管理面板域名、端口或根路由；
- 修改任何中转保留端口；
- 重置 UFW、Nginx、Tailscale 或 Docker 网络；
- 让新服务使用中转账户、组、证书私钥或状态目录。

迁移方案至少要说明当前占用者、新占用者、停机影响、验证方式和回退步骤，并在实施前备份相关配置。
