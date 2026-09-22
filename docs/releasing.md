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
