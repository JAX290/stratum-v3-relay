# 木林森 Stratum 加密中转

用于矿场的 Stratum 中转与监控系统。矿机连接局域网内的 Windows 值守电脑，值守电脑通过 TLS 加密连接 VPS，再由 VPS 将流量转发到矿池。

管理员可以通过 Tailscale 管理线路和矿场，也可以通过带账号密码的 HTTPS 只读面板日常查看状态。

当前 Windows 客户端版本为 `2.3.9`。本版增加签名自动升级：从 GitHub 正式发布下载后同时核对 SHA-256、文件版本、Windows 信任状态和固定发布者证书；支持按电脑稳定分批，替换后若新程序未能打开主界面会自动恢复上一版本。发布状态见[更新计划](docs/improvement-roadmap.md)，各组件版本定义见 [version.json](version.json)。

```text
矿机 → Windows 值守电脑 → VPS → 矿池
     局域网 Stratum       TLS    Stratum
```

TLS 加密覆盖值守电脑到 VPS 的链路；矿机到值守电脑、VPS 到矿池是否加密取决于各自协议和配置。

## 阅读导航

- [功能说明](#功能说明)：系统能做什么、在哪里操作。
- [两种面板的区别](#两种面板的区别)：管理员与值守人员的访问方式和权限。
- [快速部署与升级](#快速部署与升级)：首次安装、保留配置升级及中断续装。
- [管理员完整使用说明](docs/administrator-guide.md)：登录方式、页面指标、折叠明细原理、线路操作、矿场和通知管理。
- [部署与维护指南](docs/deployment-guide.md)：Windows、备用 VPS、矿机设置及组件排障。
- [后续更新计划与进度](docs/improvement-roadmap.md)：按 VPS 端、Windows 客户端分别维护待办、进度和完成记录。

## 功能说明

### 中转与矿池线路

| 功能 | 用途 | 操作位置 |
| --- | --- | --- |
| 加密中转 | 值守电脑与 VPS 之间使用 TLS，并校验证书指纹和共享密钥 | Windows 客户端的 VPS 设置 |
| 主、备用 VPS | 新连接在主 VPS 不可用时依次尝试已启用的备用 VPS；稳定连接不会因主 VPS 恢复而被强制切断 | Windows 客户端 → 备用 VPS 设置 |
| 矿池线路管理 | 使用矿池模板或自定义地址，配置端口对应的矿池线路 | 管理面板 → 线路与端口 |
| 矿池地址检测 | 手动检测连通性，配置自动检测频率，并查看异常记录 | 管理面板 → 线路与端口、设置 |
| 双 VPS 线路同步 | 修改线路后立即发送并反馈成功或失败；对端离线时保留任务并自动重试 | 管理面板 → 设置 → 双 VPS 线路同步 |
| 单机测试与线路恢复 | 先用少量矿机验证新矿池，再切换生产线路；支持恢复历史线路配置 | 管理面板 → 线路与端口 |

主、备用 VPS 的证书和客户端密钥分别配置。线路同步不包括客户端密钥、矿场名单或服务器证书。

### 矿场与客户端管理

| 功能 | 用途 | 操作位置 |
| --- | --- | --- |
| 新增矿场 / 客户端 | 填写名称后自动生成编号和独立共享密钥，供新的值守电脑接入 | 管理面板 → 设置 → 客户端接入资料 |
| 编辑矿场名称 | 修改显示名称，保留原编号、密钥和连接配置 | 同上，对应矿场名称下方 |
| 删除矿场 | 删除不用的接入配置；要求输入名称并勾选风险确认 | 同上，对应行的“删除矿场” |
| 查看接入资料 | 查看 VPS 地址、TLS 端口和证书指纹；限时查看或复制密钥 | 同上 |
| 矿场运行总览 | 查看在线状态、矿机数、连接数、客户端版本和最近心跳 | 管理面板 → 总览；HTTPS 只读面板 |

一个客户端密钥对应一个矿场分组。每台值守电脑建议使用独立密钥，多台电脑可在名称中注明编号。每台 VPS 独立管理接入资料。

**删除会撤销原密钥的新连接和重连权限，可能导致矿场中转掉线。请先停用或迁移对应客户端。** 已有连接可能暂时继续，不代表删除后仍可正常使用。新增、改名和删除的步骤见[管理员完整使用说明](docs/administrator-guide.md)。

### 状态、告警与故障处理

| 功能 | 用途 | 操作位置 |
| --- | --- | --- |
| 矿机与 Worker 状态 | 查看矿机 IP、Worker、连接和最近断线信息 | 管理面板 → 矿机；HTTPS 只读面板 |
| Share 统计 | 查看提交、接受和拒绝情况，判断矿机是否正常向矿池提交结果 | 管理面板 → 总览、矿机；HTTPS 只读面板 |
| 服务器与服务状态 | 查看负载、内存、磁盘、运行时间及核心服务状态 | 管理面板 → 总览；只读面板显示对应状态信息 |
| 运维问题中心 | 将服务异常和最近 24 小时 VPS 日志归纳为中文原因与处理建议，需要时展开原始日志 | 管理面板 → 日志 |
| 多渠道通知 | 企业微信、钉钉和邮箱均可设置 0–3 个接收目标；邮箱支持服务商 SMTP 或 VPS 直接发送，并可在页面逐项删除和测试 | 管理面板 → 设置 → 通知渠道 |
| 到期提醒 | 手动填写 VPS、域名购买到期日期；读取证书有效期并提示 | 管理面板 → 设置 → 管理员到期提醒 |
| Windows 一键诊断 | 检查本地网络、端口、中转状态和主备 VPS，生成可复制的诊断报告 | Windows 客户端 → 一键诊断 |
| VPS 自动恢复 | 服务异常退出由系统拉起；看门狗对连续无响应的指定服务执行恢复，并设置重试冷却 | 自动执行；管理面板查看相关事件和日志 |
| 审计与断线追溯 | 查看配置操作记录，下载最近 7 天的断线明细 | 管理面板 → 日志 |

Share 是矿机向矿池提交的计算结果；Worker 是矿池账号下的矿工标识。矿机数与连接数不是同一指标，一台矿机可能有多条连接。

上述表格只列出当前已经实现的功能。所有尚未实现、正在开发和已经完成的改进统一记录在[后续更新计划与进度](docs/improvement-roadmap.md)中，并按 **VPS 端**、**Windows 客户端** 分板块维护。每完成一项会同步更新状态、版本、日期和验证结果。

## 两种面板的区别

| 项目 | Tailscale 管理面板 | HTTPS 只读值守面板 |
| --- | --- | --- |
| 适用人员 | 负责配置、接入和维护的管理员 | 日常检查生产状态的值守人员 |
| 访问方式 | 设备连接 Tailscale，打开部署时生成的管理地址 | 普通浏览器打开状态域名，无需 Tailscale |
| 登录方式 | 使用 Tailscale 身份，访问范围由 Tailscale 网络权限控制 | 使用独立值守账号和密码 |
| 查看信息 | 运行状态、矿机、线路、服务、日志及审计等 | 矿场、矿机 IP、Worker、Share、矿池线路、服务和脱敏故障信息 |
| 修改配置 | 可以；客户端接入资料和矿场管理要求当前访问来自 Tailscale | 不可以 |
| 敏感资料 | 共享密钥默认隐藏，查看或复制记入审计；不提供证书私钥 | 不显示密钥、密码、Webhook、证书私钥、配置审计或原始日志 |

管理服务和只读服务分别监听本机 `8789`、`8790`，无需将这两个端口开放到公网。公网 HTTPS 网站使用 `80/443`，中转 TLS 入口推荐使用 `452`。Nginx 与 Tailscale Serve 的 HTTPS 监听地址由安装脚本分开配置。

Tailscale 暂时不可用时，可以使用 SSH 隧道和预先设置的应急密码进入管理面板；该方式不能打开客户端接入资料页。详见[面板访问说明](docs/deployment-guide.md#五通过-tailscale-打开管理面板)。

## 快速部署与升级

### 安装前准备

- Ubuntu VPS，具有 root 权限及可用公网地址。
- Windows 10/11 值守电脑，与矿机处于同一局域网。
- 管理设备安装 Tailscale；HTTPS 只读面板可独立使用。
- 放行 SSH 端口和中转 TLS 端口；启用 HTTPS 只读面板时另放行 TCP `80/443`。
- 可选状态域名和企业微信机器人 Webhook。

域名示例统一使用 `example.com`，执行命令时替换为实际域名。Cloudflare 的 `relay` 中转记录使用“仅 DNS”；`status` 状态网页记录可以使用“已代理”。详见[域名与 Cloudflare 指南](docs/cloudflare-domain-guide.md)。

### 新 VPS 一键部署

以 root 登录 VPS，执行：

```bash
apt-get update
apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/JAX290/stratum-v3-relay/main/deploy.sh -o /root/mulinsen-deploy.sh
chmod +x /root/mulinsen-deploy.sh
/root/mulinsen-deploy.sh --install
```

中文向导会依次设置 TLS 端口、Tailscale、可选 HTTPS 只读面板、相关账号密码和告警，完成后检查核心服务。默认中转端口为 `452`。

部署 VPS 后，还需配置 Windows 客户端、备用 VPS 和矿机地址。按[部署与维护指南](docs/deployment-guide.md)完成配置与验收。

### 已有 VPS 升级

```bash
cd /root/stratum-v3
./deploy.sh
```

脚本更新 GitHub `main`，备份配置、升级组件并检查服务。已有线路、TLS 端口、证书、客户端密钥、矿场配置、面板账号和告警设置会保留；仓库存在未提交代码修改时会停止，避免覆盖。

**升级会重启相关服务，矿机可能短暂断开并重连。** 多台 VPS 应先升级备用的一台，验证正常后再升级另一台。Windows 客户端的更新方法见[客户端说明](secure-relay/README.md)。

### 部署中断后继续

解决失败步骤显示的问题后，运行 `./deploy.sh`，不要再次强制使用 `--install`。脚本根据现有配置进入安装或升级流程。需要继续配置状态域名时执行：

```bash
cd /root/stratum-v3
./deploy.sh --public-domain status1.example.com
```

失败原因、服务检查和备份处理方法见[部署与维护指南](docs/deployment-guide.md#十四常用检查与故障处理)。

### 常用选项

| 选项 | 用途 |
| --- | --- |
| `--install` | 首次安装，检测到已有配置时拒绝执行 |
| `--upgrade` | 保留配置升级 |
| `--relay-port 452` | 指定首次安装的中转 TLS 端口 |
| `--public-domain status1.example.com` | 配置指定域名的 HTTPS 只读面板 |
| `--skip-tailscale` | 跳过 Tailscale 安装与配置 |
| `--skip-public-status` | 跳过 HTTPS 只读面板配置 |

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [管理员完整使用说明](docs/administrator-guide.md) | 三种登录方式、各页面指标、折叠明细原理、线路操作、矿场和通知管理 |
| [部署与维护指南](docs/deployment-guide.md) | 手动部署、Windows 与矿机设置、双 VPS、升级、检查及回滚 |
| [管理面板说明](monitor-panel/README.md) | 端口规划、矿池线路、监控、告警和面板维护细节 |
| [Windows 客户端与 TLS 服务](secure-relay/README.md) | 客户端设置、主备连接、证书及服务端维护 |
| [域名与 Cloudflare 指南](docs/cloudflare-domain-guide.md) | DNS 记录、代理状态、HTTPS 和更换 VPS |
| [后续更新计划与进度](docs/improvement-roadmap.md) | VPS 端与 Windows 客户端的待办、优先级、实施顺序和完成记录 |
| [版本发布流程](docs/releasing.md) | 版本号、发布包与 GitHub Release |

## 开发与自动测试

从仓库根目录执行，依赖见 `requirements-dev.txt`：

```powershell
# Windows
.\test.ps1
```

```bash
# Linux
./test.sh
```

GitHub Actions 检查 Python 测试、配置渲染、部署脚本语法和 Windows 客户端构建及核心测试。

## 安全与许可证

配置文件、共享密钥、密码、Webhook、SSH 私钥、日志和服务器备份不应上传到公开仓库。漏洞报告方式见 [SECURITY.md](SECURITY.md)，备份注意事项见[安全资料和备份](docs/deployment-guide.md#十五安全资料和备份)。

本项目采用 [MIT License](LICENSE)。
