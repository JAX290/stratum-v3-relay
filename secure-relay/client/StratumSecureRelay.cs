using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Net.NetworkInformation;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Reflection;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("木林森中转")]
[assembly: AssemblyDescription("Stratum V3 TLS client for mine-site LAN relaying")]
[assembly: AssemblyCompany("Stratum V3 Relay")]
[assembly: AssemblyProduct("木林森中转")]
[assembly: AssemblyVersion("1.2.0.0")]
[assembly: AssemblyFileVersion("1.2.0.0")]

[DataContract]
public sealed class AppConfig
{
    [DataMember] public string ServerAddress = "";
    [DataMember] public int ServerPort = 443;
    [DataMember] public string ServerName = "";
    [DataMember] public string CertificateSha256 = "";
    [DataMember] public string ProtectedToken = "";
    [DataMember] public string ListenAddress = "0.0.0.0";
    [DataMember] public string Ports = "9999,10001,10002,10010,10011,10012,10020,10021,10022,10030,10031,10032,11001,11002,11003,11101,11102,11103,11201,11202,11203,11301,11302,11303";
    [DataMember] public bool AutoStart = false;
    [DataMember] public bool CloseToTray = true;

    [OnDeserializing]
    private void BeforeDeserialize(StreamingContext context) { CloseToTray = true; }
}

public static class ConfigStore
{
    public static readonly string Folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StratumSecureRelay");
    public static readonly string FilePath = Path.Combine(Folder, "config.json");

    public static AppConfig Load()
    {
        try {
            using (FileStream stream = File.OpenRead(FilePath))
                return (AppConfig)new DataContractJsonSerializer(typeof(AppConfig)).ReadObject(stream);
        } catch { return new AppConfig(); }
    }

    public static void Save(AppConfig config)
    {
        Directory.CreateDirectory(Folder);
        string temporary = FilePath + ".tmp";
        using (FileStream stream = File.Create(temporary))
            new DataContractJsonSerializer(typeof(AppConfig)).WriteObject(stream, config);
        if (File.Exists(FilePath)) File.Replace(temporary, FilePath, null);
        else File.Move(temporary, FilePath);
    }

    public static string Protect(string value)
    {
        byte[] data = Encoding.UTF8.GetBytes(value);
        return Convert.ToBase64String(ProtectedData.Protect(data, null, DataProtectionScope.CurrentUser));
    }

    public static string Unprotect(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return "";
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(value), null, DataProtectionScope.CurrentUser)); }
        catch { return ""; }
    }
}

public sealed class RelayManager
{
    private readonly Action<string> log;
    private CancellationTokenSource stop;
    private readonly List<TcpListener> listeners = new List<TcpListener>();
    private int active;
    public bool IsRunning { get { return stop != null; } }

    public RelayManager(Action<string> logger) { log = logger; }

    public void Start(AppConfig config, string token, IList<int> ports)
    {
        if (IsRunning) return;
        stop = new CancellationTokenSource();
        IPAddress listenIp;
        if (!IPAddress.TryParse(config.ListenAddress, out listenIp)) throw new InvalidOperationException("本地监听地址格式不正确。");
        try {
            foreach (int port in ports) {
                TcpListener listener = new TcpListener(listenIp, port);
                try { listener.Start(256); }
                catch (SocketException ex) { throw new InvalidOperationException("本地端口 " + port + " 无法监听，可能已被其他程序占用。", ex); }
                listeners.Add(listener);
                AcceptLoop(listener, config, token, port, stop.Token);
            }
        } catch { Stop(); throw; }
        log("已启动，共监听 " + ports.Count + " 个端口。矿机请连接这台电脑的局域网 IP。");
    }

    public void Stop()
    {
        CancellationTokenSource source = stop;
        stop = null;
        if (source != null) source.Cancel();
        foreach (TcpListener listener in listeners) { try { listener.Stop(); } catch { } }
        listeners.Clear();
        if (source != null) source.Dispose();
        log("已停止监听。");
    }

