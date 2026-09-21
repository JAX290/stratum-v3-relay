using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Net.NetworkInformation;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Win32;

public sealed class MainForm : Form
{
    private readonly TextBox siteName = new TextBox();
    private readonly TextBox server = new TextBox();
    private readonly NumericUpDown tlsPort = new NumericUpDown();
    private readonly NumericUpDown healthCheckMinutes = new NumericUpDown();
    private readonly TextBox serverName = new TextBox();
    private readonly TextBox pin = new TextBox();
    private readonly TextBox token = new TextBox();
    private readonly TextBox listen = new TextBox();
    private readonly TextBox ports = new TextBox();
    private readonly CheckBox autoStart = new CheckBox();
    private readonly CheckBox closeToTray = new CheckBox();
    private readonly TextBox currentIp = new TextBox();
    private readonly ComboBox minerAddress = new ComboBox();
    private readonly Button start = new Button();
    private readonly Button stop = new Button();
    private readonly Button testPrimary = new Button();
    private readonly Button validateConfig = new Button();
    private readonly Button diagnostics = new Button();
    private readonly Button repair = new Button();
    private readonly TextBox logs = new TextBox();
    private readonly NotifyIcon tray = new NotifyIcon();
    private readonly RelayManager manager;
    private readonly NetworkRecoveryMonitor networkRecovery;
    private readonly System.Windows.Forms.Timer ipTimer = new System.Windows.Forms.Timer();
    private readonly System.Windows.Forms.Timer statusTimer = new System.Windows.Forms.Timer();
    private readonly System.Windows.Forms.Timer logTimer = new System.Windows.Forms.Timer();
    private readonly Queue<string> pendingLogs = new Queue<string>();
    private readonly object logLock = new object();
    private readonly Label status = new Label();
    private readonly TabControl pages=new TabControl();
    private readonly TabPage homePage=new TabPage("值守首页"),settingsPage=new TabPage("高级设置（管理员）");
    private readonly AdminAccessPolicy adminAccess=new AdminAccessPolicy();
    private List<ServerProfile> backupProfiles = new List<ServerProfile>();
    private int statusTicks;
    private bool exiting;
    private bool relayRequested;

    public MainForm()
    {
        AppBrand.Apply(this, "");
        Font = new Font("Microsoft YaHei UI", 9F);
        ClientSize = new Size(820, 856);
        MinimumSize = new Size(760, 720);
        StartPosition = FormStartPosition.CenterScreen;
        manager = new RelayManager(Log);
        BuildUi();
        LoadConfig();
        networkRecovery = new NetworkRecoveryMonitor(delegate{return relayRequested;},NetworkHelper.GetLanIPv4,
            delegate(string reason){RunNetworkRecovery(reason);},
            delegate(string phase,string message){manager.SetRecoveryState(phase,message);});
        ports.TextChanged += delegate { RefreshMinerAddresses(false); };
        RefreshMinerAddresses(false);
        ipTimer.Interval = 30000;
        ipTimer.Tick += delegate { RefreshMinerAddresses(false); };
        ipTimer.Start();
        statusTimer.Interval = 1000;
        statusTimer.Tick += delegate { RefreshStatus(); };
        statusTimer.Start();
        logTimer.Interval = 250;
        logTimer.Tick += delegate { DrainLogs(); };
        logTimer.Start();
        tray.Text = AppBrand.Title;
        tray.Icon = Icon;
        tray.Visible = true;
        tray.DoubleClick += delegate { Show(); WindowState = FormWindowState.Normal; Activate(); };
        ContextMenu menu = new ContextMenu();
        menu.MenuItems.Add("显示", delegate { Show(); WindowState = FormWindowState.Normal; Activate(); });
        menu.MenuItems.Add("退出", delegate { exiting = true; Close(); });
        tray.ContextMenu = menu;
        FormClosing += OnClosing;
        Shown += delegate { if (autoStart.Checked) StartRelay(); };
    }

