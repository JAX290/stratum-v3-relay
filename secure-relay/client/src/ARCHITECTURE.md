# Windows 客户端源码结构

WIN-D07 将现有逻辑按职责分文件，生产程序仍编译为单个 EXE，配置和网络协议不变。

源码和脚本现位于 client/src。运行本目录 build.ps1 时，正式 EXE 默认输出到上一级 client；历史 EXE 保留在 client 外层。仓库根目录的测试入口和 CI 已同步调整。

WIN-P01 已开始，当前单独验收恢复策略内核，尚未接入生产程序；边界与后续验收见 [服务阶段说明](SERVICE-DESIGN.md)。

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
| `MainForm.cs` | 主窗口和当前诊断工作流 |
| `DiagnosticReportForm.cs` / `MinerStatusForm.cs` | 诊断报告 / 矿机列表 |
| `BackupForm.cs` | 备用 VPS 窗口及服务器设置控件 |

核心不依赖 WinForms、Drawing 或窗口类。AppBrand 的界面方法通过 partial 类留在界面文件中，核心仅使用版本和名称。状态访问继续使用 RelayManager 原有锁；界面读取快照，历史格式和 DPAPI 用户作用域不变。未来 Windows 服务不能直接假定能读取另一用户的 DPAPI 配置，需在 WIN-P01 单独设计安装和迁移流程。

## 构建与验证

执行 `./test-client.ps1`，在唯一临时目录中编译完整客户端并运行回归，再不引用 WinForms/Drawing 编译核心库并执行同一套回归。输出路径会显示在末尾，不覆盖客户端目录中已有 EXE。

25 项检查覆盖配置兼容、端口映射、Share 接受和拒绝、分包解析、重复响应、历史窗口、快照隔离、在线矿机删除保护、端口冲突后的监听清理及重复停止。测试不写入生产配置或历史，不启动生产界面，也不连接实际 VPS。

`build.ps1 -OutputDirectory <目录>` 可指定构建位置。正式构建继续使用 `-Sign`，签名与发布需独立验收。此阶段没有更改配置格式；发布后需要退回旧版时，退出客户端并恢复上一份已验证的正式 EXE，保留原配置。切换 EXE 会暂时中断该电脑负责的中转，应先安排维护窗口或备用电脑。