    private async void AcceptLoop(TcpListener listener, AppConfig config, string token, int routePort, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested) {
            try {
                TcpClient miner = await listener.AcceptTcpClientAsync();
                Handle(miner, config, token, routePort, cancellation);
            } catch (ObjectDisposedException) { break; }
              catch (Exception ex) { if (!cancellation.IsCancellationRequested) log("接收连接失败：" + ex.Message); }
        }
    }

    private async void Handle(TcpClient miner, AppConfig config, string token, int routePort, CancellationToken cancellation)
    {
        int number = Interlocked.Increment(ref active);
        IPEndPoint endpoint = miner.Client.RemoteEndPoint as IPEndPoint;
        string source = endpoint == null ? "未知矿机" : endpoint.Address + ":" + endpoint.Port;
        TcpClient remote = new TcpClient();
        try {
            miner.NoDelay = true;
            await ConnectWithTimeout(remote, config.ServerAddress, config.ServerPort, 10000);
            remote.NoDelay = true;
            string expectedPin = NormalizePin(config.CertificateSha256);
            RemoteCertificateValidationCallback validator = delegate(object sender, X509Certificate cert, X509Chain chain, SslPolicyErrors errors) {
                if (cert == null) return false;
                if (expectedPin.Length > 0) {
                    using (SHA256 hash = SHA256.Create()) {
                        string actual = BitConverter.ToString(hash.ComputeHash(cert.GetRawCertData())).Replace("-", "");
                        return String.Equals(actual, expectedPin, StringComparison.OrdinalIgnoreCase);
                    }
                }
                return errors == SslPolicyErrors.None;
            };
            SslStream tls = new SslStream(remote.GetStream(), false, validator);
            string targetName = String.IsNullOrWhiteSpace(config.ServerName) ? config.ServerAddress : config.ServerName.Trim();
            await tls.AuthenticateAsClientAsync(targetName, null, SslProtocols.Tls12, false);
            string minerIp = endpoint == null ? "0.0.0.0" : endpoint.Address.ToString();
            int minerPort = endpoint == null ? 1 : endpoint.Port;
            string request = "CONNECT /relay/v1/" + routePort + " HTTP/1.1\r\n" +
                "Host: " + targetName + "\r\n" +
                "User-Agent: StratumSecureRelay/1.0\r\n" +
                "Authorization: Bearer " + token + "\r\n" +
                "X-Miner-IP: " + minerIp + "\r\n" +
                "X-Miner-Port: " + minerPort + "\r\n\r\n";
            byte[] requestBytes = Encoding.ASCII.GetBytes(request);
            await tls.WriteAsync(requestBytes, 0, requestBytes.Length, cancellation);
            await tls.FlushAsync(cancellation);
            string response = await ReadHeader(tls, cancellation);
            if (!response.StartsWith("HTTP/1.1 200 ", StringComparison.Ordinal)) throw new IOException("VPS 拒绝了连接，请检查共享密钥和端口配置。");
            log(source + " 已加密连接，目标端口 " + routePort + "；当前连接 " + number);
            Task up = miner.GetStream().CopyToAsync(tls, 65536, cancellation);
            Task down = tls.CopyToAsync(miner.GetStream(), 65536, cancellation);
            await Task.WhenAny(up, down);
            tls.Dispose();
        } catch (Exception ex) {
            if (!cancellation.IsCancellationRequested) log(source + " 连接失败：" + FriendlyError(ex));
        } finally {
            try { miner.Close(); } catch { }
            try { remote.Close(); } catch { }
            int remaining = Interlocked.Decrement(ref active);
            log(source + " 已断开；当前连接 " + remaining);
        }
    }

    private static async Task ConnectWithTimeout(TcpClient client, string host, int port, int timeoutMs)
    {
        Task connect = client.ConnectAsync(host, port);
        if (await Task.WhenAny(connect, Task.Delay(timeoutMs)) != connect) throw new TimeoutException("连接 VPS 超时。");
        await connect;
    }

    private static async Task<string> ReadHeader(Stream stream, CancellationToken cancellation)
    {
        MemoryStream buffer = new MemoryStream();
        byte[] one = new byte[1];
        while (buffer.Length < 8192) {
            int count = await stream.ReadAsync(one, 0, 1, cancellation);
            if (count == 0) throw new IOException("VPS 在握手时断开连接。");
            buffer.WriteByte(one[0]);
            byte[] data = buffer.GetBuffer();
            int n = (int)buffer.Length;
            if (n >= 4 && data[n-4] == 13 && data[n-3] == 10 && data[n-2] == 13 && data[n-1] == 10)
                return Encoding.ASCII.GetString(data, 0, n);
        }
        throw new IOException("VPS 返回的响应过长。");
    }

    private static string NormalizePin(string value) { return Regex.Replace(value ?? "", "[^0-9A-Fa-f]", "").ToUpperInvariant(); }
    private static string FriendlyError(Exception ex) {
        if (ex is AuthenticationException) return "TLS 证书验证失败。请核对证书名称或 SHA-256 指纹。";
        return ex.Message;
    }
}

