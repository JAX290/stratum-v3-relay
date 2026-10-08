# Stratum V4 方向讨论稿

> 本文是 V4 立项讨论的输入，不是已批准的开发计划。所有结论都需要共同确认后再转为
> `docs/improvement-roadmap.md` 中的正式条目。
>
> 分析基线：上游 `JAX290/stratum-v3-relay` 的 `main` 分支，提交 `692cfde`
> （2026-09-28）。工作副本位于本仓库。

## 一、先说一个必须澄清的问题：这里的 "V4" 指什么

上游仓库名叫 `stratum-v3-relay`，但版本号里的 `3` 指的是**这套中转系统的第三代实现**
（V3 面板 / 加密入口 / Windows 客户端），**不是 Stratum 协议第三版**。Stratum 协议只有
V1 和 V2 两个已发布版本。

**本项目当前实际跑的是 Stratum V1 scrypt 币种，不是比特币。** 证据在
`monitor-panel/v3-config.json`：

- 全部 15 个上游矿池地址 `algorithm` 都是 `scrypt`，`coins` 都是 `LTC/DOGE`；
- 矿池是 LitecoinPool.org、ViaBTC（`ltc.viabtc.*`）、F2Pool（`ltc.f2pool.com`）、
  LongPool（`ltc-doge-*.longpool.org`）、Hash-Hut；
- 固定端口段 `11001-11303` 就是按这四个矿池分配的。

这直接决定了 V4 的一个关键取舍：**Stratum V2 不适用于本项目的现有业务。** Stratum V2
（含 Job Declaration / Template Provider）只定义了比特币的工作证明与区块模板协商，
对 LTC/DOGE scrypt 没有规范，也没有矿池实现。所以 V4 不能以"升级到 Stratum V2"为
主线；如果确实要碰 SV2，它只能是**新增的、与 LTC 线路并列的一种上游接入方式**。

因此建议把命名语义固定下来，避免以后每次讨论都重新解释：

| 名称 | 含义 |
| --- | --- |
| **V4** | 本系统的第四代实现（产品版本） |
| **SV1 / SV2** | Stratum 协议版本，与 V4 无关 |
| **面板 / 加密入口 / 客户端** | 三个可独立发版的组件，保留 `version.json` 的三元组 |

如果确认走 V4，仓库名建议用 `stratum-relay-v4` 或独立的组件式命名，不要用
`stratum-v4-relay` 这类容易被误读为"Stratum 协议 V4"的名字。

## 二、上游现状盘点

### 2.1 三个组件

代码量按换行符精确统计（`monitor-panel/` 与 `secure-relay/server/` 用 Python，客户端用 C#）：

| 组件 | 主要文件 | 行数 | 版本 |
| --- | --- | --- | --- |
| VPS 面板与运维服务 | 34 个 `.py` 共 11,528 行；17 个 `.sh` 共 2,147 行 | `stratum_admin_v3.py` **3,109**、`test_admin_v3.py` **1,119**、`stratum_inspector.py` 738、`endpoint_monitor.py` 732 | 3.2.22 |
| VPS 加密入口 | `secure-relay/server/stratum_secure_server.py` 492 + 测试 283 | 775 | 2.2.5 |
| Windows 值守客户端 | 34 个生产 `.cs` 共 3,874 行 + 12 个测试 `.cs` 共 852 行 | `MainForm.cs` 497、`RelayManager.cs` 393 | 2.3.13 |

合计：Python 11,528 行、C# 4,174 行、shell 2,147 行、文档 2,708 行。上游在
2026-08 至 2026-09 的约两个月里提交了 116 次（其中 9 月 113 次），作者主要是
`Codex <codex@example.local>`。

数据通路是 **TCP 隧道 + 旁路观测**，不是协议代理：

```text
矿机 --Stratum V1 明文--> Windows 值守电脑 --TLS + Bearer--> VPS --TCP--> 矿池
                              (解析 JSON 行做统计)      HAProxy 分流
                                                        └-> stratum-inspector (解析并透明转发)
```

这个设计本身是对的：中转层不做协议改写，只搬运字节，因此不会因为解析错误而损毁
Share。HAProxy 按公网端口把流量送到固定的 `127.0.0.1:20000+` 内部端口，检查器同时
解析并透明转发。矿机来源 IP 通过 HAProxy 的 `send-proxy` 传给检查器，因此面板能按
局域网 IP 统计矿机数——这条链路被安装脚本用硬检查保护着
（`secure-relay/server/install-secure-relay.sh:33`）。

