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
    // 首页分区展示：状态条 + 指标卡片 + 端口 + 线路，避免把一切塞进一个 Label 导致折叠截断
    private readonly Panel bannerBar = new Panel();
    private readonly Label bannerTitle = new Label();
    private readonly Label bannerDetail = new Label();
    private readonly Label cardActive = new Label();
    private readonly Label cardTotal = new Label();
    private readonly Label cardFailures = new Label();
    private readonly Label cardUptime = new Label();
    private readonly FlowLayoutPanel portStats = new FlowLayoutPanel();
    private readonly Label portList = new Label();
    private readonly Label portFailed = new Label();
    private readonly FlowLayoutPanel endpointList = new FlowLayoutPanel();
    private string portSignature = "", endpointSignature = "";
    private readonly TabControl pages=new TabControl();
    private readonly TabPage homePage=new TabPage("值守首页"),settingsPage=new TabPage("设置");
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
        manager.RemoteActionRequested+=HandleRemoteAction;
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
        Button checkUpdate=new Button();checkUpdate.Text="检查更新";checkUpdate.AutoSize=true;checkUpdate.Click+=delegate{CheckForUpdates(checkUpdate);};
        Button updateClient=new Button();updateClient.Text="一键升级";updateClient.AutoSize=true;updateClient.Click+=delegate{CheckAndInstallUpdate(updateClient,null);};
        Button miners = new Button(); miners.Text="矿机状态"; miners.AutoSize=true; miners.Click+=delegate{new MinerStatusForm(manager).Show(this);};
        diagnostics.Text="一键诊断"; diagnostics.AutoSize=true; diagnostics.Click+=delegate{RunDiagnostics();};
        repair.Text="检查并修复";repair.AutoSize=true;repair.Click+=delegate{RunCheckAndRepair();};
        Button help = new Button(); help.Text = "各项说明"; help.AutoSize = true; help.Click += delegate { ShowHelp(); };
        start.Text = "启动中转"; start.AutoSize = true; start.Click += delegate { StartRelay(); };
        stop.Text = "停止"; stop.AutoSize = true; stop.Enabled = false; stop.Click += delegate { relayRequested=false;manager.Stop();manager.SetRecoveryState("","");SetRunning(false); };
        settingsButtons.Controls.Add(save); settingsButtons.Controls.Add(validateConfig); settingsButtons.Controls.Add(backups);settingsButtons.Controls.Add(updateClient);settingsButtons.Controls.Add(help);
        settingsPage.Controls.Add(settingsButtons);settingsButtons.BringToFront();

        FlowLayoutPanel dutyButtons=new FlowLayoutPanel();dutyButtons.Dock=DockStyle.Top;dutyButtons.Height=92;dutyButtons.Padding=new Padding(18,10,0,0);dutyButtons.WrapContents=true;
        Button contact=new Button();contact.Text="联系技术人员";contact.AutoSize=true;contact.Click+=delegate{ShowContactSupport();};
        Button supportBundle=new Button();supportBundle.Text="导出技术支持包";supportBundle.AutoSize=true;supportBundle.Click+=delegate{ExportSupportBundle();};
        Button admin=new Button();admin.Text="设置";admin.AutoSize=true;admin.Click+=delegate{pages.SelectedTab=settingsPage;};
        dutyButtons.Controls.Add(repair);dutyButtons.Controls.Add(diagnostics);dutyButtons.Controls.Add(miners);dutyButtons.Controls.Add(start);dutyButtons.Controls.Add(stop);dutyButtons.Controls.Add(supportBundle);dutyButtons.Controls.Add(contact);dutyButtons.Controls.Add(admin);
        homePage.Controls.Add(dutyButtons);

        Panel summary = BuildHomeSummary();
        homePage.Controls.Add(summary); summary.BringToFront();

        Label logLabel = new Label(); logLabel.Text = "运行记录"; logLabel.Dock = DockStyle.Top; logLabel.Height = 28; logLabel.Padding = new Padding(18, 5, 0, 0);
        homePage.Controls.Add(logLabel); logLabel.BringToFront();
        logs.Dock = DockStyle.Fill; logs.Multiline = true; logs.ReadOnly = true; logs.ScrollBars = ScrollBars.Vertical;
        logs.BackColor = Color.FromArgb(247, 249, 252); logs.BorderStyle = BorderStyle.FixedSingle; logs.Margin = new Padding(18);
        Panel logPanel = new Panel(); logPanel.Dock = DockStyle.Fill; logPanel.Padding = new Padding(18, 0, 18, 16); logPanel.Controls.Add(logs);
        homePage.Controls.Add(logPanel); logPanel.BringToFront();
        Controls.Add(pages);
    }

    // 首页信息分区：状态条（先给结论）、指标卡片、端口明细、线路明细。
    // 配色只区分「正常／需关注／异常」三种语气，饱和度压低，长时间看不刺眼。
    private static readonly Color InkStrong = Color.FromArgb(38, 50, 56);
    private static readonly Color InkMuted = Color.FromArgb(108, 118, 128);
    private static readonly Color ToneOk = Color.FromArgb(45, 105, 58);
    private static readonly Color ToneWarn = Color.FromArgb(158, 100, 4);
    private static readonly Color ToneBad = Color.FromArgb(170, 56, 56);
    private static readonly Color SurfaceSubtle = Color.FromArgb(247, 250, 252);
    private static readonly Color SurfaceOk = Color.FromArgb(233, 244, 235);
    private static readonly Color SurfaceWarn = Color.FromArgb(252, 245, 230);
    private static readonly Color SurfaceIdle = Color.FromArgb(240, 243, 246);

    private Panel BuildHomeSummary()
    {
        TableLayoutPanel host = new TableLayoutPanel();
        host.Dock = DockStyle.Top; host.Height = 356; host.ColumnCount = 1; host.RowCount = 4;
        host.BackColor = Color.White; host.Padding = new Padding(0); host.Margin = new Padding(0);
        host.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        host.RowStyles.Add(new RowStyle(SizeType.Absolute, 66));
        host.RowStyles.Add(new RowStyle(SizeType.Absolute, 84));
        host.RowStyles.Add(new RowStyle(SizeType.Absolute, 114));
        host.RowStyles.Add(new RowStyle(SizeType.Absolute, 92));

        // 1) 状态条
        bannerBar.Dock = DockStyle.Fill; bannerBar.Margin = new Padding(18, 6, 18, 0); bannerBar.BackColor = SurfaceIdle;
        bannerBar.Padding = new Padding(0);
        bannerTitle.AutoSize = false; bannerTitle.Dock = DockStyle.Top; bannerTitle.Height = 34;
        bannerTitle.TextAlign = ContentAlignment.MiddleLeft; bannerTitle.Padding = new Padding(14, 0, 14, 0);
        bannerTitle.Font = new Font("Microsoft YaHei UI", 14F, FontStyle.Bold);
        bannerTitle.ForeColor = InkStrong; bannerTitle.BackColor = SurfaceIdle; bannerTitle.Text = "未启动";
        bannerDetail.AutoSize = false; bannerDetail.Dock = DockStyle.Fill;
        bannerDetail.TextAlign = ContentAlignment.TopLeft; bannerDetail.Padding = new Padding(15, 0, 14, 4);
        bannerDetail.Font = new Font("Microsoft YaHei UI", 9F);
        bannerDetail.ForeColor = InkMuted; bannerDetail.BackColor = SurfaceIdle;
        bannerDetail.Text = "点上面的「启动中转」开始转发。";
        bannerBar.Controls.Add(bannerDetail); bannerBar.Controls.Add(bannerTitle);
        host.Controls.Add(bannerBar, 0, 0);

        // 2) 指标卡片
        FlowLayoutPanel cards = new FlowLayoutPanel();
        cards.Dock = DockStyle.Fill; cards.Margin = new Padding(18, 4, 18, 6);
        cards.WrapContents = false; cards.BackColor = Color.White;
        cards.Controls.Add(BuildCard("当前连接", cardActive));
        cards.Controls.Add(BuildCard("累计连接", cardTotal));
        cards.Controls.Add(BuildCard("失败次数", cardFailures));
        cards.Controls.Add(BuildCard("已运行", cardUptime));
        host.Controls.Add(cards, 0, 1);

        // 3) 端口
        Panel ports = new Panel();
        ports.Dock = DockStyle.Fill; ports.Margin = new Padding(18, 2, 18, 2); ports.BackColor = Color.White;
        Label portsHead = new Label();
        portsHead.Dock = DockStyle.Top; portsHead.Height = 22; portsHead.Text = "端口";
        portsHead.TextAlign = ContentAlignment.MiddleLeft; portsHead.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
        portsHead.ForeColor = InkStrong;
        portFailed.Dock = DockStyle.Top; portFailed.Height = 20; portFailed.AutoSize = false;
        portFailed.Font = new Font("Microsoft YaHei UI", 9F); portFailed.ForeColor = ToneWarn;
        portFailed.TextAlign = ContentAlignment.MiddleLeft; portFailed.BackColor = Color.White; portFailed.Text = "";
        portList.Dock = DockStyle.Top; portList.Height = 40; portList.AutoSize = false;
        portList.Font = new Font("Microsoft YaHei UI", 9F); portList.ForeColor = InkMuted;
        portList.TextAlign = ContentAlignment.TopLeft; portList.BackColor = Color.White; portList.Text = "";
        portStats.Dock = DockStyle.Top; portStats.Height = 24; portStats.WrapContents = false;
        portStats.BackColor = Color.White; portStats.Margin = new Padding(0); portStats.Padding = new Padding(0);
        // Dock=Top 的堆叠顺序与添加顺序相反，因此按「失败行 → 列表 → 统计 → 标题」反向添加，
        // 视觉上才是「标题 / 统计 / 端口列表 / 失败行」
        ports.Controls.Add(portFailed);
        ports.Controls.Add(portList);
        ports.Controls.Add(portStats);
        ports.Controls.Add(portsHead);
        host.Controls.Add(ports, 0, 2);

        // 4) 线路
        Panel endpoints = new Panel();
        endpoints.Dock = DockStyle.Fill; endpoints.Margin = new Padding(18, 2, 18, 2); endpoints.BackColor = Color.White;
        Label endpointsHead = new Label();
        endpointsHead.Dock = DockStyle.Top; endpointsHead.Height = 22; endpointsHead.Text = "线路";
        endpointsHead.TextAlign = ContentAlignment.MiddleLeft; endpointsHead.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
        endpointsHead.ForeColor = InkStrong; endpointsHead.BackColor = Color.White;
        endpointList.Dock = DockStyle.Fill; endpointList.WrapContents = false;
        endpointList.FlowDirection = FlowDirection.TopDown; endpointList.AutoScroll = false;
        endpointList.BackColor = Color.White; endpointList.Margin = new Padding(0); endpointList.Padding = new Padding(0);
        endpoints.Controls.Add(endpointList); endpoints.Controls.Add(endpointsHead);
        host.Controls.Add(endpoints, 0, 3);
        return host;
    }

    private static Label StatChip(string text, Color color, bool bold)
    {
        Label chip = new Label();
        chip.AutoSize = true; chip.Text = text; chip.ForeColor = color; chip.BackColor = Color.White;
        chip.Font = new Font("Microsoft YaHei UI", 9F, bold ? FontStyle.Bold : FontStyle.Regular);
        chip.Margin = new Padding(0, 2, 4, 0);
        return chip;
    }

    private static Panel BuildCard(string caption, Label value)
    {
        Panel card = new Panel();
        card.Width = 148; card.Height = 68; card.Margin = new Padding(0, 0, 10, 0);
        card.BackColor = SurfaceSubtle;
        Panel top = new Panel(); top.Dock = DockStyle.Top; top.Height = 40; top.BackColor = SurfaceSubtle;
        value.AutoSize = false; value.Dock = DockStyle.Fill;
        value.TextAlign = ContentAlignment.MiddleLeft;
        value.Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold);
        value.ForeColor = InkStrong; value.Text = "--"; value.Padding = new Padding(12, 0, 8, 0);
        value.BackColor = SurfaceSubtle;
        top.Controls.Add(value);
        Label title = new Label();
        title.Dock = DockStyle.Fill; title.Text = caption; title.TextAlign = ContentAlignment.MiddleLeft;
        title.Padding = new Padding(12, 0, 8, 6); title.Font = new Font("Microsoft YaHei UI", 8.5F);
        title.ForeColor = InkMuted; title.BackColor = SurfaceSubtle;
        card.Controls.Add(title); card.Controls.Add(top);
        return card;
    }

    private static void AddRow(TableLayoutPanel grid, int row, string labelText, Control control, string hint)    {
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

    private void EditBackups()
    {
        using (BackupForm form = new BackupForm(backupProfiles,manager,siteName.Text.Trim())) if (form.ShowDialog(this) == DialogResult.OK) backupProfiles = form.Profiles;
    }

    private void HandleRemoteAction(RemoteClientAction action)
    {
        if(IsDisposed||Disposing)return;if(InvokeRequired){BeginInvoke(new Action<RemoteClientAction>(HandleRemoteAction),action);return;}RemoteActionReceiptStore receipts=new RemoteActionReceiptStore(ConfigStore.Folder);Log("收到 VPS 受限操作："+action.Action+"（"+action.Id+"）。");
        try{
            if(action.Action=="diagnose"){RelaySnapshot snapshot=manager.Snapshot();List<ClientHealthIssue> issues=ClientHealthInspector.Inspect(CurrentConfig(),manager,Application.ExecutablePath);StringBuilder report=new StringBuilder();report.AppendLine("木林森中转远程诊断");report.AppendLine("时间："+DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));report.AppendLine("版本："+AppBrand.Version);report.AppendLine("运行："+(snapshot.Running?"是":"否"));report.AppendLine("在线矿机："+snapshot.ActiveMiners);report.AppendLine("当前连接："+snapshot.Active);report.AppendLine("失败重连："+snapshot.Failures);foreach(ClientHealthIssue issue in issues)report.AppendLine(issue.Code+"："+issue.Title+"；"+issue.Detail);Directory.CreateDirectory(ConfigStore.Folder);File.WriteAllText(Path.Combine(ConfigStore.Folder,"remote-diagnostic-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".txt"),report.ToString(),Encoding.UTF8);receipts.Save(action,"completed");Log("VPS 远程诊断完成，报告已保存在本机数据目录。");return;}
            if(action.Action=="reconnect"){if(!relayRequested)throw new InvalidOperationException("中转当前未启动，不能执行远程重连。");RunNetworkRecovery("VPS 管理面板请求重新连接");receipts.Save(action,"completed");return;}
            if(action.Action=="upgrade"){CheckAndInstallUpdate(null,action);return;}
            throw new InvalidOperationException("未允许的远程操作。");
        }catch(Exception ex){try{receipts.Save(action,"failed");}catch{}Log("VPS 受限操作失败："+ex.Message);}
    }

    private void ExportSupportBundle()
    {
        try{using(SaveFileDialog file=new SaveFileDialog()){file.Filter="木林森技术支持包|*.zip";file.DefaultExt="zip";file.AddExtension=true;file.FileName="木林森技术支持包-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".zip";if(file.ShowDialog(this)!=DialogResult.OK)return;SupportBundleExporter.Create(file.FileName,CurrentConfig(),manager.Snapshot(),manager.MinerSnapshots(),logs.Text,ConfigStore.Folder);Log("脱敏技术支持包已导出："+file.FileName);MessageBox.Show(this,"技术支持包已生成。\r\n\r\n程序已扫描并排除共享密钥、密码、证书私钥、完整公网地址和完整矿机 IP。可以把这个 ZIP 交给技术人员。","导出完成",MessageBoxButtons.OK,MessageBoxIcon.Information);}}
        catch(Exception ex){Log("技术支持包导出失败："+ex.Message);MessageBox.Show(this,"支持包没有生成。\r\n\r\n"+ex.Message,"导出失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
    }

    private async void CheckForUpdates(Button button)
    {
        string original=button==null?"":button.Text;
        try{
            if(button!=null){button.Enabled=false;button.Text="检查中…";}
            LatestClientRelease release=await LatestClientReleaseChecker.CheckAsync(CancellationToken.None);
            string detail="当前版本 "+AppBrand.Version+"，最新版本 "+release.Version+"。";
            if(!release.IsNewerThan(AppBrand.Version)){Log("检查更新："+detail+"已是最新。");MessageBox.Show(this,detail+"\r\n\r\n当前已是最新版本。","检查更新",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}
            if(!UpdateRolloutPolicy.IsEligible(Environment.MachineName,release.RolloutPercent)){Log("检查更新："+detail+"本机暂未进入本批次（"+release.RolloutPercent+"%）。");MessageBox.Show(this,detail+"\r\n\r\n新版正在分批发布，这台电脑暂未进入本批次，稍后再检查即可。","检查更新",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}
            Log("检查更新："+detail+"可以升级。");
            MessageBox.Show(this,detail+"\r\n\r\n可以点击「一键升级」安装。","检查更新",MessageBoxButtons.OK,MessageBoxIcon.Information);
        }catch(Exception ex){Log("检查更新失败："+ex.Message);MessageBox.Show(this,"检查更新失败："+ex.Message,"检查更新",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
        finally{if(button!=null&&!IsDisposed){button.Enabled=true;button.Text=original;}}
    }

    private async void CheckAndInstallUpdate(Button button,RemoteClientAction remoteAction)
    {
        bool remote=remoteAction!=null;if(button!=null){button.Enabled=false;button.Text="检查升级中…";}try{LatestClientRelease release=await LatestClientReleaseChecker.CheckAsync(CancellationToken.None);if(!release.IsNewerThan(AppBrand.Version)){if(remote)new RemoteActionReceiptStore(ConfigStore.Folder).Save(remoteAction,"completed");else MessageBox.Show(this,"当前版本 "+AppBrand.Version+" 已是最新版。","无需升级",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}if(!remote&&!UpdateRolloutPolicy.IsEligible(Environment.MachineName,release.RolloutPercent)){MessageBox.Show(this,"新版 "+release.Version+" 正在分批发布，这台电脑暂未进入本批次。稍后再次检查即可。","分批升级",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}string folder=Path.Combine(ConfigStore.Folder,"updates");Log("发现新版 "+release.Version+"，正在下载并校验摘要、版本和固定发布者签名。");string staged=await SignedUpdatePackage.DownloadAndVerifyAsync(release,folder,CancellationToken.None);if(!remote&&MessageBox.Show(this,"新版 "+release.Version+" 已通过 SHA-256、文件版本和固定发布者签名校验。\r\n\r\n程序将退出并自动替换；如果新版本无法正常启动，会恢复上一版本。现在升级吗？","安装签名升级",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;if(remote)new RemoteActionReceiptStore(ConfigStore.Folder).Save(remoteAction,"started");SignedUpdateCoordinator.Begin(Application.ExecutablePath,staged,release.Version,SignedUpdatePackage.ComputeSha256(staged));exiting=true;Application.Exit();}
        catch(Exception ex){if(remote){try{new RemoteActionReceiptStore(ConfigStore.Folder).Save(remoteAction,"failed");}catch{}}Log("升级检查失败："+ex.Message);if(!remote)MessageBox.Show(this,"升级没有执行，当前版本保持不变。\r\n\r\n"+ex.Message,"升级失败",MessageBoxButtons.OK,MessageBoxIcon.Warning);}finally{if(button!=null&&!IsDisposed&&!Disposing){button.Enabled=true;button.Text="检查并升级";}}
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
        if(++statusTicks>=30){statusTicks=0;manager.SaveMinerHistory();}
        RelaySnapshot s=manager.Snapshot();
        DutyHomeSummary duty=DutyHomeSummary.Create(s,manager.MinerSnapshots());
        start.Enabled=!manager.IsRunning;

        List<int> livePorts=manager.ListeningPorts;
        List<string> failedPorts=manager.PortFailures;
        DateTime nowUtc=DateTime.UtcNow;
        bool endpointTrouble=false;
        foreach(EndpointState e in s.Endpoints) if(!e.Online||e.Recovering||e.CooldownUntilUtc>nowUtc) endpointTrouble=true;

        // 状态条：先给一句结论
        bool needsAttention=s.Running&&(failedPorts.Count>0||endpointTrouble||duty.AffectedMiners>0);
        if(!s.Running){
            PaintBanner(SurfaceIdle,InkStrong,"未启动",InkMuted,"点上面的「启动中转」开始转发。");
        } else if(needsAttention){
            string detail=String.IsNullOrWhiteSpace(duty.Guidance)?("受影响矿机 "+duty.AffectedMiners+" 台"):duty.Guidance;
            if(failedPorts.Count>0) detail="有 "+failedPorts.Count+" 个端口被占用已跳过；"+detail;
            PaintBanner(SurfaceWarn,ToneWarn,"运行中 · 有需要关注的项目",ToneWarn,detail);
        } else {
            PaintBanner(SurfaceOk,ToneOk,"运行正常",ToneOk,"受影响矿机 0 台，当前不需要值守员操作。");
        }

        cardActive.Text=s.Active.ToString();
        cardTotal.Text=s.Total.ToString();
        cardFailures.Text=s.Failures.ToString();
        cardFailures.ForeColor=s.Failures>0?ToneBad:InkStrong;
        cardUptime.Text=s.Running?FormatDuration(DateTime.Now-s.StartedAt):"--";
        cardUptime.ForeColor=s.Running?InkStrong:InkMuted;

        // 端口：只在内容变化时重建，避免每秒刷新闪动
        List<string> liveText=new List<string>(); foreach(int port in livePorts) liveText.Add(port.ToString());
        string portSig=String.Join(",",liveText.ToArray())+"|"+String.Join(",",failedPorts.ToArray())+"|"+s.Running;
        if(portSig!=portSignature){
            portSignature=portSig;
            portStats.SuspendLayout();
            portStats.Controls.Clear();
            if(!s.Running&&livePorts.Count==0&&failedPorts.Count==0){
                portStats.Controls.Add(StatChip("未启动，启动后这里会列出正在监听的端口。",InkMuted,false));
                portList.Text=""; portFailed.Text="";
            } else {
                portStats.Controls.Add(StatChip("正在监听",InkMuted,false));
                portStats.Controls.Add(StatChip(livePorts.Count+" 个",livePorts.Count>0?ToneOk:ToneBad,true));
                if(failedPorts.Count>0){
                    portStats.Controls.Add(StatChip("· 无法监听",InkMuted,false));
                    portStats.Controls.Add(StatChip(failedPorts.Count+" 个",ToneWarn,true));
                }
                portList.Text=livePorts.Count==0?"（没有可用的监听端口）":String.Join("，",liveText.ToArray());
                portFailed.Text=failedPorts.Count==0?"":("无法监听："+String.Join("，",failedPorts.ToArray()));
            }
            portStats.ResumeLayout();
        }

        // 线路：每条一行，按状态着色
        List<string> endpointLines=new List<string>(); List<Color> endpointColors=new List<Color>();
        bool endpointBold=true;
        foreach(EndpointState e in s.Endpoints){
            string phase; Color tone;
            if(e.Recovering){phase="恢复观察";tone=ToneWarn;}
            else if(e.CooldownUntilUtc>nowUtc){phase="冷却至 "+e.CooldownUntilUtc.ToLocalTime().ToString("HH:mm:ss");tone=ToneWarn;}
            else if(e.Online){phase="正常 "+e.LatencyMs+"ms";tone=ToneOk;}
            else {phase="异常 "+(String.IsNullOrWhiteSpace(e.LastError)?"连接失败":e.LastError);tone=ToneBad;}
            endpointLines.Add((e.Selected?"▶ ":"　 ")+(String.IsNullOrWhiteSpace(e.Name)?"线路":e.Name)+"　"+phase);
            endpointColors.Add(tone);
        }
        if(!String.IsNullOrWhiteSpace(s.RecoveryPhase)){
            endpointLines.Add("网络恢复："+s.RecoveryPhase+" · "+s.RecoveryMessage);
            endpointColors.Add(ToneWarn);
        }
        string endpointSig=String.Join("|",endpointLines.ToArray());
        if(endpointSig!=endpointSignature){
            endpointSignature=endpointSig;
            endpointList.SuspendLayout();
            endpointList.Controls.Clear();
            if(endpointLines.Count==0){
                endpointList.Controls.Add(StatChip("启动后自动检测矿池线路。",InkMuted,false));
            } else {
                for(int i=0;i<endpointLines.Count;i++){
                    Label line=StatChip(endpointLines[i],endpointColors[i],endpointBold);
                    line.Margin=new Padding(0,1,0,1);
                    endpointList.Controls.Add(line);
                }
            }
            endpointList.ResumeLayout();
        }
    }

    private void PaintBanner(Color surface, Color titleColor, string title, Color detailColor, string detail)
    {
        if(bannerBar.BackColor!=surface){
            bannerBar.BackColor=surface; bannerTitle.BackColor=surface; bannerDetail.BackColor=surface;
        }
        bannerTitle.ForeColor=titleColor; bannerTitle.Text=title;
        bannerDetail.ForeColor=detailColor; bannerDetail.Text=detail;
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