public static class NetworkHelper
{
    public static string GetLanIPv4()
    {
        string best = "";
        int bestScore = -1;
        try {
            foreach (NetworkInterface adapter in NetworkInterface.GetAllNetworkInterfaces()) {
                if (adapter.OperationalStatus != OperationalStatus.Up || adapter.NetworkInterfaceType == NetworkInterfaceType.Loopback || adapter.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
                string identity = (adapter.Name + " " + adapter.Description).ToLowerInvariant();
                if (identity.Contains("tailscale") || identity.Contains("vmware") || identity.Contains("virtual") || identity.Contains("hyper-v") || identity.Contains("clash") || identity.Contains("tap") || identity.Contains("vpn")) continue;
                IPInterfaceProperties properties = adapter.GetIPProperties();
                bool hasGateway = false;
                foreach (GatewayIPAddressInformation gateway in properties.GatewayAddresses) {
                    if (gateway.Address.AddressFamily == AddressFamily.InterNetwork && !gateway.Address.Equals(IPAddress.Any)) { hasGateway = true; break; }
                }
                if (!hasGateway) continue;
                foreach (UnicastIPAddressInformation address in properties.UnicastAddresses) {
                    if (address.Address.AddressFamily != AddressFamily.InterNetwork || !IsPrivate(address.Address)) continue;
                    int score = 100;
                    if (adapter.NetworkInterfaceType == NetworkInterfaceType.Wireless80211) score += 20;
                    if (adapter.NetworkInterfaceType == NetworkInterfaceType.Ethernet || adapter.NetworkInterfaceType == NetworkInterfaceType.GigabitEthernet) score += 15;
                    if (score > bestScore) { best = address.Address.ToString(); bestScore = score; }
                }
            }
        } catch { }
        return best;
    }

    private static bool IsPrivate(IPAddress address)
    {
        byte[] value = address.GetAddressBytes();
        return value.Length == 4 && (value[0] == 10 || (value[0] == 172 && value[1] >= 16 && value[1] <= 31) || (value[0] == 192 && value[1] == 168));
    }
}

public sealed class MainForm : Form
{
    private readonly TextBox server = new TextBox();
    private readonly NumericUpDown tlsPort = new NumericUpDown();
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
    private readonly TextBox logs = new TextBox();
    private readonly NotifyIcon tray = new NotifyIcon();
    private readonly RelayManager manager;
    private readonly System.Windows.Forms.Timer ipTimer = new System.Windows.Forms.Timer();
    private bool exiting;