### 2.2 安全模型（这部分是上游做得最好的）

值得在 V4 中原样保留的部分：

- 面板以低权限 `stratum-admin` 账户运行，特权操作全部走
  `stratum-admin-helper.service` 的 Unix 套接字助手；
- 助手用 `SO_PEERCRED` 校验调用者 UID（`privileged_helper.py:230`），并且只接受
  **固定操作白名单**（`service` / `haproxy_apply` / `firewall_allow` /
  `certificate_apply` / `upgrade` / `write_managed`），不接受任意命令和任意路径；
- HAProxy 配置由 root 助手自己从 `stratum-v3.json` 重新生成后 `haproxy -c` 校验再
  `os.replace`（`privileged_helper.py:95-110`），面板无法直接写 `/etc/haproxy`；
- root 看门狗（`recovery_guard.py` + `vps_watchdog.py`）每分钟校验 root 专属可信基线，
  重建被篡改的程序与生成配置；
- Windows 侧的 `ServiceStoragePermissions.cs` 拒绝重解析点、要求关闭 ACL 继承、
  世界可读直接拒绝启动——这是全仓库最扎实的一段安全代码。

### 2.3 部署链路

`deploy.sh` → `monitor-panel/bootstrap-vps.sh` + `install-v3.sh`（首次）或
`upgrade-v3-panel.sh`（升级）→ `secure-relay/server/install-secure-relay.sh`。
systemd 单元全部由 shell heredoc 生成（`install-v3.sh:116-361` 一次写了 8 个单元）。

`install-v3.sh`（350 行）与 `upgrade-v3-panel.sh`（389 行）有大量逐字重复的单元定义、
目录创建和权限迁移逻辑，两边必须手工保持同步——`test_deployment_security.py` 正是
靠 `assertIn` 逐一检查两边都含有某段字符串来防止漂移。这是 V4 最该消除的结构性负担。

## 三、技术债清单

### 3.1 VPS 端

| 问题 | 证据 | 影响 |
| --- | --- | --- |
| 上帝模块 | `stratum_admin_v3.py` **3,109 行**、130 个函数、41 个路由，配置、渲染、审计、通知、双机同步、自动修复、证书全在里面 | 任何改动都要回归全量测试；新人无法局部理解 |
| 模块级全局配置，测试靠猴子补丁 | `test_admin_v3.py`（**1,119 行**）的 `setUp` 逐条改写 **30 个模块级路径常量**（`admin.CONFIG_FILE = ...`） | 代码里没有可注入的边界；并发测试不可能 |
| 模板即应用 | `templates/v3_dashboard.html` 73.8 KB、510 个 Jinja 表达式、内联 JS | 前后端逻辑混在模板里；无法对界面逻辑做单元测试 |
| 源码字符串断言冒充安全测试 | `test_deployment_security.py` 13 个测试、29 处 `read_text()`，断言的是"脚本里含有某段字符串" | **重构即误报、真错不报**；这是阻止把逻辑从 bash 搬进 Python 的根本原因 |
| 部署逻辑三套并存 | `install.sh` / `install-phase2.sh` / `rollback-phase2.sh` 是 v2 时代残留，仍在安装 `stratum_admin.py`、`stratum_admin_server.py`（两个各 200+ 行的旧面板） | 死代码、双份维护、误用时会把 v2 面板装回生产 |
| 手工分片升级脚本 | `upgrade-dashboard.sh`（25 行）、`upgrade-alert-monitor.sh`（18）、`upgrade-integrity-monitor.sh`（17）、`upgrade-probe-settings.sh`（33）、`upgrade-worker-view.sh`（26） | 与 `upgrade-v3-panel.sh` 部分重叠，历史包袱 |
| 配置存储靠文件约定 | `v3-config.json` + `.lock` 文件 + `generation` 计数器 + `os.chown` 分组保持，权限迁移散落在两个安装脚本里 | 每次动配置结构都有升级后 500 的历史（roadmap 里 `面板 3.2.21`、`3.2.22` 两次修正都是这个原因） |
| 写操作面板跑 Flask 开发服务器 | `install-v3.sh:277` 直接 `ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_admin_v3.py`，末尾 `app.run(host="127.0.0.1", port=8789)`（`stratum_admin_v3.py:3108`）无 WSGI 容器；而只需读的 `stratum-public-status` 反而用了 `gunicorn --workers 2 --threads 2`（`install-v3.sh:312`） | 权力最大、任务最重（全面诊断、矿池探测、支持包导出都是数十秒级）的面板用的是仅适合开发的服务器；并发行为依赖 Werkzeug 默认值，无进程管理、无优雅重启、无 worker 隔离 |
| `upgrade` 特权操作面偏大 | `privileged_helper.py:141-151`：只要 `/var/lib/stratum-monitor/candidates` 下有 root 持有的目录，就以 root 执行其中的 `upgrade-v3-panel.sh` | 目录权限是 0750 root:stratum-admin（面板账户**可读可执行**）；面板一旦被攻破即可触发 root 级代码执行 |

