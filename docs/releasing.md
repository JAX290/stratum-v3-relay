# 版本与发布流程

所有组件版本只在仓库根目录 `version.json` 中维护。修改版本后先运行根目录的 `test.ps1` 或 `test.sh`，一致性检查会同时验证程序、测试和 README。

## 发布 Windows 客户端

1. 在 `version.json` 更新 `windows_client`，同步更新 README 的当前版本说明。
2. 完整运行测试，并确认升级和回滚说明仍适用。
3. 只有准备启用云端自动签名时，才在 GitHub 仓库 Actions secrets 配置 `WINDOWS_SIGNING_PFX_BASE64` 和 `WINDOWS_SIGNING_PFX_PASSWORD`，并把仓库 Actions variable `ENABLE_CLOUD_SIGNING` 设为 `true`。证书必须是专门用于自动发布的代码签名证书。
4. 创建并推送与版本完全相同的标签，例如 `client-v2.2.2`。
5. 当云端签名开关启用时，`Release Windows client` 工作流会重新测试，构建并签名桌面客户端、Windows 服务、服务安装脚本和证书辅助脚本，核对固定发布证书指纹，同时附带不含私钥的公开证书并生成统一 `SHA256SUMS.txt`，随后创建 GitHub Release。开关未启用时，标签推送只会跳过该任务，不会产生一条已知必败的发布记录。

云端签名开关启用后，缺少签名 secrets、标签与 `version.json` 不一致、测试失败或签名验证失败时，工作流会停止，不会发布未签名程序。

内部根证书的私钥当前不可导出，不能用于 GitHub Actions。当前标准流程是在受控的 Windows 发布电脑上完整运行 `test-client.ps1`，使用 `build.ps1 -Sign` 和 `build-service.ps1 -Sign` 构建，逐个核对 Authenticode 状态、固定指纹、文件版本和 SHA-256 后再人工发布。若启用全自动发布，应单独申请可供 CI 安全使用的代码签名证书或接入云签名服务，然后再打开上述变量。

## 安装内部发布者证书

证书安装脚本会在写入 Windows 系统级“受信任的根证书颁发机构”和“受信任的发布者”之前显示影响，并要求输入 `INSTALL`。无人值守部署只有在已经通过其他方式核对证书指纹时才可使用 `-Force`。

## Defender 误报与值守电脑初始化

- 2.3.15 起客户端会从 VPS 下载升级包，Windows Defender 的机器学习规则会把它判为
  `Trojan:Script/Conteban.A!ml`（`!ml` 表示按行为判定，不是特征匹配）并隔离，
  文件会被移出原目录，表现为"程序自己停了、文件也不见了"。
- 因此发布包附带单文件 `watch-pc-setup-<版本>.bat`（批处理 + 内嵌 PowerShell，由 `build-setup.ps1` 生成，不要手工编辑生成的 .bat）：双击这个文件，
- 同一个文件也发布到 VPS 闸门的 releases 目录，访问裸链接 `https://status1.mulinsen.win/exe` 直接下载它；`/exe/manifest` 与 `/exe/<安装包>` 仍供自动升级使用。工具本身不计次，只有它下载安装包时才消耗闸门额度。
  脚本会申请管理员权限，把 `C:\MulinSenRelay` 与配置目录加入 Defender 排除，
  尝试恢复被隔离的文件，再从 VPS 下载当前版本、校验 SHA-256 后启动。
  每台值守电脑只需运行一次，之后升级不再需要它。生成单文件时注意：批处理头部必须纯 ASCII，PowerShell 主体按 UTF-8 读取，整个文件保存为无 BOM、CRLF。
- `install-service-2.3.16.ps1` 在安装服务程序时同样写入排除，服务版部署无需额外步骤。
- 长期方案是向 Microsoft 提交误报（WDSA 文件提交），加白后所有机器都不再需要排除。
