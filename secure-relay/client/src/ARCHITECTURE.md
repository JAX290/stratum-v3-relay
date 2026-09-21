# Windows 客户端源码结构

WIN-D07 将现有逻辑按职责分文件，生产程序仍编译为单个 EXE，配置和网络协议不变。

源码和脚本现位于 client/src。运行本目录 build.ps1 时，正式 EXE 默认输出到上一级 client；历史 EXE 保留在 client 外层。仓库根目录的测试入口和 CI 已同步调整。

WIN-P01 已在 2.3.0 完成 Windows 服务、独立看门狗、配置迁移、安装回退和重启实机验收；边界与验证记录见 [服务阶段说明](SERVICE-DESIGN.md)。WIN-P03 在 2.3.1 完成最后可用配置存储、显式完整验证和失败自动回退。WIN-P04 在 2.3.2 完成防抖策略、桌面与服务共用连接路径集成及真实 TLS 故障注入验收。WIN-P05 在 2.3.3 监听网络可用性、网卡地址和休眠唤醒事件，稳定后自动重建监听与 VPS 连接。WIN-P02 在 2.3.4 增加无特权检查和按需管理员防火墙修复。WIN-P06 在 2.3.5 将日常值守首页与高级参数分离。WIN-P07 在 2.3.6 使用 Windows 管理员凭据保护高级设置，并让授权自动过期。WIN-P08 在 2.3.7 与面板 3.2.5 完成加密接入文件生成、导入和一次性限制。WIN-P09 在 2.3.8 完成加密换机备份、完整验证门禁和切换指引。WIN-P10 在 2.3.9 完成固定发布者签名升级、分批控制和启动失败回退。WIN-P11 在 2.3.10 与面板 3.2.6、加密入口 2.2.3 完成远程状态和白名单操作。WIN-P12 在 2.3.11 完成脱敏技术支持包。

| 文件 | 职责 |
| --- | --- |
| `StratumSecureRelay.cs` | 程序入口、单实例和全局异常处理 |
| `AppIdentity.cs` / `AppBrand.cs` | 版本与名称 / 窗口标题与图标 |
| `ConfigModels.cs` | 配置、端口映射、DPAPI 和配置存储 |
| `RelayManager.cs` | 监听、TLS 认证、中转、主备连接及运行状态协调 |
| `RelayStatus.cs` | 线路状态与中转快照 |
| `MinerStatistics.cs` | Stratum 观察、Share 统计与矿机快照 |
| `MinerHistoryStore.cs` | 矿机历史序列化和存储 |
| `NetworkHelper.cs` / `SystemStatus.cs` | 局域网地址选择 / 系统运行时间 |
| `CrashRecovery.cs` | 现有桌面进程异常恢复 |
| `LastKnownGoodConfiguration.cs` | 最后可用配置的验证证据、加密快照、原子替换和防篡改读取 |
| `CompleteConfigurationValidator.cs` | 本地监听端口、启用 VPS 的证书、密钥和连通性完整验证 |
| `ConfigurationRollback.cs` | 验证失败后恢复不同且完整性有效的最后可用配置 |
| `FailoverPolicy.cs` | 连续失败阈值、线路重试冷却、切换冷却和主线路恢复观察策略 |
| `RelayFailoverController.cs` | 并发失败去重、连接候选排序及健康探测与防抖策略协调 |
| `NetworkRecovery.cs` | 断网恢复、网卡/IP 变化和休眠唤醒的稳定等待、重复抑制及限次重试 |
| `ClientRepair.cs` | 配置、监听、端口、防火墙和版本检查，以及受限的防火墙修复入口 |
| `DutyStatus.cs` | 将运行、线路、恢复进度和矿机健康聚合为值守首页结论 |
| `AdminAccessPolicy.cs` / `AdminLoginForm.cs` | 值守员默认权限、Windows 管理员身份验证和 15 分钟授权窗口 |
| `AccessPackage.cs` / `AccessPackageCodeForm.cs` | 加密接入文件的认证解密、有效期、一次性导入记录和配置合并 |
| `MigrationBackup.cs` / `MigrationPasswordForm.cs` | 跨电脑加密备份、完整配置恢复、验证证据门禁和迁移密码输入 |
| `SignedUpdate.cs` | GitHub 正式包下载、摘要/版本/签名校验、稳定分批、独立替换助手和启动失败回退 |
| `RemoteControl.cs` | 三类远程动作白名单、响应解析和跨重启操作回执 |
| `SupportBundle.cs` | 支持包白名单取材、地址与凭据脱敏、阻断扫描、摘要清单和原子 ZIP 导出 |
| `MainForm.cs` | 主窗口和当前诊断工作流 |
| `DiagnosticReportForm.cs` / `MinerStatusForm.cs` | 诊断报告 / 矿机列表 |
| `BackupForm.cs` | 备用 VPS 窗口及服务器设置控件 |

核心不依赖 WinForms、Drawing 或窗口类。AppBrand 的界面方法通过 partial 类留在界面文件中，核心仅使用版本和名称。状态访问继续使用 RelayManager 原有锁；界面读取快照，历史格式和 DPAPI 用户作用域不变。未来 Windows 服务不能直接假定能读取另一用户的 DPAPI 配置，需在 WIN-P01 单独设计安装和迁移流程。

## 构建与验证

执行 `./test-client.ps1`，在唯一临时目录中编译完整客户端并运行回归，再不引用 WinForms/Drawing 编译核心库并执行同一套回归。输出路径会显示在末尾，不覆盖客户端目录中已有 EXE。

核心每种构建 45 项检查覆盖配置兼容、端口映射、Share 接受和拒绝、分包解析、重复响应、历史窗口、快照隔离、在线矿机删除保护、端口冲突后的监听清理、重复停止、本地监听健康检查、最后可用配置存储、完整验证、配置回退，以及中转快照中的当前线路和防抖状态。独立防抖策略与控制器包含 19 项确定性时间和并发去重检查，并使用本机临时证书完成真实 TLS 握手故障、备用线路认证和矿机流量转发验收。网络恢复策略另有 10 项；检查修复与版本判断 6 项；值守摘要 4 项；管理员授权 5 项；加密接入文件 8 项；换机备份与切换门禁 7 项；签名升级策略 8 项；远程动作白名单与回执 7 项；支持包脱敏和内容 8 项。测试使用唯一临时目录，不写入生产配置或历史，不启动生产界面，也不连接实际 VPS。额外运行恢复及服务组件测试，总计 247 项检查。

`build.ps1 -OutputDirectory <目录>` 可指定构建位置。正式构建继续使用 `-Sign`，签名与发布需独立验收。最后可用配置另存为当前用户 DPAPI 保护的 `last-known-good.dat`，写入时保留一份 `.bak`；读取主文件失败时会停止并报告损坏，不会静默采用备份。“验证并设为可用配置”会在中转停止时检查全部本地端口及所有启用 VPS，全部成功后才保存并晋升快照；验证失败时自动恢复不同且完整性有效的上一份配置。