### 3.2 Windows 客户端

| 问题 | 证据 | 影响 |
| --- | --- | --- |
| 没有真正的项目/共享库 | `build.ps1` 与 `build-service.ps1` 各自手写一份源文件清单；19 个文件里 **15 个逐字节相同**，重复约 **2,409 行 = 桌面版 3,200 行的 75%** | 改一个文件要改两处清单；漏写就是编译失败或静默缺功能 |
| 上帝类 | `MainForm.cs` 497 行，同时做 UI、配置读写、完整验证+回退、一键诊断、检查修复、远程动作、升级编排 | 六条工作流全部无自动化覆盖 |
| 单行超长方法 | `MainForm.cs:407-411`（约 1.6 KB/行）、`:413-432`、`:434-458`；`SignedUpdate.cs:37`（1000+ 字符）；`DiagnosticReportForm.cs:30` 整个构造函数一行 | 不可读、不可 diff、不可评审 |
| 逻辑多副本且规则已漂移 | `FormatBytes` 3 份；共享密钥校验 4 份且**正则不一致**；远程动作诊断两份（`MainForm.cs:390-399` 与 `RelayServiceHost.cs:94-101`）内容已不同 | 同一概念多个真相 |
| 硬编码常量 | 固定发布者指纹 `D83C5C3E…AAB` 出现 6 处；User-Agent 硬编码 `MulinSenRelay/2.0`（`RelayManager.cs:136`）且已与真实版本脱节；GitHub 仓库路径硬编码 2 处 | 轮换证书或改名仓库要全文搜索 |
| 静默吞异常 | `ConfigModels.cs:65/68`（读配置失败静默用默认值、解密失败返回**空密钥**）、`MinerHistoryStore.cs:39`、`RelayManager.cs:324` 等 | 故障不可见 |
| `async void` | `RelayManager.cs:102/113/212` | 异常逃逸到全局处理器，调用方无法 await/捕获 |
| 构建工具链过旧 | .NET Framework 4.0 + `csc.exe` + 手写 PowerShell；无 csproj、无 `dotnet test` | 无法用现代测试框架、无法做覆盖率、CI 只能跑脚本 |

### 3.3 两个组件共有的发布问题

- 仓库里**提交了 6 个已编译 EXE（1.5 MB）**，且只到 `2.2.1`，与源码 `2.3.13` 不一致；
  `.gitignore` 已经忽略 `*.exe`，属于历史残留。
- `release-client.yml` 的签名作业被 `vars.ENABLE_CLOUD_SIGNING == 'true'` 关掉，
  注释说明当前发布证书私钥不可导出，**只能在受控 Windows 主机手工签名发布**。
  这意味着"签名自动升级"的下游能力有了，上游发布却是手工的。
- `scripts/check-version.py` 只校验 `version.json` 三个字段格式和 README 里的一句话，
  不校验各组件源码里的实际版本。

### 3.4 运维层面的一个真实风险（不是代码问题，但优先级更高）

`monitor-panel/v3-config.json` 里**四个动态端口组（`9999`/`10001-10002`、
`10010-10012`、`10020-10022`、`10030-10032`，共 12 个端口）全部指向 Hash-Hut 一家矿池**
（`hashhut-eu` / `hashhut-ru` / `hashhut-by` 三个地域节点）。

也就是说：四个"备用端口组"其实只是同一家矿池的三个区域入口，**它们不构成跨矿池容灾**。
Hash-Hut 整体故障、被攻击、或停止对特定地区结算时，12 条主线一起断，而面板的
`templates` 机制结构上只支持"一个模板 = 一家矿池的三个地域"。这是当前架构里
**最大的单点**，而且它藏在配置语义里，不读配置看不出来。