    public MainForm()
    {
        Text = "木林森中转";
        Font = new Font("Microsoft YaHei UI", 9F);
        ClientSize = new Size(760, 740);
        MinimumSize = new Size(700, 680);
        StartPosition = FormStartPosition.CenterScreen;
        manager = new RelayManager(Log);
        BuildUi();
        LoadConfig();
        ports.TextChanged += delegate { RefreshMinerAddresses(false); };
        RefreshMinerAddresses(false);
        ipTimer.Interval = 30000;
        ipTimer.Tick += delegate { RefreshMinerAddresses(false); };
        ipTimer.Start();
        tray.Text = "木林森中转";
        Icon appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        if (appIcon != null) { Icon = appIcon; tray.Icon = appIcon; }
        else tray.Icon = SystemIcons.Application;
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
        TableLayoutPanel grid = new TableLayoutPanel();
        grid.Dock = DockStyle.Top; grid.Height = 426; grid.Padding = new Padding(18, 14, 18, 4);
        grid.ColumnCount = 2; grid.RowCount = 11;
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 165));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int i=0; i<11; i++) grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        AddRow(grid, 0, "VPS 地址", server, "例如 203.0.113.10");
        tlsPort.Minimum = 1; tlsPort.Maximum = 65535; tlsPort.Value = 443;
        AddRow(grid, 1, "TLS 端口", tlsPort, "默认 443");
        AddRow(grid, 2, "证书名称（可选）", serverName, "使用正规域名证书时填写域名");
        AddRow(grid, 3, "证书 SHA-256（可选）", pin, "使用 IP/自签名证书时填写 VPS 安装脚本输出的指纹");
        token.UseSystemPasswordChar = true;
        AddRow(grid, 4, "共享密钥", token, "VPS 安装脚本输出的 64 位密钥");
        AddRow(grid, 5, "本地监听地址", listen, "0.0.0.0 表示接受局域网矿机连接");
        AddRow(grid, 6, "本地端口", ports, "多个端口用英文逗号分隔，与 V3 面板端口一致");
        autoStart.Text = "开机自动启动";
        autoStart.AutoSize = true;
        grid.Controls.Add(autoStart, 1, 7);
        closeToTray.Text = "点击关闭最小化";
        closeToTray.AutoSize = true;
        grid.Controls.Add(closeToTray, 1, 8);
        currentIp.ReadOnly = true;
        currentIp.BackColor = Color.White;
        Button refreshIp = new Button(); refreshIp.Text = "刷新"; refreshIp.AutoSize = true; refreshIp.Click += delegate { RefreshMinerAddresses(true); };
        AddRow(grid, 9, "当前局域网 IP", InlineControls(currentIp, refreshIp), "自动识别矿机应连接的值守电脑局域网 IP");
        minerAddress.DropDownStyle = ComboBoxStyle.DropDownList;
        Button copyAddress = new Button(); copyAddress.Text = "复制地址"; copyAddress.AutoSize = true; copyAddress.Click += delegate { CopyMinerAddress(); };
        AddRow(grid, 10, "矿机填写地址", InlineControls(minerAddress, copyAddress), "选择端口后复制完整的 stratum+tcp 地址");
        Controls.Add(grid);

        FlowLayoutPanel buttons = new FlowLayoutPanel();
        buttons.Dock = DockStyle.Top; buttons.Height = 52; buttons.Padding = new Padding(180, 6, 0, 0);
        Button save = new Button(); save.Text = "保存设置"; save.AutoSize = true; save.Click += delegate { SaveConfig(true); };
        Button help = new Button(); help.Text = "各项说明"; help.AutoSize = true; help.Click += delegate { ShowHelp(); };
        start.Text = "启动中转"; start.AutoSize = true; start.Click += delegate { StartRelay(); };
        stop.Text = "停止"; stop.AutoSize = true; stop.Enabled = false; stop.Click += delegate { manager.Stop(); SetRunning(false); };
        buttons.Controls.Add(save); buttons.Controls.Add(start); buttons.Controls.Add(stop); buttons.Controls.Add(help);
        Controls.Add(buttons); buttons.BringToFront();

        Label logLabel = new Label(); logLabel.Text = "运行记录"; logLabel.Dock = DockStyle.Top; logLabel.Height = 28; logLabel.Padding = new Padding(18, 5, 0, 0);
        Controls.Add(logLabel); logLabel.BringToFront();
        logs.Dock = DockStyle.Fill; logs.Multiline = true; logs.ReadOnly = true; logs.ScrollBars = ScrollBars.Vertical;
        logs.BackColor = Color.FromArgb(247, 249, 252); logs.BorderStyle = BorderStyle.FixedSingle; logs.Margin = new Padding(18);
        Panel logPanel = new Panel(); logPanel.Dock = DockStyle.Fill; logPanel.Padding = new Padding(18, 0, 18, 16); logPanel.Controls.Add(logs);
        Controls.Add(logPanel); logPanel.BringToFront();
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
            HashSet<int> unique = new HashSet<int>();
            foreach (string item in ports.Text.Split(',')) {
                int value;
                if (Int32.TryParse(item.Trim(), out value) && value >= 1 && value <= 65535 && unique.Add(value))
                    minerAddress.Items.Add("stratum+tcp://" + detected + ":" + value);
            }
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
        server.Text = c.ServerAddress; tlsPort.Value = Math.Max(1, Math.Min(65535, c.ServerPort)); serverName.Text = c.ServerName;
        pin.Text = c.CertificateSha256; token.Text = ConfigStore.Unprotect(c.ProtectedToken); listen.Text = c.ListenAddress; ports.Text = c.Ports; autoStart.Checked = c.AutoStart; closeToTray.Checked = c.CloseToTray;
        Log("请填写 VPS 安装脚本输出的设置，然后启动中转。");
    }

    private AppConfig CurrentConfig()
    {
        return new AppConfig { ServerAddress=server.Text.Trim(), ServerPort=(int)tlsPort.Value, ServerName=serverName.Text.Trim(),
            CertificateSha256=pin.Text.Trim(), ProtectedToken=ConfigStore.Protect(token.Text.Trim()), ListenAddress=listen.Text.Trim(),
            Ports=ports.Text.Trim(), AutoStart=autoStart.Checked, CloseToTray=closeToTray.Checked };
    }

    private List<int> ValidateSettings()
    {
        if (String.IsNullOrWhiteSpace(server.Text)) throw new InvalidOperationException("请填写 VPS 地址。");
        if (token.Text.Trim().Length < 32 || !Regex.IsMatch(token.Text.Trim(), "^[A-Za-z0-9_-]+$")) throw new InvalidOperationException("共享密钥格式不正确。");
        if (String.IsNullOrWhiteSpace(pin.Text) && String.IsNullOrWhiteSpace(serverName.Text)) throw new InvalidOperationException("请填写证书名称或证书 SHA-256 指纹，不能关闭证书验证。");
        HashSet<int> unique = new HashSet<int>();
        foreach (string item in ports.Text.Split(',')) {
            int value; if (!Int32.TryParse(item.Trim(), out value) || value < 1 || value > 65535) throw new InvalidOperationException("本地端口格式不正确：" + item);
            unique.Add(value);
        }
        if (unique.Count == 0) throw new InvalidOperationException("至少需要一个本地端口。");
        return new List<int>(unique);
    }

    private void SaveConfig(bool showMessage)
    {
        ValidateSettings();
        AppConfig c = CurrentConfig(); ConfigStore.Save(c); SetAutoStart(c.AutoStart);
        if (showMessage) Log("设置已保存。共享密钥已使用当前 Windows 用户加密保存。");
    }

    private void StartRelay()
    {
        try { List<int> routePorts = ValidateSettings(); SaveConfig(false); manager.Start(CurrentConfig(), token.Text.Trim(), routePorts); SetRunning(true); }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, "无法启动", MessageBoxButtons.OK, MessageBoxIcon.Warning); Log("启动失败：" + ex.Message); }
    }

    private void SetRunning(bool running)
    {
        start.Enabled = !running; stop.Enabled = running;
        server.Enabled = tlsPort.Enabled = serverName.Enabled = pin.Enabled = token.Enabled = listen.Enabled = ports.Enabled = !running;
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
            "VPS 地址：云服务器的公网 IP。\r\n\r\n" +
            "TLS 端口：加密入口，通常使用 443。\r\n\r\n" +
            "证书名称：有正规域名证书时填写域名；没有域名可以留空。\r\n\r\n" +
            "证书 SHA-256：没有正规域名证书时，填写 VPS 安装脚本给出的指纹，用来确认连到的是自己的服务器。\r\n\r\n" +
            "共享密钥：相当于电脑和 VPS 之间的密码。\r\n\r\n" +
            "本地监听地址：保持 0.0.0.0，局域网矿机才能连接。\r\n\r\n" +
            "本地端口：矿机连接值守电脑时使用的端口，应与 V3 中转端口一致。\r\n\r\n" +
            "当前局域网 IP：值守电脑在矿机局域网里的地址。\r\n\r\n" +
            "矿机填写地址：已经补全的挖矿地址，选择后可以直接复制到矿机后台。";
        MessageBox.Show(this, message, "各项设置说明", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    private void Log(string message)
    {
        if (InvokeRequired) { BeginInvoke(new Action<string>(Log), message); return; }
        logs.AppendText(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message + Environment.NewLine);
    }

    private void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (!exiting && closeToTray.Checked && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; Hide(); tray.ShowBalloonTip(1500, "木林森中转", "程序仍在后台运行。", ToolTipIcon.Info); return; }
        ipTimer.Stop(); manager.Stop(); tray.Visible = false;
    }
}

public static class Program
{
    [STAThread]
    public static void Main()
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new MainForm());
    }
}
