# 版本与发布流程

所有组件版本只在仓库根目录 `version.json` 中维护。修改版本后先运行根目录的 `test.ps1` 或 `test.sh`，一致性检查会同时验证程序、测试和 README。

## 发布 Windows 客户端

1. 在 `version.json` 更新 `windows_client`，同步更新 README 的当前版本说明。
2. 完整运行测试，并确认升级和回滚说明仍适用。
3. 在 GitHub 仓库 Actions secrets 配置 `WINDOWS_SIGNING_PFX_BASE64` 和 `WINDOWS_SIGNING_PFX_PASSWORD`。证书必须是专门用于自动发布的代码签名证书。
4. 创建并推送与版本完全相同的标签，例如 `client-v2.2.2`。
5. `Release Windows client` 工作流会重新测试，构建并签名桌面客户端、Windows 服务、服务安装脚本和证书辅助脚本，核对固定发布证书指纹，同时附带不含私钥的公开证书并生成统一 `SHA256SUMS.txt`，随后创建 GitHub Release。

缺少签名 secrets、标签与 `version.json` 不一致、测试失败或签名验证失败时，工作流会停止，不会发布未签名程序。

内部根证书的私钥当前不可导出，不能用于 GitHub Actions。若继续使用内部证书，应在受控的 Windows 发布电脑上运行 `build.ps1 -Sign` 并人工发布；若启用全自动发布，应单独申请可供 CI 安全使用的代码签名证书或接入云签名服务。

## 安装内部发布者证书

证书安装脚本会在写入 Windows 系统级“受信任的根证书颁发机构”和“受信任的发布者”之前显示影响，并要求输入 `INSTALL`。无人值守部署只有在已经通过其他方式核对证书指纹时才可使用 `-Force`。