V4 如果只做一件事，建议是这一件。

## 四、V4 的三个候选方向

### 方向 A：控制面重写（推荐）

保留已经验证过的数据面（HAProxy + 透明检查器 + TCP 隧道 + 指纹固定 TLS + 特权助手模型），
重写控制面。

- **VPS 端**：`monitor-panel/` 拆成有明确边界的包
  （`config` 配置与迁移 / `routes` 线路与端口规划 / `observers` 检查器与探测 /
  `notify` 通知 / `cluster` 双机同步 / `ops` 诊断与修复 / `web` 路由与模板），
  用类型化配置 + 显式 schema 版本取代"JSON + generation + 锁 + 手工 chown 迁移"。
  部署从 shell 改为**幂等的 Python 安装器 + 真实的 systemd 模板文件**，
  `install` 与 `upgrade` 共用同一份单元定义。
- **Windows 端**：建 `Relay.Core` 类库（.NET Framework 4.8 或
  `netstandard2.0 + net48` 双目标），桌面版与服务版只留宿主；用 csproj + `dotnet test`
  取代手写 csc 脚本；把 `MainForm` 的六条工作流抽成无 UI 依赖的协调器。
- **测试**：把"源码字符串断言"换成"在容器/tmp 里真跑一遍安装，断言最终文件系统、
  权限和 systemd 单元内容"；给配置迁移写逐版本 fixture。
- **发布**：单一 csproj + 单点版本 + CI 自动生成 SHA256SUMS；把 6 处硬编码指纹
  改为构建时注入 + 启动时自检。

**优点**：风险最低，能顺手解决上面几乎所有技术债；不改变矿场侧任何操作习惯；
可以增量交付（先拆包，后换部署，最后换构建）。
**缺点**：不产生新的业务价值，纯工程投入；需要一套"升级后配置一定不丢"的回归网。

### 方向 B：策略与容灾能力升级

不动架构，只补业务能力。候选（按我建议的优先级）：

1. **跨矿池真容灾**：把"模板 = 一家矿池的三个地域"改成"线路 = 有序的地址候选列表，
   可跨矿池"。配合"按矿池结算方式过滤"（PPS / PPS+ / PPLNS 收益模型不同，不能无脑
   自动切换）。这是当前最大单点的直接解药。
2. **按线路的延迟与稳定性画像**：现在有 `benchmark_pools.py` 和地址探测，但缺
   "这条线路最近 1 小时的握手延迟分布 / 掉线率"，也缺自动排序依据。
3. **矿机侧失控检测**：检查器只看经过自己的流量。可加"值守电脑上发现同一网段存在
   直连外部矿池的矿机"告警（需要本地网络扫描，属于 Windows 端能力）。
4. **通知插件化**：现在企业微信/钉钉/邮件是硬编码三类。抽成插件接口后可加 Telegram、
   飞书、短信、Webhook 自定义。
5. **算力与收益历史**：现在只有 24 小时矿机历史和 7 天巡检。做长期时序后可做
   "这台矿机算力在退化"这类判断。

### 方向 C：Stratum V2 试点（低优先级，且需要前提）

只有在满足以下**全部**条件时才值得做：

1. 矿场里有比特币算力（目前配置里没有一行 BTC 地址）；
2. 目标 SV2 矿池确实支持 Job Declaration，且愿意接受自建模板；
3. 现有固件能对接（或用 SRI 的 translation proxy 把现有 SV1 矿机接到 SV2 矿池）；
4. 目标收益明确——要么是争取区块模板手续费，要么是 latency 改善，而不是"技术更新"。