    private void BuildUi()
    {
        pages.Dock=DockStyle.Fill;
        pages.TabPages.Add(homePage);pages.TabPages.Add(settingsPage);
        pages.Selecting+=OnPageSelecting;
        TableLayoutPanel grid = new TableLayoutPanel();
        grid.Dock = DockStyle.Top; grid.Height = 498; grid.Padding = new Padding(18, 14, 18, 4);
        grid.ColumnCount = 2; grid.RowCount = 13;
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 165));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int i=0; i<13; i++) grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        AddRow(grid, 0, "矿场名称", siteName, "例如 一号矿场；用于心跳和离线告警识别");
        testPrimary.Text = "测试主VPS"; testPrimary.AutoSize = true; testPrimary.Click += delegate { TestPrimary(); };
        AddRow(grid, 1, "主 VPS 地址", InlineControls(server, testPrimary), "填写后可测试完整的TCP、TLS证书和共享密钥认证");
        tlsPort.Minimum = 1; tlsPort.Maximum = 65535; tlsPort.Value = 443;
        AddRow(grid, 2, "TLS 端口", tlsPort, "默认 443");
        AddRow(grid, 3, "证书名称（可选）", serverName, "使用正规域名证书时填写域名");
        AddRow(grid, 4, "证书 SHA-256（可选）", pin, "使用 IP/自签名证书时填写 VPS 安装脚本输出的指纹");
        token.UseSystemPasswordChar = true;
        AddRow(grid, 5, "共享密钥", token, "VPS 为这台值守电脑生成的独立密钥");
        AddRow(grid, 6, "本地监听地址", listen, "0.0.0.0 表示接受局域网矿机连接");
        AddRow(grid, 7, "端口或端口映射", ports, "9999 表示同端口；10041=10001 表示本地 10041 转到 VPS 10001");
        healthCheckMinutes.Minimum=1;healthCheckMinutes.Maximum=1440;healthCheckMinutes.Value=5;
        AddRow(grid,8,"VPS自动探测间隔（分钟）",healthCheckMinutes,"每隔多少分钟测试一次所有已启用的主、备用VPS");
        autoStart.Text = "开机自动启动";
        autoStart.AutoSize = true;
        grid.Controls.Add(autoStart, 1, 9);
        closeToTray.Text = "点击关闭最小化";
        closeToTray.AutoSize = true;
        grid.Controls.Add(closeToTray, 1, 10);
        currentIp.ReadOnly = true;
        currentIp.BackColor = Color.White;
        Button refreshIp = new Button(); refreshIp.Text = "刷新"; refreshIp.AutoSize = true; refreshIp.Click += delegate { RefreshMinerAddresses(true); };
        AddRow(grid, 11, "当前局域网 IP", InlineControls(currentIp, refreshIp), "自动识别矿机应连接的值守电脑局域网 IP");
        minerAddress.DropDownStyle = ComboBoxStyle.DropDownList;
        Button copyAddress = new Button(); copyAddress.Text = "复制地址"; copyAddress.AutoSize = true; copyAddress.Click += delegate { CopyMinerAddress(); };
        AddRow(grid, 12, "矿机填写地址", InlineControls(minerAddress, copyAddress), "选择端口后复制完整的 stratum+tcp 地址");
        settingsPage.Controls.Add(grid);

        FlowLayoutPanel settingsButtons = new FlowLayoutPanel();
        settingsButtons.Dock = DockStyle.Top; settingsButtons.Height = 84; settingsButtons.Padding = new Padding(90, 6, 0, 0); settingsButtons.WrapContents = true;
        Button save = new Button(); save.Text = "保存设置"; save.AutoSize = true; save.Click += delegate { SaveConfig(true); };
        validateConfig.Text = "验证并设为可用配置"; validateConfig.AutoSize = true; validateConfig.Click += delegate { ValidateAndPromoteConfig(); };
        Button backups = new Button(); backups.Text = "备用 VPS 设置"; backups.AutoSize = true; backups.Click += delegate { EditBackups(); };
        Button importAccess=new Button();importAccess.Text="导入加密接入文件";importAccess.AutoSize=true;importAccess.Click+=delegate{ImportAccessPackage();};
        Button exportMigration=new Button();exportMigration.Text="导出换机备份";exportMigration.AutoSize=true;exportMigration.Click+=delegate{ExportMigrationBackup();};
        Button importMigration=new Button();importMigration.Text="导入换机备份";importMigration.AutoSize=true;importMigration.Click+=delegate{ImportMigrationBackup();};
        Button miners = new Button(); miners.Text="矿机状态"; miners.AutoSize=true; miners.Click+=delegate{new MinerStatusForm(manager).Show(this);};
        diagnostics.Text="一键诊断"; diagnostics.AutoSize=true; diagnostics.Click+=delegate{RunDiagnostics();};
        repair.Text="检查并修复";repair.AutoSize=true;repair.Click+=delegate{RunCheckAndRepair();};
        Button help = new Button(); help.Text = "各项说明"; help.AutoSize = true; help.Click += delegate { ShowHelp(); };
        start.Text = "启动中转"; start.AutoSize = true; start.Click += delegate { StartRelay(); };
        stop.Text = "停止"; stop.AutoSize = true; stop.Enabled = false; stop.Click += delegate { relayRequested=false;manager.Stop();manager.SetRecoveryState("","");SetRunning(false); };
        settingsButtons.Controls.Add(save); settingsButtons.Controls.Add(validateConfig); settingsButtons.Controls.Add(backups);settingsButtons.Controls.Add(importAccess);settingsButtons.Controls.Add(exportMigration);settingsButtons.Controls.Add(importMigration);settingsButtons.Controls.Add(help);
        settingsPage.Controls.Add(settingsButtons);settingsButtons.BringToFront();

        FlowLayoutPanel dutyButtons=new FlowLayoutPanel();dutyButtons.Dock=DockStyle.Top;dutyButtons.Height=58;dutyButtons.Padding=new Padding(18,10,0,0);
        Button contact=new Button();contact.Text="联系技术人员";contact.AutoSize=true;contact.Click+=delegate{ShowContactSupport();};
        Button admin=new Button();admin.Text="管理员设置";admin.AutoSize=true;admin.Click+=delegate{UnlockAdministrator();};
        dutyButtons.Controls.Add(repair);dutyButtons.Controls.Add(diagnostics);dutyButtons.Controls.Add(miners);dutyButtons.Controls.Add(start);dutyButtons.Controls.Add(stop);dutyButtons.Controls.Add(contact);dutyButtons.Controls.Add(admin);
        homePage.Controls.Add(dutyButtons);

        status.Text = "状态：未启动"; status.Dock = DockStyle.Top; status.Height = 122; status.Padding = new Padding(18, 12, 18, 4);
        status.Font=new Font("Microsoft YaHei UI",11F,FontStyle.Bold);
        status.BackColor = Color.FromArgb(236, 244, 252); status.AutoEllipsis = true;
        homePage.Controls.Add(status); status.BringToFront();

        Label logLabel = new Label(); logLabel.Text = "运行记录"; logLabel.Dock = DockStyle.Top; logLabel.Height = 28; logLabel.Padding = new Padding(18, 5, 0, 0);
        homePage.Controls.Add(logLabel); logLabel.BringToFront();
        logs.Dock = DockStyle.Fill; logs.Multiline = true; logs.ReadOnly = true; logs.ScrollBars = ScrollBars.Vertical;
        logs.BackColor = Color.FromArgb(247, 249, 252); logs.BorderStyle = BorderStyle.FixedSingle; logs.Margin = new Padding(18);
        Panel logPanel = new Panel(); logPanel.Dock = DockStyle.Fill; logPanel.Padding = new Padding(18, 0, 18, 16); logPanel.Controls.Add(logs);
        homePage.Controls.Add(logPanel); logPanel.BringToFront();
        Controls.Add(pages);
    }

    private static void AddRow(TableLayoutPanel grid, int row, string labelText, Control control, string hint)
    {
        Label label = new Label(); label.Text = labelText; label.TextAlign = ContentAlignment.MiddleLeft; label.Dock = DockStyle.Fill;
        ToolTip tip = new ToolTip(); tip.SetToolTip(control, hint);
        control.Dock = DockStyle.Fill; control.Margin = new Padding(3, 4, 3, 4);
        grid.Controls.Add(label, 0, row); grid.Controls.Add(control, 1, row);
    }

    private static Control InlineControls(Control main, Control button)
    {
        TableLayoutPanel panel = new TableLayoutPanel();
        panel.ColumnCount = 2; panel.RowCount = 1; panel.Dock = DockStyle.Fill; panel.Margin = new Padding(0);
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        main.Dock = DockStyle.Fill; main.Margin = new Padding(3, 4, 6, 4);
        button.Margin = new Padding(0, 3, 3, 3);
        panel.Controls.Add(main, 0, 0); panel.Controls.Add(button, 1, 0);
        return panel;
    }

    private void RefreshMinerAddresses(bool writeLog)
    {
        string detected = NetworkHelper.GetLanIPv4();
        currentIp.Text = String.IsNullOrWhiteSpace(detected) ? "未找到局域网 IP" : detected;
        string selected = minerAddress.SelectedItem == null ? "" : minerAddress.SelectedItem.ToString();
        minerAddress.Items.Clear();
        if (!String.IsNullOrWhiteSpace(detected)) {
            try { foreach (PortRoute route in PortRoute.Parse(ports.Text)) minerAddress.Items.Add("stratum+tcp://" + detected + ":" + route.LocalPort); } catch { }
        }
        if (minerAddress.Items.Count > 0) {
            int previous = minerAddress.Items.IndexOf(selected);
            minerAddress.SelectedIndex = previous >= 0 ? previous : 0;
        }
        if (writeLog) Log(String.IsNullOrWhiteSpace(detected) ? "没有找到可用的局域网 IP，请检查网线或 Wi-Fi。" : "已刷新局域网 IP：" + detected);
    }

    private void CopyMinerAddress()
    {
        if (minerAddress.SelectedItem == null) { MessageBox.Show(this, "当前没有可复制的矿机地址。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Information); return; }
        string value = minerAddress.SelectedItem.ToString();
        Clipboard.SetText(value);
        Log("已复制矿机填写地址：" + value);
    }

    private void LoadConfig()
    {
        AppConfig c = ConfigStore.Load();
        ApplyConfig(c);
        Log("请填写 VPS 安装脚本输出的设置，然后启动中转。");
    }

    private void ApplyConfig(AppConfig c)
    {
        c.Normalize();
        ServerProfile primary = c.Servers[0];
        siteName.Text=c.SiteName; server.Text = primary.Address; tlsPort.Value = Math.Max(1, Math.Min(65535, primary.Port)); serverName.Text = primary.ServerName;
        pin.Text = primary.CertificateSha256; token.Text = primary.SharedKey; listen.Text = c.ListenAddress; ports.Text = c.Ports; healthCheckMinutes.Value=c.HealthCheckMinutes; autoStart.Checked = c.AutoStart; closeToTray.Checked = c.CloseToTray;
        backupProfiles.Clear(); backupProfiles.Add(c.Servers[1].Copy()); backupProfiles.Add(c.Servers[2].Copy());
    }

    private AppConfig CurrentConfig()
    {
        AppConfig c = new AppConfig { SiteName=siteName.Text.Trim(), ListenAddress=listen.Text.Trim(), Ports=ports.Text.Trim(), HealthCheckMinutes=(int)healthCheckMinutes.Value, AutoStart=autoStart.Checked, CloseToTray=closeToTray.Checked };
        c.Servers.Add(new ServerProfile { Name="主VPS", Enabled=true, Address=server.Text.Trim(), Port=(int)tlsPort.Value, ServerName=serverName.Text.Trim(), CertificateSha256=pin.Text.Trim(), SharedKey=token.Text.Trim() });
        foreach (ServerProfile p in backupProfiles) c.Servers.Add(p.Copy());
        return c;
    }

    private List<PortRoute> ValidateSettings()
    {
        AppConfig c = CurrentConfig();
        if (String.IsNullOrWhiteSpace(c.SiteName)) throw new InvalidOperationException("请填写矿场名称，便于离线告警识别。");
        int enabled=0;
        foreach (ServerProfile p in c.Servers) if (p.Enabled) { enabled++; ValidateProfile(p); }
        if (enabled == 0) throw new InvalidOperationException("至少启用一个 VPS。");
        return PortRoute.Parse(ports.Text);
    }

    private static void ValidateProfile(ServerProfile p) {
        CompleteConfigurationValidator.ValidateProfile(p);
    }

    private void SaveConfig(bool showMessage)
    {
        ValidateSettings();
        AppConfig c = CurrentConfig(); ConfigStore.Save(c); SetAutoStart(c.AutoStart);
        if (showMessage) Log("设置已保存。共享密钥已使用当前 Windows 用户加密保存。");
    }

    private void StartRelay()
    {
        try { List<PortRoute> routePorts = ValidateSettings(); SaveConfig(false); manager.Start(CurrentConfig(), routePorts); relayRequested=true;SetRunning(true); }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, "无法启动", MessageBoxButtons.OK, MessageBoxIcon.Warning); Log("启动失败：" + ex.Message); }
    }

    private void RunNetworkRecovery(string reason)
    {
        if(IsDisposed||Disposing)return;
        if(InvokeRequired){Invoke(new Action<string>(RunNetworkRecovery),reason);return;}
        if(!relayRequested)return;
        AppConfig config=CurrentConfig();List<PortRoute> routePorts=PortRoute.Parse(config.Ports);
        manager.Stop();
        try{manager.Start(config,routePorts);SetRunning(true);RefreshMinerAddresses(false);}
        catch{SetRunning(false);throw;}
    }

    private async void ValidateAndPromoteConfig()
    {
        AppConfig config = null;
        try {
            config = CurrentConfig();
            validateConfig.Enabled = false; validateConfig.Text = "完整验证中…";
            Log("开始完整验证本地端口以及所有已启用 VPS 的证书、密钥和连通性。");
            CompleteConfigurationValidator validator = new CompleteConfigurationValidator();
            ConfigurationValidationEvidence evidence = await validator.ValidateAsync(config,
                delegate(ServerProfile profile, string name, CancellationToken cancellation) {
                    return manager.TestProfileAsync(profile, name, cancellation);
                }, CancellationToken.None);
            if (IsDisposed || Disposing) return;
            ConfigStore.Save(config);
            SetAutoStart(config.AutoStart);
            new LastKnownGoodStore(ConfigStore.Folder).Promote(config, evidence);
            Log("完整验证通过，当前设置已保存为最后可用配置。");
            MessageBox.Show(this, "完整配置验证通过。\r\n\r\n本地监听端口：通过\r\n所有已启用 VPS：证书、共享密钥和连通性均通过\r\n\r\n已保存为最后可用配置。", "验证成功", MessageBoxButtons.OK, MessageBoxIcon.Information);
        } catch (Exception ex) {
            if (IsDisposed || Disposing) return;
            Log("完整配置验证失败：" + ex.Message);
            string rollback = config == null ? "无法读取当前设置，未执行自动回退。" : RestoreLastKnownGood(config);
            MessageBox.Show(this, "当前设置未保存为最后可用配置。\r\n\r\n" + ex.Message + "\r\n\r\n" + rollback, "验证失败", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        } finally {
            if (!IsDisposed && !Disposing) { validateConfig.Enabled = !manager.IsRunning; validateConfig.Text = "验证并设为可用配置"; RefreshStatus(); }
        }
    }

    private string RestoreLastKnownGood(AppConfig failedConfig)
    {
        try {
            ConfigurationRollbackCoordinator coordinator = new ConfigurationRollbackCoordinator();
            ConfigurationRollbackResult result = coordinator.RestoreIfDifferent(failedConfig,
                new LastKnownGoodStore(ConfigStore.Folder), delegate(AppConfig restored) {
                    ConfigStore.Save(restored); SetAutoStart(restored.AutoStart);
                });
            if (result.AlreadyCurrent) {
                Log("验证失败；当前设置与最后可用配置相同，因此未重复回退。");
                return "当前设置与最后可用配置相同，未重复回退。";
            }
            ApplyConfig(result.Config);
            string message = "已自动恢复到 " + result.ValidatedUtc.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") + " 验证通过的最后可用配置。";
            Log(message);
            return message;
        } catch (FileNotFoundException) {
            Log("验证失败，尚无最后可用配置可供自动回退。");
            return "尚无最后可用配置，未执行自动回退。";
        } catch (Exception ex) {
            Log("自动回退失败：" + ex.Message);
            return "自动回退失败：" + ex.Message;
        }
    }

    private async void TestPrimary()
    {
        ServerProfile profile=CurrentConfig().Servers[0];
        try {
            ValidateProfile(profile); testPrimary.Enabled=false; testPrimary.Text="测试中…";
            EndpointState result=await manager.TestProfileAsync(profile,siteName.Text.Trim(),CancellationToken.None);if(IsDisposed||Disposing)return;
            Log("主VPS测试成功：TCP、TLS证书和共享密钥均正常，延迟 "+result.LatencyMs+" ms。");
            MessageBox.Show(this,"主VPS连接正常。\r\n\r\nTLS证书验证：通过\r\n共享密钥认证：通过\r\n响应时间："+result.LatencyMs+" ms","测试成功",MessageBoxButtons.OK,MessageBoxIcon.Information);
        } catch(Exception ex) { if(IsDisposed||Disposing)return;Log("主VPS测试失败："+ex.Message); MessageBox.Show(this,"主VPS连接失败。\r\n\r\n"+ex.Message,"测试失败",MessageBoxButtons.OK,MessageBoxIcon.Warning); }
        finally { if(!IsDisposed&&!Disposing){testPrimary.Enabled=true; testPrimary.Text="测试主VPS"; RefreshStatus();} }
    }

    private void SetRunning(bool running)
    {
        start.Enabled = !running; stop.Enabled = running; validateConfig.Enabled = !running;
        siteName.Enabled = server.Enabled = tlsPort.Enabled = serverName.Enabled = pin.Enabled = token.Enabled = listen.Enabled = ports.Enabled = healthCheckMinutes.Enabled = !running;
    }

    private void SetAutoStart(bool enabled)
    {
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Run", true)) {
            key.DeleteValue("StratumSecureRelay", false);
            if (enabled) key.SetValue("木林森中转", "\"" + Application.ExecutablePath + "\""); else key.DeleteValue("木林森中转", false);
        }
    }

    private void ShowHelp()
    {
        string message =
            "矿场名称：这台电脑所在矿场的名字，会显示在离线告警中。\r\n\r\n" +
            "主 VPS 地址：正常情况下优先使用的云服务器公网 IP。\r\n\r\n" +
            "TLS 端口：加密入口，通常使用 443。\r\n\r\n" +
            "证书名称：有正规域名证书时填写域名；没有域名可以留空。\r\n\r\n" +
            "证书 SHA-256：没有正规域名证书时，填写 VPS 安装脚本给出的指纹，用来确认连到的是自己的服务器。\r\n\r\n" +
            "共享密钥：相当于电脑和 VPS 之间的密码。\r\n\r\n" +
            "本地监听地址：保持 0.0.0.0，局域网矿机才能连接。\r\n\r\n" +
            "端口或端口映射：只填 9999 时两端都用 9999；填 10041=10001 时，矿机连接本机 10041，VPS 按 10001 路线转发。\r\n\r\n" +
            "备用 VPS：主 VPS 不通时按顺序自动使用。主 VPS 恢复后，后续新连接自动优先使用主 VPS。\r\n\r\n" +
            "VPS自动探测间隔：中转运行时，按这个分钟数逐一验证主、备用VPS的TCP、TLS证书和共享密钥。\r\n\r\n" +
            "当前局域网 IP：值守电脑在矿机局域网里的地址。\r\n\r\n" +
            "矿机填写地址：已经补全的挖矿地址，选择后可以直接复制到矿机后台。";
        message += "\r\n\r\n矿机状态：按局域网 IP 合并显示连接、Worker、Share、响应时间和估算算力。双击矿机可查看检修详情。";
        MessageBox.Show(this, message, "各项设置说明", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    private void ShowContactSupport()
    {
        MessageBox.Show(this,"请先点击“一键诊断”，在报告窗口复制或另存报告，再发送给维护人员。\r\n\r\n诊断报告会排除共享密钥、证书私钥和矿池密码。","联系技术人员",MessageBoxButtons.OK,MessageBoxIcon.Information);
    }

    private void OnPageSelecting(object sender,TabControlCancelEventArgs args)
    {
        if(args.TabPage!=settingsPage||adminAccess.CanAccess(DateTime.UtcNow))return;args.Cancel=true;UnlockAdministrator();
    }

    private void UnlockAdministrator()
    {
        if(adminAccess.CanAccess(DateTime.UtcNow)){pages.SelectedTab=settingsPage;return;}
        using(AdminLoginForm form=new AdminLoginForm())if(form.ShowDialog(this)==DialogResult.OK){adminAccess.Unlock(DateTime.UtcNow,TimeSpan.FromMinutes(15));Log("高级设置已由 Windows 管理员解锁 15 分钟。");pages.SelectedTab=settingsPage;}
    }

    private void EditBackups()
    {
        using (BackupForm form = new BackupForm(backupProfiles,manager,siteName.Text.Trim())) if (form.ShowDialog(this) == DialogResult.OK) backupProfiles = form.Profiles;
    }

    private async void ImportAccessPackage()
    {
        using(OpenFileDialog file=new OpenFileDialog()){file.Filter="木林森加密接入文件|*.msrelay|所有文件|*.*";file.Title="选择加密接入文件";if(file.ShowDialog(this)!=DialogResult.OK)return;
            using(AccessPackageCodeForm code=new AccessPackageCodeForm()){if(code.ShowDialog(this)!=DialogResult.OK)return;
                try{string json=File.ReadAllText(file.FileName,Encoding.UTF8);AccessPackageData package=AccessPackageCodec.Decrypt(json,code.ImportCode,DateTime.UtcNow);UsedAccessPackageStore used=new UsedAccessPackageStore(ConfigStore.Folder);if(used.IsUsed(package.PackageId))throw new InvalidOperationException("这个一次性接入文件已经导入过。");AppConfig imported=AccessPackageCodec.Apply(CurrentConfig(),package);CompleteConfigurationValidator validator=new CompleteConfigurationValidator();ConfigurationValidationEvidence evidence=await validator.ValidateAsync(imported,delegate(ServerProfile profile,string name,CancellationToken cancellation){return manager.TestProfileAsync(profile,name,cancellation);},CancellationToken.None);used.MarkUsed(package.PackageId);ConfigStore.Save(imported);new LastKnownGoodStore(ConfigStore.Folder).Promote(imported,evidence);ApplyConfig(imported);Log("加密接入文件已导入并通过完整验证："+package.SiteName+"。");MessageBox.Show(this,"接入资料已自动填写并通过本地端口、TLS 证书、共享密钥和 VPS 连通性验证。\r\n\r\n此一次性文件已在本机标记为使用。","导入成功",MessageBoxButtons.OK,MessageBoxIcon.Information);}
                catch(Exception ex){Log("加密接入文件导入失败："+ex.Message);MessageBox.Show(this,"接入文件没有导入。\r\n\r\n"+ex.Message,"导入失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
            }
        }
    }

    private void ExportMigrationBackup()
    {
        try{
            AppConfig config=CurrentConfig();
            using(MigrationPasswordForm password=new MigrationPasswordForm(true)){if(password.ShowDialog(this)!=DialogResult.OK)return;
                using(SaveFileDialog file=new SaveFileDialog()){file.Filter="木林森换机备份|*.msbackup";file.DefaultExt="msbackup";file.AddExtension=true;file.FileName="木林森中转换机备份-"+DateTime.Now.ToString("yyyyMMdd-HHmm")+".msbackup";if(file.ShowDialog(this)!=DialogResult.OK)return;
                    string package=MigrationBackupCodec.Export(config,password.MigrationPassword,NetworkHelper.GetLanIPv4(),DateTime.UtcNow,72);string temporary=file.FileName+".tmp";File.WriteAllText(temporary,package,Encoding.UTF8);if(File.Exists(file.FileName))File.Replace(temporary,file.FileName,null);else File.Move(temporary,file.FileName);Log("换机备份已加密导出："+file.FileName);MessageBox.Show(this,"换机备份已加密保存，有效期 72 小时。\r\n\r\n请把备份文件和迁移密码分开传给新电脑。新电脑完成全部验证前，不要修改矿机地址。","导出完成",MessageBoxButtons.OK,MessageBoxIcon.Information);
                }
            }
        }catch(Exception ex){Log("换机备份导出失败："+ex.Message);MessageBox.Show(this,ex.Message,"无法导出换机备份",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
    }

    private async void ImportMigrationBackup()
    {
        if(manager.IsRunning){MessageBox.Show(this,"请先停止这台新电脑上的中转，再导入换机备份，以便验证本地端口没有被占用。","请先停止中转",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}
        using(OpenFileDialog file=new OpenFileDialog()){file.Filter="木林森换机备份|*.msbackup|所有文件|*.*";file.Title="选择旧电脑导出的换机备份";if(file.ShowDialog(this)!=DialogResult.OK)return;
            using(MigrationPasswordForm password=new MigrationPasswordForm(false)){if(password.ShowDialog(this)!=DialogResult.OK)return;
                try{
                    MigrationBackupData backup=MigrationBackupCodec.Import(File.ReadAllText(file.FileName,Encoding.UTF8),password.MigrationPassword,DateTime.UtcNow);CompleteConfigurationValidator validator=new CompleteConfigurationValidator();Log("开始在新电脑验证迁移配置的端口、证书、共享密钥和 VPS 连通性。");ConfigurationValidationEvidence evidence=await validator.ValidateAsync(backup.Config,delegate(ServerProfile profile,string name,CancellationToken cancellation){return manager.TestProfileAsync(profile,name,cancellation);},CancellationToken.None);MigrationSwitchPlan plan=MigrationSwitchPlan.Create(backup,NetworkHelper.GetLanIPv4(),evidence);ConfigStore.Save(backup.Config);SetAutoStart(backup.Config.AutoStart);new LastKnownGoodStore(ConfigStore.Folder).Promote(backup.Config,evidence);ApplyConfig(backup.Config);
                    StringBuilder addresses=new StringBuilder();foreach(string address in plan.MinerAddresses)addresses.AppendLine(address);Log("换机备份导入并完整验证通过，新电脑局域网 IP："+plan.NewLanIp);MessageBox.Show(this,"新电脑迁移验证全部通过。\r\n\r\n本地端口：通过\r\nVPS 证书：通过\r\n共享密钥：通过\r\nVPS 连通性：通过\r\n\r\n旧电脑 IP："+(String.IsNullOrWhiteSpace(plan.OldLanIp)?"未记录":plan.OldLanIp)+"\r\n新电脑矿机地址：\r\n"+addresses+"\r\n现在可以启动新电脑中转，停止旧电脑中转，再逐台切换矿机地址。切换完成后请安全删除迁移备份。","可以开始换机",MessageBoxButtons.OK,MessageBoxIcon.Information);
                }catch(Exception ex){Log("换机备份导入失败："+ex.Message);MessageBox.Show(this,"迁移没有生效，矿机地址不要切换。\r\n\r\n"+ex.Message,"迁移验证失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
            }
        }
    }

    private async void RunDiagnostics()
    {
        diagnostics.Enabled=false;diagnostics.Text="诊断中…";Log("开始一键诊断，请稍候。");
        StringBuilder report=new StringBuilder();List<string> problems=new List<string>();
        try {
            AppConfig config=CurrentConfig();RelaySnapshot snapshot=manager.Snapshot();List<MinerSnapshot> miners=manager.MinerSnapshots();
            report.AppendLine("木林森中转一键诊断报告");report.AppendLine("生成时间："+DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));report.AppendLine("程序版本：v"+AppBrand.Version);report.AppendLine("矿场名称："+(config.SiteName.Length==0?"未填写":config.SiteName));report.AppendLine();
            report.AppendLine("【电脑与局域网】");report.AppendLine("Windows："+Environment.OSVersion);report.AppendLine("电脑连续运行："+FormatDuration(SystemStatus.Uptime));report.AppendLine("处理器线程："+Environment.ProcessorCount);report.AppendLine("程序内存："+FormatBytes(Environment.WorkingSet));string lan=NetworkHelper.GetLanIPv4();report.AppendLine("当前局域网IP："+(lan.Length==0?"未找到":lan));if(lan.Length==0)problems.Add("没有找到矿机局域网IP，请检查网线、网卡和IP设置。");
            try{List<PortRoute> routes=PortRoute.Parse(config.Ports);report.AppendLine("本地监听端口："+String.Join("，",routes.ConvertAll(delegate(PortRoute r){return r.LocalPort.ToString();}).ToArray()));if(!snapshot.Running)problems.Add("中转目前没有启动，矿机无法通过这台电脑连接VPS。");}
            catch(Exception ex){report.AppendLine("本地端口：配置有误（"+ex.Message+"）");problems.Add("本地端口配置有误，需要先修正设置。");}
            report.AppendLine();report.AppendLine("【当前生产状态】");report.AppendLine("中转状态："+(snapshot.Running?"运行中":"未启动"));report.AppendLine("在线矿机："+snapshot.ActiveMiners);report.AppendLine("当前连接："+snapshot.Active);report.AppendLine("累计失败："+snapshot.Failures);report.AppendLine("传输流量：上传 "+FormatBytes(snapshot.Uploaded)+" / 下载 "+FormatBytes(snapshot.Downloaded));
            int warning=0,offline=0;foreach(MinerSnapshot miner in miners){if(miner.Connections==0)offline++;else if(miner.Health<85)warning++;}report.AppendLine("需要注意的在线矿机："+warning);report.AppendLine("历史离线记录："+offline);if(snapshot.Running&&snapshot.ActiveMiners==0)problems.Add("中转正在运行，但目前没有矿机连接这台电脑。");if(warning>0)problems.Add("有 "+warning+" 台在线矿机健康度偏低，请打开“矿机状态”查看。");
            report.AppendLine();report.AppendLine("【主、备用VPS检测】");int usable=0;foreach(ServerProfile profile in config.Servers){if(profile==null||!profile.Enabled)continue;try{ValidateProfile(profile);EndpointState result=await manager.TestProfileAsync(profile,config.SiteName,CancellationToken.None);usable++;report.AppendLine(profile.Name+"：正常，响应 "+result.LatencyMs+" ms，地址 "+profile.Address+":"+profile.Port);}catch(Exception ex){report.AppendLine(profile.Name+"：失败，"+ex.Message);problems.Add(profile.Name+"当前无法通过完整的TCP、TLS和共享密钥检测。");}}
            if(IsDisposed||Disposing)return;if(usable==0)problems.Add("没有任何一台VPS检测成功，矿机的新连接将无法建立。");
            report.AppendLine();report.AppendLine("【管理员结论】");if(problems.Count==0){report.AppendLine("当前未发现明显问题，可以继续运行。");}else{for(int i=0;i<problems.Count;i++)report.AppendLine((i+1)+". "+problems[i]);}
            report.AppendLine();report.AppendLine("说明：报告不包含共享密钥、证书私钥或矿池密码，可以复制给维护人员排查。");
            Directory.CreateDirectory(ConfigStore.Folder);string path=Path.Combine(ConfigStore.Folder,"diagnostic-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".txt");File.WriteAllText(path,report.ToString(),Encoding.UTF8);Log("一键诊断完成，报告已保存："+path);using(DiagnosticReportForm form=new DiagnosticReportForm(report.ToString(),path))form.ShowDialog(this);
        } catch(Exception ex){if(IsDisposed||Disposing)return;Log("一键诊断失败："+ex.Message);MessageBox.Show(this,"无法完成诊断。\r\n\r\n"+ex.Message,"诊断失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
        finally{if(!IsDisposed&&!Disposing){diagnostics.Enabled=true;diagnostics.Text="一键诊断";RefreshStatus();}}
    }

    private async void RunCheckAndRepair()
    {
        repair.Enabled=false;repair.Text="检查修复中…";StringBuilder report=new StringBuilder();report.AppendLine("木林森中转检查并修复");report.AppendLine("时间："+DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));report.AppendLine();
        try{
            AppConfig config=CurrentConfig();List<ClientHealthIssue> issues=ClientHealthInspector.Inspect(config,manager,Application.ExecutablePath);
            ClientHealthIssue configuration=FindIssue(issues,"CONFIG");
            if(configuration!=null){report.AppendLine("发现："+configuration.Title+"。"+configuration.Detail);string result=RestoreLastKnownGood(config);report.AppendLine("处理："+result);config=CurrentConfig();issues=ClientHealthInspector.Inspect(config,manager,Application.ExecutablePath);}
            bool primaryOk=false;ServerProfile verifiedBackup=null;
            for(int index=0;index<config.Servers.Count;index++){
                ServerProfile profile=config.Servers[index];if(profile==null||!profile.Enabled)continue;
                try{ValidateProfile(profile);await manager.TestProfileAsync(profile,config.SiteName,CancellationToken.None);report.AppendLine("线路："+profile.Name+" 验证正常。");if(index==0)primaryOk=true;else if(verifiedBackup==null)verifiedBackup=profile;}
                catch(Exception ex){report.AppendLine("线路："+profile.Name+" 异常，"+ex.Message);}
            }
            if(!primaryOk&&verifiedBackup!=null)report.AppendLine("发现：主线路不可用，"+verifiedBackup.Name+" 验证正常。");
            ClientHealthIssue firewall=FindIssue(issues,"FIREWALL");
            if(firewall!=null){try{FirewallRuleManager.EnsureRule(Application.ExecutablePath,PortRoute.Parse(config.Ports));report.AppendLine("已修复：Windows 私有网络防火墙入站规则已补齐。");}catch(Exception ex){report.AppendLine("未修复：防火墙规则需要管理员授权。"+ex.Message);}}
            ClientHealthIssue port=FindIssue(issues,"PORT");if(port!=null)report.AppendLine("需人工："+port.Title+"。"+port.Detail);
            if((FindIssue(issues,"STOPPED")!=null||FindIssue(issues,"LISTENER")!=null)&&port==null){manager.Stop();manager.Start(config,PortRoute.Parse(config.Ports));relayRequested=true;SetRunning(true);report.AppendLine("已修复：本地监听和中转进程已重新启动。");}
            if(!primaryOk&&verifiedBackup!=null){manager.SelectVerifiedEndpoint(verifiedBackup,false);report.AppendLine("已修复：后续新连接已改用 "+verifiedBackup.Name+"。");}
            try{LatestClientRelease latest=await LatestClientReleaseChecker.CheckAsync(CancellationToken.None);if(latest.IsNewerThan(AppBrand.Version))report.AppendLine("版本：发现新版 "+latest.Version+"，下载地址 "+latest.Url);else report.AppendLine("版本：当前 "+AppBrand.Version+" 已是最新版。 ");}catch(Exception ex){report.AppendLine("版本：暂时无法联网核对（"+ex.Message+"），不影响本地修复。 ");}
            List<ClientHealthIssue> remaining=ClientHealthInspector.Inspect(config,manager,Application.ExecutablePath);report.AppendLine();report.AppendLine("复查结论："+(remaining.Count==0?"可自动处理的项目均已恢复正常。":"仍有 "+remaining.Count+" 项需要查看。"));foreach(ClientHealthIssue issue in remaining)report.AppendLine("- "+issue.Title+"："+issue.Detail);
            Log("检查并修复完成。");using(DiagnosticReportForm form=new DiagnosticReportForm(report.ToString(),""))form.ShowDialog(this);
        }catch(Exception ex){Log("检查并修复失败："+ex.Message);MessageBox.Show(this,"检查并修复未完成。\r\n\r\n"+ex.Message,"修复失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
        finally{if(!IsDisposed&&!Disposing){repair.Enabled=true;repair.Text="检查并修复";RefreshStatus();}}
    }

    private static ClientHealthIssue FindIssue(List<ClientHealthIssue> issues,string code){foreach(ClientHealthIssue issue in issues)if(issue.Code==code)return issue;return null;}

    private void RefreshStatus()
    {
        if(pages.SelectedTab==settingsPage&&!adminAccess.CanAccess(DateTime.UtcNow)){adminAccess.Lock();pages.SelectedTab=homePage;Log("管理员授权已到期，已返回值守员模式。");}
        if(++statusTicks>=30){statusTicks=0;manager.SaveMinerHistory();}
        RelaySnapshot s=manager.Snapshot();
        DutyHomeSummary duty=DutyHomeSummary.Create(s,manager.MinerSnapshots());
        string line=s.Running ? "运行中" : "未启动";
        string uptime=s.Running ? FormatDuration(DateTime.Now-s.StartedAt) : "--";
        List<string> endpoints=new List<string>(); foreach(EndpointState e in s.Endpoints) { string phase=e.Recovering?"恢复观察":(e.CooldownUntilUtc>DateTime.UtcNow?"冷却至 "+e.CooldownUntilUtc.ToLocalTime().ToString("HH:mm:ss"):(e.Online?"正常 "+e.LatencyMs+"ms":"异常 "+e.LastError));endpoints.Add((e.Selected?"当前·":"")+e.Name+":"+phase+"（"+(e.LastCheck==DateTime.MinValue?"未检测":e.LastCheck.ToString("HH:mm:ss"))+"）"); }
        start.Enabled=!manager.IsRunning;
        string recovery=String.IsNullOrWhiteSpace(s.RecoveryPhase)?"":("\r\n网络恢复："+s.RecoveryPhase+" · "+s.RecoveryMessage);
        status.Text="总状态："+duty.Overall+"    受影响矿机："+duty.AffectedMiners+" 台\r\n"+duty.Guidance+"\r\n运行："+line+"    当前连接："+s.Active+"    累计连接："+s.Total+"    失败："+s.Failures+"    时长："+uptime+"\r\n线路："+(endpoints.Count==0 ? "启动后自动检测" : String.Join("，",endpoints.ToArray()))+recovery;
    }
    private static string FormatDuration(TimeSpan t){ return ((int)t.TotalDays>0 ? ((int)t.TotalDays)+"天 " : "")+t.Hours.ToString("00")+":"+t.Minutes.ToString("00")+":"+t.Seconds.ToString("00"); }
    private static string FormatBytes(long value){ string[] u={"B","KB","MB","GB","TB"}; double n=value; int i=0; while(n>=1024&&i<u.Length-1){n/=1024;i++;} return n.ToString(i==0?"0":"0.0")+" "+u[i]; }

    private void Log(string message)
    {
        string line=DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")+"  "+message+Environment.NewLine;
        lock(logLock){if(pendingLogs.Count>=5000)pendingLogs.Dequeue();pendingLogs.Enqueue(line);}
    }

    private void DrainLogs()
    {
        StringBuilder batch=new StringBuilder();lock(logLock){int count=Math.Min(500,pendingLogs.Count);for(int i=0;i<count;i++)batch.Append(pendingLogs.Dequeue());}
        if(batch.Length==0||IsDisposed||Disposing)return;
        const int maximum=524288,keep=393216;if(logs.TextLength+batch.Length>maximum){int remove=Math.Max(0,logs.TextLength-keep);if(remove>0){logs.Select(0,remove);logs.SelectedText="";}}
        logs.AppendText(batch.ToString());
    }

    private void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (!exiting && closeToTray.Checked && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; Hide(); tray.ShowBalloonTip(1500, "木林森中转", "程序仍在后台运行。", ToolTipIcon.Info); return; }
        relayRequested=false;networkRecovery.Dispose();ipTimer.Stop(); statusTimer.Stop(); logTimer.Stop(); manager.SaveMinerHistory(); manager.Stop(); tray.Visible = false;
    }
}