现状参考：SRI 已发布 1.0.0 并支持 SV1→SV2 翻译代理与 Job Declaration
（[stratumprotocol.org/blog/sri-1.0.0](https://stratumprotocol.org/blog/sri-1.0.0/)），
主流矿池在 2026 年对 Job Declaration 的支持仍在推进中
（[D-Central SV2 部署跟踪](https://d-central.tech/stratum-v2-deployment-tracker/)）。
对 LTC/DOGE 无任何可用实现。**建议 V4 明确不包含 SV2，把它记为"未来的独立组件"。**

## 五、建议的 V4 范围与里程碑

建议取 **方向 A 为主体 + 方向 B 第 1 项作为首个业务交付**，理由：跨矿池容灾是当前
唯一的系统性单点，而它恰好需要动配置模型——和方向 A 的配置重写是同一次改动，
分开做就是白白搬两次家。

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 记录现状** | 冻结上游 `main`，补一份"当前生产行为"基线：端口表、配置结构、systemd 单元、权限矩阵、通知行为 | 任何一次改动都能和基线逐项对比 |
| **M2 拆包** | `monitor-panel/` 按边界拆包，保持行为不变；删除 v2 残留（`stratum_admin.py`、`stratum_admin_server.py`、`install.sh`、`install-phase2.sh`、`rollback-phase2.sh` 及 5 个碎片升级脚本） | 面板测试全绿（158 项）且不再依赖源码字符串断言 |
| **M3 配置模型** | 类型化配置 + schema 版本 + 逐版本迁移函数；线路从"模板=一家矿池"升级为"跨矿池有序候选" | 提供从现有 `v3-config.json` 的自动迁移并保留全部线路；迁移失败可回滚 |
| **M4 跨矿池容灾** | 自动切换按"矿池结算方式 + 地域 + 最近成功率"排序，跨矿池候选；提供单机试切验证门禁 | 注入一家矿池整体不可达，验证流量按序切到另一家且 Share 恢复 |
| **M5 部署收敛** | `install` / `upgrade` 共用单元定义与迁移逻辑；安装器幂等且可重复执行 | 在干净容器里从零装、连装两次、从 v3 升级三种路径都通过；断言最终文件系统与单元内容 |
| **M6 Windows 工程化** | `Relay.Core` 类库、csproj、`dotnet test`；`MainForm` 六条工作流抽成协调器 | 现有 247 项隔离检查全部保留并转为 `dotnet test`；六条工作流各有行为测试 |
| **M7 安全收口** | 收紧 `upgrade` 特权操作面（改为校验签名的发布包，不再是 candidates 里的任意脚本）；统一共享密钥校验规则；`ConfigStore.Save` 不再明文落盘密钥；`recovery.json` 加完整性保护 | 每项有对应的负向测试 |
| **M8 发布流水线** | 单点版本、指纹注入、CI 生成校验清单；决定是申请可导出的代码签名证书还是保留手工签名 | 一次打标签能产出可验证的完整发布包 |

## 六、需要你决定的几个问题

1. **V4 的范围**：只做方向 A（工程重写），还是 A + 跨矿池容灾（建议），
   还是先只做方向 B 的业务改进？
2. **是否保留 Stratum V3 的生产部署不动**：V4 是"另起仓库、平行演进、验证后再切换"，
   还是"直接在当前生产上迭代"？前者安全但需要双份维护一段时间。
3. **Windows 端技术栈**：继续 .NET Framework 4.x（改动小、现场机器兼容性最好），
   还是迁到 .NET 8 + WinForms/WPF（现代工具链、可 `dotnet test`，但需要现场装运行时
   或自包含发布，安装包会变大）？
4. **Stratum V2**：确认 V4 不含 SV2，还是希望预留一个可插拔的上游协议接口
   （即使暂时只有 SV1 实现）？
5. **仓库落地**：新仓库名、是否保留完整 git 历史（建议保留，`git filter-repo`
   可先摘掉那 6 个 EXE 和 `secure-relay/local-archives/`）、是否继续 MIT。
6. **签名证书**：是否值得为 CI 自动发布申请一张私钥可导出的代码签名证书？
   这决定了 M8 是"自动化"还是"继续手工"。

## 七、附：上游值得保留的设计（V4 不要丢）

- HAProxy 独占 `/etc/haproxy/haproxy.cfg` 并自动生成 + `haproxy -c` 预检；
- 透明转发（不改写协议）+ 旁路解析统计的检查器分工；
- `send-proxy` 传递矿机真实来源 IP，并在安装时硬校验；
- 特权助手白名单 + `SO_PEERCRED`，面板低权限运行；
- root 专属可信基线 + 每分钟看门狗自动恢复；
- Windows 服务的受限 ACL 校验、Job Object 隔离工作进程、挑战式心跳；
- 证书指纹固定 + TLS 1.2 + Bearer 共享密钥；
- 双 VPS 节点编号选举通知主节点、备用抑制重复通知；
- 高危操作向导（候选验证 → 影响说明 → 复核 → 二次验证）；
- 单机试切（只让一台矿机改道，验证 Share 后才全量）；
- `docs/vps-service-boundaries.md` 这份共存边界文档，以及 `AGENTS.md` 的强制约束。
