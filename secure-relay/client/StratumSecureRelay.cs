using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
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
using System.Web.Script.Serialization;
using Microsoft.Win32;

[assembly: AssemblyTitle("木林森中转")]
[assembly: AssemblyDescription("Stratum V3 TLS client for mine-site LAN relaying")]
[assembly: AssemblyCompany("Stratum V3 Relay")]
[assembly: AssemblyProduct("木林森中转")]
[assembly: AssemblyVersion("2.1.3.0")]
[assembly: AssemblyFileVersion("2.1.3.0")]

[DataContract]
public sealed class ServerProfile
{
    [DataMember] public string Name = "";
    [DataMember] public bool Enabled = true;
    [DataMember] public string Address = "";
    [DataMember] public int Port = 443;
    [DataMember] public string ServerName = "";
    [DataMember] public string CertificateSha256 = "";
    [DataMember] public string ProtectedToken = "";
    [IgnoreDataMember] public string SharedKey = "";

    public ServerProfile Copy()
    {
        return new ServerProfile { Name=Name, Enabled=Enabled, Address=Address, Port=Port, ServerName=ServerName,
            CertificateSha256=CertificateSha256, ProtectedToken=ProtectedToken, SharedKey=SharedKey };
    }
}

public sealed class PortRoute
{
    public int LocalPort;
    public int RemotePort;
    public override string ToString() { return LocalPort == RemotePort ? LocalPort.ToString() : LocalPort + "=" + RemotePort; }

    public static List<PortRoute> Parse(string text)
    {
        List<PortRoute> result = new List<PortRoute>();
        HashSet<int> locals = new HashSet<int>();
        foreach (string raw in (text ?? "").Split(',')) {
            string item = raw.Trim();
            if (item.Length == 0) continue;
            string[] parts = item.Split('=');
            int local, remote;
            if (parts.Length == 1) { if (!Int32.TryParse(parts[0], out local)) throw new InvalidOperationException("本地端口格式不正确：" + item); remote = local; }
            else if (parts.Length == 2 && Int32.TryParse(parts[0], out local) && Int32.TryParse(parts[1], out remote)) { }
            else throw new InvalidOperationException("端口映射格式不正确：" + item + "，请使用 本地端口=VPS端口");
            if (local < 1 || local > 65535 || remote < 1 || remote > 65535) throw new InvalidOperationException("端口必须在 1 到 65535 之间：" + item);
            if (!locals.Add(local)) throw new InvalidOperationException("本地端口重复：" + local);
            result.Add(new PortRoute { LocalPort=local, RemotePort=remote });
        }
        if (result.Count == 0) throw new InvalidOperationException("至少需要一个本地端口。");
        return result;
    }
}

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
    [DataMember] public string SiteName = "";
    [DataMember] public List<ServerProfile> Servers = new List<ServerProfile>();

    [OnDeserializing]
    private void BeforeDeserialize(StreamingContext context) { CloseToTray = true; }

    public void Normalize()
    {
        if (String.IsNullOrWhiteSpace(SiteName)) SiteName = Environment.MachineName;
        if (Servers == null) Servers = new List<ServerProfile>();
        if (Servers.Count == 0) {
            Servers.Add(new ServerProfile { Name="主VPS", Enabled=true, Address=ServerAddress, Port=ServerPort,
                ServerName=ServerName, CertificateSha256=CertificateSha256, ProtectedToken=ProtectedToken });
        }
        while (Servers.Count < 3) Servers.Add(new ServerProfile { Name=Servers.Count == 1 ? "备用VPS 1" : "备用VPS 2", Enabled=false, Port=443 });
        for (int i=0; i<Servers.Count; i++) {
            if (Servers[i] == null) Servers[i] = new ServerProfile();
            if (String.IsNullOrWhiteSpace(Servers[i].Name)) Servers[i].Name = i == 0 ? "主VPS" : "备用VPS " + i;
            if (String.IsNullOrEmpty(Servers[i].SharedKey)) Servers[i].SharedKey = ConfigStore.Unprotect(Servers[i].ProtectedToken);
        }
    }
}

public static class ConfigStore
{
    public static readonly string Folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StratumSecureRelay");
    public static readonly string FilePath = Path.Combine(Folder, "config.json");

    public static AppConfig Load()
    {
        try {
            using (FileStream stream = File.OpenRead(FilePath))
            {
                AppConfig value = (AppConfig)new DataContractJsonSerializer(typeof(AppConfig)).ReadObject(stream);
                value.Normalize(); return value;
            }
        } catch { AppConfig value = new AppConfig(); value.Normalize(); return value; }
    }

    public static void Save(AppConfig config)
    {
        config.Normalize();
        foreach (ServerProfile profile in config.Servers) profile.ProtectedToken = Protect(profile.SharedKey ?? "");
        ServerProfile primary = config.Servers[0];
        config.ServerAddress=primary.Address; config.ServerPort=primary.Port; config.ServerName=primary.ServerName;
        config.CertificateSha256=primary.CertificateSha256; config.ProtectedToken=primary.ProtectedToken;
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
    private long totalConnections, failedConnections, uploadedBytes, downloadedBytes;
    private DateTime startedAt;
    private readonly object stateLock = new object();
    private readonly Dictionary<string, EndpointState> endpointStates = new Dictionary<string, EndpointState>();
    private readonly Dictionary<string, MinerState> miners = new Dictionary<string, MinerState>();
    public bool IsRunning { get { return stop != null; } }

    public RelayManager(Action<string> logger) { log = logger; LoadMinerHistory(); }

    public void Start(AppConfig config, IList<PortRoute> routes)
    {
        if (IsRunning) return;
        config.Normalize();
        stop = new CancellationTokenSource();
        startedAt = DateTime.Now;
        Interlocked.Exchange(ref totalConnections, 0); Interlocked.Exchange(ref failedConnections, 0);
        Interlocked.Exchange(ref uploadedBytes, 0); Interlocked.Exchange(ref downloadedBytes, 0);
        IPAddress listenIp;
        if (!IPAddress.TryParse(config.ListenAddress, out listenIp)) throw new InvalidOperationException("本地监听地址格式不正确。");
        try {
            foreach (PortRoute route in routes) {
                TcpListener listener = new TcpListener(listenIp, route.LocalPort);
                try { listener.Start(256); }
                catch (SocketException ex) { throw new InvalidOperationException("本地端口 " + route.LocalPort + " 无法监听，可能已被其他程序占用。", ex); }
                listeners.Add(listener);
                AcceptLoop(listener, config, route, stop.Token);
            }
        } catch { Stop(); throw; }
        HealthLoop(config, stop.Token);
        log("已启动，共监听 " + routes.Count + " 个端口。矿机请连接这台电脑的局域网 IP。");
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

    private async void AcceptLoop(TcpListener listener, AppConfig config, PortRoute route, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested) {
            try {
                TcpClient miner = await listener.AcceptTcpClientAsync();
                Handle(miner, config, route, cancellation);
            } catch (ObjectDisposedException) { break; }
              catch (Exception ex) { if (!cancellation.IsCancellationRequested) log("接收连接失败：" + ex.Message); }
        }
    }

    private async void Handle(TcpClient miner, AppConfig config, PortRoute route, CancellationToken cancellation)
    {
        int number = Interlocked.Increment(ref active);
        Interlocked.Increment(ref totalConnections);
        IPEndPoint endpoint = miner.Client.RemoteEndPoint as IPEndPoint;
        string source = endpoint == null ? "未知矿机" : endpoint.Address + ":" + endpoint.Port;
        string minerIp = endpoint == null ? "0.0.0.0" : endpoint.Address.ToString();
        MinerConnection minerState = OpenMiner(minerIp, route);
        TcpClient remote = null;
        SslStream tls = null;
        try {
            miner.NoDelay = true;
            ServerProfile selected = null;
            Exception last = null;
            int minerPort = endpoint == null ? 1 : endpoint.Port;
            foreach (ServerProfile profile in EnabledProfiles(config)) {
                try {
                    TlsConnection connection = await OpenTls(profile, 10000); tls = connection.Stream; remote = connection.Client;
                    string targetName = String.IsNullOrWhiteSpace(profile.ServerName) ? profile.Address : profile.ServerName.Trim();
                    string request = "CONNECT /relay/v1/" + route.RemotePort + " HTTP/1.1\r\n" +
                        "Host: " + targetName + "\r\nUser-Agent: MulinSenRelay/2.0\r\nAuthorization: Bearer " + profile.SharedKey + "\r\n" +
                        "X-Site-Name: " + SafeHeader(config.SiteName) + "\r\nX-Miner-IP: " + minerIp + "\r\nX-Miner-Port: " + minerPort + "\r\n\r\n";
                    byte[] requestBytes = Encoding.ASCII.GetBytes(request);
                    await tls.WriteAsync(requestBytes, 0, requestBytes.Length, cancellation); await tls.FlushAsync(cancellation);
                    string response = await ReadHeader(tls, cancellation);
                    if (!response.StartsWith("HTTP/1.1 200 ", StringComparison.Ordinal)) throw new IOException("VPS 拒绝认证或没有这个转发端口。");
                    selected = profile; MarkSuccess(profile, 0); SetMinerEndpoint(minerState,profile.Name); break;
                } catch (Exception ex) {
                    last = ex; MarkFailure(profile, FriendlyError(ex));
                    try { if(tls!=null)tls.Dispose(); } catch{} try { if(remote!=null)remote.Close(); } catch{}
                    tls=null; remote=null;
                }
            }
            if (selected == null) throw new IOException("主、备用 VPS 都无法连接。" + (last == null ? "" : " " + FriendlyError(last)));
            log(source + " 已通过" + selected.Name + "加密连接，本地 " + route.LocalPort + " → VPS " + route.RemotePort + "；当前连接 " + number);
            Task up = CopyAndCount(miner.GetStream(), tls, true, minerState, cancellation);
            Task down = CopyAndCount(tls, miner.GetStream(), false, minerState, cancellation);
            await Task.WhenAny(up, down);
            try { miner.Client.Shutdown(SocketShutdown.Both); } catch { }
            try { remote.Client.Shutdown(SocketShutdown.Both); } catch { }
            try { await Task.WhenAll(up, down); } catch { }
        } catch (Exception ex) {
            Interlocked.Increment(ref failedConnections);
            MinerFailed(minerState,FriendlyError(ex));
            if (!cancellation.IsCancellationRequested) log(source + " 连接失败：" + FriendlyError(ex));
        } finally {
            try { if (tls != null) tls.Dispose(); } catch { }
            try { miner.Close(); } catch { }
            try { if (remote != null) remote.Close(); } catch { }
            int remaining = Interlocked.Decrement(ref active);
            CloseMiner(minerState);
            log(source + " 已断开；当前连接 " + remaining);
        }
    }

    private async Task<TlsConnection> OpenTls(ServerProfile profile, int timeoutMs)
    {
        TcpClient client = new TcpClient();
        try {
            await ConnectWithTimeout(client, profile.Address, profile.Port, timeoutMs);
            client.NoDelay = true;
            string expectedPin = NormalizePin(profile.CertificateSha256);
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
            SslStream tls = new SslStream(client.GetStream(), false, validator);
            string targetName = String.IsNullOrWhiteSpace(profile.ServerName) ? profile.Address : profile.ServerName.Trim();
            Task auth = tls.AuthenticateAsClientAsync(targetName, null, SslProtocols.Tls12, false);
            if (await Task.WhenAny(auth, Task.Delay(timeoutMs)) != auth) throw new System.TimeoutException("TLS 握手超时。");
            await auth;
            return new TlsConnection { Stream=tls, Client=client };
        } catch { client.Close(); throw; }
    }

    private async void HealthLoop(AppConfig config, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested) {
            foreach (ServerProfile profile in EnabledProfiles(config)) {
                DateTime begin = DateTime.UtcNow;
                try {
                    TlsConnection connection = await OpenTls(profile, 5000);
                    using (connection.Client) using (SslStream tls = connection.Stream) {
                        string host = String.IsNullOrWhiteSpace(profile.ServerName) ? profile.Address : profile.ServerName.Trim();
                        byte[] bytes = Encoding.ASCII.GetBytes("CONNECT /relay/v2/health HTTP/1.1\r\nHost: " + host + "\r\nAuthorization: Bearer " + profile.SharedKey + "\r\nX-Site-Name: " + SafeHeader(config.SiteName) + "\r\n\r\n");
                        await tls.WriteAsync(bytes, 0, bytes.Length, cancellation); await tls.FlushAsync(cancellation);
                        string response = await ReadHeader(tls, cancellation);
                        if (!response.StartsWith("HTTP/1.1 200 ", StringComparison.Ordinal)) throw new IOException("认证失败");
                    }
                    MarkSuccess(profile, (int)(DateTime.UtcNow - begin).TotalMilliseconds);
                } catch (Exception ex) { if (!cancellation.IsCancellationRequested) MarkFailure(profile, FriendlyError(ex)); }
            }
            try { await Task.Delay(30000, cancellation); } catch { break; }
        }
    }

    private static List<ServerProfile> EnabledProfiles(AppConfig config)
    {
        List<ServerProfile> result = new List<ServerProfile>();
        foreach (ServerProfile p in config.Servers) if (p != null && p.Enabled && !String.IsNullOrWhiteSpace(p.Address)) result.Add(p);
        return result;
    }

    private async Task CopyAndCount(Stream input, Stream output, bool upload, MinerConnection connection, CancellationToken cancellation)
    {
        byte[] buffer = new byte[65536];
        while (!cancellation.IsCancellationRequested) {
            int count = await input.ReadAsync(buffer, 0, buffer.Length, cancellation);
            if (count <= 0) break;
            await output.WriteAsync(buffer, 0, count, cancellation);
            if (upload) Interlocked.Add(ref uploadedBytes, count); else Interlocked.Add(ref downloadedBytes, count);
            ObserveMiner(connection,buffer,count,upload);
        }
    }

    private void MarkSuccess(ServerProfile p, int latency) { lock(stateLock) { EndpointState s=GetState(p); s.Online=true; s.LatencyMs=latency; s.LastError=""; s.LastCheck=DateTime.Now; } }
    private void MarkFailure(ServerProfile p, string error) { lock(stateLock) { EndpointState s=GetState(p); s.Online=false; s.Failures++; s.LastError=error; s.LastCheck=DateTime.Now; } }
    private EndpointState GetState(ServerProfile p) { EndpointState s; if (!endpointStates.TryGetValue(p.Name, out s)) { s=new EndpointState{Name=p.Name}; endpointStates[p.Name]=s; } return s; }

    public RelaySnapshot Snapshot()
    {
        RelaySnapshot value = new RelaySnapshot { Running=IsRunning, Active=Volatile.Read(ref active), Total=Interlocked.Read(ref totalConnections), Failures=Interlocked.Read(ref failedConnections), Uploaded=Interlocked.Read(ref uploadedBytes), Downloaded=Interlocked.Read(ref downloadedBytes), StartedAt=startedAt };
        lock(stateLock) { foreach (EndpointState s in endpointStates.Values) value.Endpoints.Add(s.Copy()); foreach(MinerState miner in miners.Values)if(miner.Connections>0)value.ActiveMiners++; }
        return value;
    }

    private MinerConnection OpenMiner(string ip,PortRoute route)
    {
        lock(stateLock) {
            MinerState state; if(!miners.TryGetValue(ip,out state)){state=new MinerState{Ip=ip,FirstSeen=DateTime.Now,LastActivity=DateTime.Now};miners[ip]=state;}
            state.Connections++;state.LastActivity=DateTime.Now;state.LocalPorts.Add(route.LocalPort);state.RemotePorts.Add(route.RemotePort);
            return new MinerConnection{State=state};
        }
    }
    private void SetMinerEndpoint(MinerConnection c,string endpoint){lock(stateLock){c.State.Endpoint=endpoint;c.State.LastActivity=DateTime.Now;}}
    private void MinerFailed(MinerConnection c,string error){lock(stateLock){c.State.Failures++;c.State.LastError=error;c.State.LastActivity=DateTime.Now;}}
    private void CloseMiner(MinerConnection c){lock(stateLock){c.State.Connections=Math.Max(0,c.State.Connections-1);c.State.Disconnects++;c.State.LastActivity=DateTime.Now;}}
    private void ObserveMiner(MinerConnection c,byte[] bytes,int count,bool fromMiner)
    {
        lock(stateLock) {
            c.State.LastActivity=DateTime.Now;if(fromMiner)c.State.Uploaded+=count;else c.State.Downloaded+=count;
            c.Observe(bytes,count,fromMiner);
        }
    }
    public List<MinerSnapshot> MinerSnapshots()
    {
        lock(stateLock){List<MinerSnapshot> result=new List<MinerSnapshot>();foreach(MinerState state in miners.Values)result.Add(state.Snapshot(DateTime.Now));result.Sort(delegate(MinerSnapshot a,MinerSnapshot b){return String.Compare(a.Ip,b.Ip,StringComparison.OrdinalIgnoreCase);});return result;}
    }
    public bool RemoveMiner(string ip)
    {
        lock(stateLock){MinerState state;if(!miners.TryGetValue(ip,out state))return true;if(state.Connections>0)return false;miners.Remove(ip);MinerHistoryStore.Save(miners.Values);return true;}
    }
    public int RemoveExpiredMiners(TimeSpan offlineFor)
    {
        lock(stateLock){DateTime cutoff=DateTime.Now-offlineFor;List<string>remove=new List<string>();foreach(KeyValuePair<string,MinerState> item in miners)if(item.Value.Connections==0&&item.Value.LastActivity<cutoff)remove.Add(item.Key);foreach(string ip in remove)miners.Remove(ip);if(remove.Count>0)MinerHistoryStore.Save(miners.Values);return remove.Count;}
    }
    public void SaveMinerHistory()
    {
        lock(stateLock){try{MinerHistoryStore.Save(miners.Values);}catch(Exception ex){log("矿机历史保存失败："+ex.Message);}}
    }
    private void LoadMinerHistory()
    {
        try{
            foreach(MinerHistoryItem item in MinerHistoryStore.Load()){
                DateTime lastActivity=FromTicks(item.LastActivityTicks);if(lastActivity<DateTime.Now.AddHours(-24))continue;
                MinerState state=new MinerState{Ip=item.Ip,Connections=0,FirstSeen=FromTicks(item.FirstSeenTicks),LastActivity=lastActivity,LastAccepted=FromTicks(item.LastAcceptedTicks),Disconnects=item.Disconnects,Failures=item.Failures,Submitted=item.Submitted,Accepted=item.Accepted,Rejected=item.Rejected,Uploaded=item.Uploaded,Downloaded=item.Downloaded,LatencyTotal=item.LatencyTotal,LatencySamples=item.LatencySamples,Endpoint=item.Endpoint??"",LastError=item.LastError??""};
                foreach(string value in item.Workers??new List<string>())state.Workers.Add(value);foreach(string value in item.Agents??new List<string>())state.Agents.Add(value);foreach(int value in item.LocalPorts??new List<int>())state.LocalPorts.Add(value);foreach(ShareHistoryItem share in item.Shares??new List<ShareHistoryItem>())if(share.Time>=DateTime.Now.AddHours(-24))state.Shares.Add(new ShareEvent{Time=share.Time,Difficulty=share.Difficulty});miners[state.Ip]=state;
            }
        }catch{}
    }
    private static DateTime FromTicks(long ticks){return ticks<=0?DateTime.MinValue:new DateTime(ticks,DateTimeKind.Local);}

    private static async Task ConnectWithTimeout(TcpClient client, string host, int port, int timeoutMs)
    {
        Task connect = client.ConnectAsync(host, port);
        if (await Task.WhenAny(connect, Task.Delay(timeoutMs)) != connect) throw new System.TimeoutException("连接 VPS 超时。");
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
    private static string SafeHeader(string value) { return Regex.Replace(value ?? "", "[\r\n]", " ").Trim(); }
    private static string FriendlyError(Exception ex) {
        if (ex is AuthenticationException) return "TLS 证书验证失败。请核对证书名称或 SHA-256 指纹。";
        return ex.Message;
    }
}

[DataContract] public sealed class EndpointState { [DataMember]public string Name=""; [DataMember]public bool Online; [DataMember]public int LatencyMs; [DataMember]public long Failures; [DataMember]public string LastError=""; [DataMember]public DateTime LastCheck; public EndpointState Copy(){return (EndpointState)MemberwiseClone();} }
[DataContract] public sealed class RelaySnapshot { [DataMember]public bool Running; [DataMember]public int Active,ActiveMiners; [DataMember]public long Total,Failures,Uploaded,Downloaded; [DataMember]public DateTime StartedAt; [DataMember]public List<EndpointState> Endpoints=new List<EndpointState>(); }

public sealed class TlsConnection { public SslStream Stream; public TcpClient Client; }

public sealed class ShareEvent { public DateTime Time; public double Difficulty; }
public sealed class PendingShare { public DateTime Time; public double Difficulty; }
public sealed class MinerState
{
    public string Ip="",Endpoint="",LastError=""; public int Connections,Disconnects,Failures,Submitted,Accepted,Rejected; public long Uploaded,Downloaded; public double LatencyTotal; public int LatencySamples; public DateTime FirstSeen,LastActivity,LastAccepted;
    public readonly HashSet<int> LocalPorts=new HashSet<int>(); public readonly HashSet<int> RemotePorts=new HashSet<int>(); public readonly HashSet<string> Workers=new HashSet<string>(); public readonly HashSet<string> Agents=new HashSet<string>(); public readonly List<ShareEvent> Shares=new List<ShareEvent>();
    public MinerSnapshot Snapshot(DateTime now)
    {
        Shares.RemoveAll(delegate(ShareEvent e){return e.Time<now.AddHours(-24);});
        double reject=Submitted==0?0:(double)Rejected*100/Submitted; double idle=Math.Max(0,(now-LastActivity).TotalSeconds); int health=100;
        if(Connections==0)health=0;else{if(idle>120)health-=50;else if(idle>45)health-=15;if(reject>5)health-=30;else if(reject>1)health-=10;if(Failures>0)health-=Math.Min(15,Failures*3);if(LastAccepted!=DateTime.MinValue&&(now-LastAccepted).TotalMinutes>30)health-=15;}
        health=Math.Max(0,Math.Min(100,health));string text=Connections==0?"离线":health>=85?"健康":health>=60?"注意":"异常";
        return new MinerSnapshot{Ip=Ip,Health=health,HealthText=text,Connections=Connections,Worker=Join(Workers),Agent=Join(Agents),Endpoint=Endpoint,Ports=JoinPorts(LocalPorts),FirstSeen=FirstSeen,LastActivity=LastActivity,Uploaded=Uploaded,Downloaded=Downloaded,Disconnects=Disconnects,Failures=Failures,Submitted=Submitted,Accepted=Accepted,Rejected=Rejected,RejectPercent=reject,LatencyMs=LatencySamples==0?0:(int)(LatencyTotal/LatencySamples),LastAccepted=LastAccepted,Hashrate10m=Estimate(now,TimeSpan.FromMinutes(10)),Hashrate1h=Estimate(now,TimeSpan.FromHours(1)),Hashrate24h=Estimate(now,TimeSpan.FromHours(24)),LastError=LastError};
    }
    private double Estimate(DateTime now,TimeSpan window){DateTime cutoff=now-window;double sum=0;foreach(ShareEvent e in Shares)if(e.Time>=cutoff)sum+=e.Difficulty;double observed=Math.Min(window.TotalSeconds,Math.Max(60,(now-FirstSeen).TotalSeconds));return sum*4294967296.0/observed;}
    private static string Join(HashSet<string> values){string[] a=new string[values.Count];values.CopyTo(a);return String.Join("，",a);}
    private static string JoinPorts(HashSet<int> values){List<int>a=new List<int>(values);a.Sort();List<string>s=new List<string>();foreach(int v in a)s.Add(v.ToString());return String.Join(",",s.ToArray());}
}
public sealed class MinerSnapshot
{
    public string Ip="",HealthText="",Worker="",Agent="",Endpoint="",Ports="",LastError="";public int Health,Connections,Disconnects,Failures,Submitted,Accepted,Rejected,LatencyMs;public long Uploaded,Downloaded;public double RejectPercent,Hashrate10m,Hashrate1h,Hashrate24h;public DateTime FirstSeen,LastActivity,LastAccepted;
}
public sealed class MinerConnection
{
    public MinerState State; private readonly JavaScriptSerializer json=new JavaScriptSerializer(); private string upBuffer="",downBuffer=""; private double difficulty; private readonly Dictionary<string,PendingShare> pending=new Dictionary<string,PendingShare>();
    public void Observe(byte[] bytes,int count,bool fromMiner)
    {
        string buffer=(fromMiner?upBuffer:downBuffer)+Encoding.UTF8.GetString(bytes,0,count);int lineEnd;
        while((lineEnd=buffer.IndexOf('\n'))>=0){string line=buffer.Substring(0,lineEnd).Trim();buffer=buffer.Substring(lineEnd+1);if(line.Length>0&&line.Length<1048576)Parse(line,fromMiner);}
        if(buffer.Length>1048576)buffer="";if(fromMiner)upBuffer=buffer;else downBuffer=buffer;
    }
    private void Parse(string line,bool fromMiner)
    {
        Dictionary<string,object> message;try{message=json.DeserializeObject(line) as Dictionary<string,object>;}catch{return;}if(message==null)return;
        object methodValue;string method=message.TryGetValue("method",out methodValue)&&methodValue!=null?methodValue.ToString():"";IList parameters=Parameters(message);
        if(fromMiner){
            if(method=="mining.subscribe"&&parameters.Count>0&&parameters[0]!=null)State.Agents.Add(parameters[0].ToString());
            else if(method=="mining.authorize"&&parameters.Count>0&&parameters[0]!=null)State.Workers.Add(parameters[0].ToString());
            else if(method=="mining.submit"&&parameters.Count>0){State.Submitted++;if(parameters[0]!=null)State.Workers.Add(parameters[0].ToString());object id;if(message.TryGetValue("id",out id))pending[json.Serialize(id)]=new PendingShare{Time=DateTime.UtcNow,Difficulty=difficulty};}
            return;
        }
        if(method=="mining.set_difficulty"&&parameters.Count>0){double value;if(Double.TryParse(Convert.ToString(parameters[0],System.Globalization.CultureInfo.InvariantCulture),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out value))difficulty=value;return;}
        object responseId;if(!message.TryGetValue("id",out responseId))return;PendingShare share;if(!pending.TryGetValue(json.Serialize(responseId),out share))return;pending.Remove(json.Serialize(responseId));
        object result,error;bool accepted=message.TryGetValue("result",out result)&&result is bool&&(bool)result&&(!message.TryGetValue("error",out error)||error==null);double latency=(DateTime.UtcNow-share.Time).TotalMilliseconds;State.LatencyTotal+=latency;State.LatencySamples++;
        if(accepted){State.Accepted++;State.LastAccepted=DateTime.Now;State.Shares.Add(new ShareEvent{Time=DateTime.Now,Difficulty=share.Difficulty});State.LastError="";}else{State.Rejected++;State.LastError="Share 被矿池拒绝";}
    }
    private static IList Parameters(Dictionary<string,object> message){object value;if(message.TryGetValue("params",out value)&&value is IList)return (IList)value;return new object[0];}
}

[DataContract] public sealed class MinerHistoryFile { [DataMember]public List<MinerHistoryItem> Miners=new List<MinerHistoryItem>(); }
[DataContract] public sealed class ShareHistoryItem { [DataMember]public DateTime Time;[DataMember]public double Difficulty; }
[DataContract] public sealed class MinerHistoryItem
{
    [DataMember]public string Ip="",Endpoint="",LastError="";[DataMember]public int Disconnects,Failures,Submitted,Accepted,Rejected,LatencySamples;[DataMember]public long Uploaded,Downloaded,FirstSeenTicks,LastActivityTicks,LastAcceptedTicks;[DataMember]public double LatencyTotal;[DataMember]public List<int> LocalPorts=new List<int>();[DataMember]public List<string> Workers=new List<string>(),Agents=new List<string>();[DataMember]public List<ShareHistoryItem> Shares=new List<ShareHistoryItem>();
}
public static class MinerHistoryStore
{
    public static readonly string FilePath=Path.Combine(ConfigStore.Folder,"miner-history.json");
    public static void Save(IEnumerable<MinerState> states)
    {
        DateTime cutoff=DateTime.Now.AddHours(-24);MinerHistoryFile file=new MinerHistoryFile();foreach(MinerState s in states){if(s.LastActivity<cutoff)continue;MinerHistoryItem item=new MinerHistoryItem{Ip=s.Ip,Endpoint=s.Endpoint,LastError=s.LastError,Disconnects=s.Disconnects,Failures=s.Failures,Submitted=s.Submitted,Accepted=s.Accepted,Rejected=s.Rejected,LatencySamples=s.LatencySamples,Uploaded=s.Uploaded,Downloaded=s.Downloaded,LatencyTotal=s.LatencyTotal,FirstSeenTicks=s.FirstSeen.Ticks,LastActivityTicks=s.LastActivity.Ticks,LastAcceptedTicks=s.LastAccepted.Ticks,LocalPorts=new List<int>(s.LocalPorts),Workers=new List<string>(s.Workers),Agents=new List<string>(s.Agents)};foreach(ShareEvent e in s.Shares)if(e.Time>=cutoff)item.Shares.Add(new ShareHistoryItem{Time=e.Time,Difficulty=e.Difficulty});file.Miners.Add(item);}
        Directory.CreateDirectory(ConfigStore.Folder);string temporary=FilePath+".tmp";using(FileStream stream=File.Create(temporary))new DataContractJsonSerializer(typeof(MinerHistoryFile)).WriteObject(stream,file);if(File.Exists(FilePath))File.Replace(temporary,FilePath,null);else File.Move(temporary,FilePath);
    }
    public static List<MinerHistoryItem> Load(){try{using(FileStream stream=File.OpenRead(FilePath)){MinerHistoryFile file=(MinerHistoryFile)new DataContractJsonSerializer(typeof(MinerHistoryFile)).ReadObject(stream);return file.Miners??new List<MinerHistoryItem>();}}catch{return new List<MinerHistoryItem>();}}
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
    private readonly TextBox siteName = new TextBox();
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
    private readonly System.Windows.Forms.Timer statusTimer = new System.Windows.Forms.Timer();
    private readonly Label status = new Label();
    private List<ServerProfile> backupProfiles = new List<ServerProfile>();
    private int statusTicks;
    private bool exiting;

    public MainForm()
    {
        Text = "木林森中转";
        Font = new Font("Microsoft YaHei UI", 9F);
        ClientSize = new Size(820, 820);
        MinimumSize = new Size(760, 720);
        StartPosition = FormStartPosition.CenterScreen;
        manager = new RelayManager(Log);
        BuildUi();
        LoadConfig();
        ports.TextChanged += delegate { RefreshMinerAddresses(false); };
        RefreshMinerAddresses(false);
        ipTimer.Interval = 30000;
        ipTimer.Tick += delegate { RefreshMinerAddresses(false); };
        ipTimer.Start();
        statusTimer.Interval = 1000;
        statusTimer.Tick += delegate { RefreshStatus(); };
        statusTimer.Start();
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
        grid.Dock = DockStyle.Top; grid.Height = 462; grid.Padding = new Padding(18, 14, 18, 4);
        grid.ColumnCount = 2; grid.RowCount = 12;
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 165));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int i=0; i<12; i++) grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        AddRow(grid, 0, "矿场名称", siteName, "例如 一号矿场；用于心跳和离线告警识别");
        AddRow(grid, 1, "主 VPS 地址", server, "例如 203.0.113.10");
        tlsPort.Minimum = 1; tlsPort.Maximum = 65535; tlsPort.Value = 443;
        AddRow(grid, 2, "TLS 端口", tlsPort, "默认 443");
        AddRow(grid, 3, "证书名称（可选）", serverName, "使用正规域名证书时填写域名");
        AddRow(grid, 4, "证书 SHA-256（可选）", pin, "使用 IP/自签名证书时填写 VPS 安装脚本输出的指纹");
        token.UseSystemPasswordChar = true;
        AddRow(grid, 5, "共享密钥", token, "VPS 为这台值守电脑生成的独立密钥");
        AddRow(grid, 6, "本地监听地址", listen, "0.0.0.0 表示接受局域网矿机连接");
        AddRow(grid, 7, "端口或端口映射", ports, "9999 表示同端口；10041=10001 表示本地 10041 转到 VPS 10001");
        autoStart.Text = "开机自动启动";
        autoStart.AutoSize = true;
        grid.Controls.Add(autoStart, 1, 8);
        closeToTray.Text = "点击关闭最小化";
        closeToTray.AutoSize = true;
        grid.Controls.Add(closeToTray, 1, 9);
        currentIp.ReadOnly = true;
        currentIp.BackColor = Color.White;
        Button refreshIp = new Button(); refreshIp.Text = "刷新"; refreshIp.AutoSize = true; refreshIp.Click += delegate { RefreshMinerAddresses(true); };
        AddRow(grid, 10, "当前局域网 IP", InlineControls(currentIp, refreshIp), "自动识别矿机应连接的值守电脑局域网 IP");
        minerAddress.DropDownStyle = ComboBoxStyle.DropDownList;
        Button copyAddress = new Button(); copyAddress.Text = "复制地址"; copyAddress.AutoSize = true; copyAddress.Click += delegate { CopyMinerAddress(); };
        AddRow(grid, 11, "矿机填写地址", InlineControls(minerAddress, copyAddress), "选择端口后复制完整的 stratum+tcp 地址");
        Controls.Add(grid);

        FlowLayoutPanel buttons = new FlowLayoutPanel();
        buttons.Dock = DockStyle.Top; buttons.Height = 52; buttons.Padding = new Padding(180, 6, 0, 0);
        Button save = new Button(); save.Text = "保存设置"; save.AutoSize = true; save.Click += delegate { SaveConfig(true); };
        Button backups = new Button(); backups.Text = "备用 VPS 设置"; backups.AutoSize = true; backups.Click += delegate { EditBackups(); };
        Button miners = new Button(); miners.Text="矿机状态"; miners.AutoSize=true; miners.Click+=delegate{new MinerStatusForm(manager).Show(this);};
        Button help = new Button(); help.Text = "各项说明"; help.AutoSize = true; help.Click += delegate { ShowHelp(); };
        start.Text = "启动中转"; start.AutoSize = true; start.Click += delegate { StartRelay(); };
        stop.Text = "停止"; stop.AutoSize = true; stop.Enabled = false; stop.Click += delegate { manager.Stop(); SetRunning(false); };
        buttons.Controls.Add(save); buttons.Controls.Add(backups); buttons.Controls.Add(miners); buttons.Controls.Add(start); buttons.Controls.Add(stop); buttons.Controls.Add(help);
        Controls.Add(buttons); buttons.BringToFront();

        status.Text = "状态：未启动"; status.Dock = DockStyle.Top; status.Height = 62; status.Padding = new Padding(18, 7, 18, 4);
        status.BackColor = Color.FromArgb(236, 244, 252); status.AutoEllipsis = true;
        Controls.Add(status); status.BringToFront();

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
        ServerProfile primary = c.Servers[0];
        siteName.Text=c.SiteName; server.Text = primary.Address; tlsPort.Value = Math.Max(1, Math.Min(65535, primary.Port)); serverName.Text = primary.ServerName;
        pin.Text = primary.CertificateSha256; token.Text = primary.SharedKey; listen.Text = c.ListenAddress; ports.Text = c.Ports; autoStart.Checked = c.AutoStart; closeToTray.Checked = c.CloseToTray;
        backupProfiles.Clear(); backupProfiles.Add(c.Servers[1].Copy()); backupProfiles.Add(c.Servers[2].Copy());
        Log("请填写 VPS 安装脚本输出的设置，然后启动中转。");
    }

    private AppConfig CurrentConfig()
    {
        AppConfig c = new AppConfig { SiteName=siteName.Text.Trim(), ListenAddress=listen.Text.Trim(), Ports=ports.Text.Trim(), AutoStart=autoStart.Checked, CloseToTray=closeToTray.Checked };
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
        if (String.IsNullOrWhiteSpace(p.Address)) throw new InvalidOperationException(p.Name + "：请填写 VPS 地址。");
        if ((p.SharedKey ?? "").Length < 32 || !Regex.IsMatch(p.SharedKey ?? "", "^[A-Za-z0-9_-]+$")) throw new InvalidOperationException(p.Name + "：共享密钥格式不正确。");
        if (String.IsNullOrWhiteSpace(p.CertificateSha256) && String.IsNullOrWhiteSpace(p.ServerName)) throw new InvalidOperationException(p.Name + "：请填写证书名称或证书 SHA-256 指纹。");
    }

    private void SaveConfig(bool showMessage)
    {
        ValidateSettings();
        AppConfig c = CurrentConfig(); ConfigStore.Save(c); SetAutoStart(c.AutoStart);
        if (showMessage) Log("设置已保存。共享密钥已使用当前 Windows 用户加密保存。");
    }

    private void StartRelay()
    {
        try { List<PortRoute> routePorts = ValidateSettings(); SaveConfig(false); manager.Start(CurrentConfig(), routePorts); SetRunning(true); }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, "无法启动", MessageBoxButtons.OK, MessageBoxIcon.Warning); Log("启动失败：" + ex.Message); }
    }

    private void SetRunning(bool running)
    {
        start.Enabled = !running; stop.Enabled = running;
        siteName.Enabled = server.Enabled = tlsPort.Enabled = serverName.Enabled = pin.Enabled = token.Enabled = listen.Enabled = ports.Enabled = !running;
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
            "当前局域网 IP：值守电脑在矿机局域网里的地址。\r\n\r\n" +
            "矿机填写地址：已经补全的挖矿地址，选择后可以直接复制到矿机后台。";
        message += "\r\n\r\n矿机状态：按局域网 IP 合并显示连接、Worker、Share、响应时间和估算算力。双击矿机可查看检修详情。";
        MessageBox.Show(this, message, "各项设置说明", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    private void EditBackups()
    {
        using (BackupForm form = new BackupForm(backupProfiles)) if (form.ShowDialog(this) == DialogResult.OK) backupProfiles = form.Profiles;
    }

    private void RefreshStatus()
    {
        if(++statusTicks>=30){statusTicks=0;manager.SaveMinerHistory();}
        RelaySnapshot s=manager.Snapshot();
        string line=s.Running ? "运行中" : "未启动";
        string uptime=s.Running ? FormatDuration(DateTime.Now-s.StartedAt) : "--";
        List<string> endpoints=new List<string>(); foreach(EndpointState e in s.Endpoints) endpoints.Add(e.Name+":"+(e.Online ? "正常 "+e.LatencyMs+"ms" : "异常"));
        start.Enabled=!manager.IsRunning;
        status.Text="状态："+line+"    当前矿机："+s.ActiveMiners+"    当前连接："+s.Active+"    累计连接："+s.Total+"    失败："+s.Failures+"    运行："+uptime+"\r\n流量：上传 "+FormatBytes(s.Uploaded)+" / 下载 "+FormatBytes(s.Downloaded)+(endpoints.Count==0 ? "" : "    线路："+String.Join("，",endpoints.ToArray()));
    }
    private static string FormatDuration(TimeSpan t){ return ((int)t.TotalDays>0 ? ((int)t.TotalDays)+"天 " : "")+t.Hours.ToString("00")+":"+t.Minutes.ToString("00")+":"+t.Seconds.ToString("00"); }
    private static string FormatBytes(long value){ string[] u={"B","KB","MB","GB","TB"}; double n=value; int i=0; while(n>=1024&&i<u.Length-1){n/=1024;i++;} return n.ToString(i==0?"0":"0.0")+" "+u[i]; }

    private void Log(string message)
    {
        if (InvokeRequired) { BeginInvoke(new Action<string>(Log), message); return; }
        logs.AppendText(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message + Environment.NewLine);
    }

    private void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (!exiting && closeToTray.Checked && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; Hide(); tray.ShowBalloonTip(1500, "木林森中转", "程序仍在后台运行。", ToolTipIcon.Info); return; }
        ipTimer.Stop(); statusTimer.Stop(); manager.SaveMinerHistory(); manager.Stop(); tray.Visible = false;
    }
}

public sealed class MinerStatusForm : Form
{
    private readonly RelayManager manager; private readonly DataGridView grid=new DataGridView(); private readonly Label summary=new Label();
    public MinerStatusForm(RelayManager relay)
    {
        manager=relay;Text="木林森中转 - 矿机状态";Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(1450,620);MinimumSize=new Size(980,500);StartPosition=FormStartPosition.CenterParent;Icon appIcon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);if(appIcon!=null)Icon=appIcon;
        summary.Dock=DockStyle.Top;summary.Height=42;summary.Padding=new Padding(12,11,0,0);summary.BackColor=Color.FromArgb(236,244,252);Controls.Add(summary);
        FlowLayoutPanel actions=new FlowLayoutPanel();actions.Dock=DockStyle.Top;actions.Height=43;actions.Padding=new Padding(10,6,0,0);Button refresh=new Button();refresh.Text="刷新";refresh.AutoSize=true;refresh.Click+=delegate{RefreshRows();};Button remove=new Button();remove.Text="删除选中记录";remove.AutoSize=true;remove.Click+=delegate{RemoveSelected();};Button prune=new Button();prune.Text="清理离线超过24小时";prune.AutoSize=true;prune.Click+=delegate{int count=manager.RemoveExpiredMiners(TimeSpan.FromHours(24));RefreshRows();MessageBox.Show(this,"已清理 "+count+" 条记录。","清理完成",MessageBoxButtons.OK,MessageBoxIcon.Information);};actions.Controls.Add(refresh);actions.Controls.Add(remove);actions.Controls.Add(prune);Controls.Add(actions);actions.BringToFront();
        grid.Dock=DockStyle.Fill;grid.ReadOnly=true;grid.AllowUserToAddRows=false;grid.AllowUserToDeleteRows=false;grid.AutoSizeRowsMode=DataGridViewAutoSizeRowsMode.AllCells;grid.SelectionMode=DataGridViewSelectionMode.FullRowSelect;grid.RowHeadersVisible=false;grid.BackgroundColor=Color.White;grid.AutoGenerateColumns=false;
        Add("IP","矿机 IP",110);Add("Health","健康度",80);Add("Connections","连接",55);Add("Worker","矿工名",165);Add("Agent","矿机软件/型号",145);Add("Endpoint","线路",75);Add("Ports","本地端口",90);Add("LastActivity","最近活动",125);Add("Shares","提交/接受/拒绝",115);Add("Reject","拒绝率",65);Add("Latency","响应",65);Add("Hash10","10分钟估算算力",115);Add("Hash1","1小时估算算力",115);Add("Hash24","24小时估算算力",115);Add("Traffic","流量 上/下",110);Add("Disconnects","断线/失败",75);
        grid.CellDoubleClick+=delegate(object sender,DataGridViewCellEventArgs e){if(e.RowIndex>=0&&grid.Rows[e.RowIndex].Tag is MinerSnapshot)ShowDetail((MinerSnapshot)grid.Rows[e.RowIndex].Tag);};
        grid.SortCompare+=SortCompare;Controls.Add(grid);grid.BringToFront();RefreshRows();
    }
    private void RemoveSelected(){if(grid.SelectedRows.Count==0){MessageBox.Show(this,"请先选择一台矿机。","提示",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}MinerSnapshot m=grid.SelectedRows[0].Tag as MinerSnapshot;if(m==null)return;if(m.Connections>0){MessageBox.Show(this,"这台矿机仍在连接，不能删除。请先确认旧 IP 已经离线。","无法删除",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}if(MessageBox.Show(this,"确定删除矿机 "+m.Ip+" 的历史记录吗？","删除记录",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;if(!manager.RemoveMiner(m.Ip)){MessageBox.Show(this,"矿机刚刚重新连接，记录没有删除。","无法删除",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}RefreshRows();}
    private void Add(string name,string title,int width){grid.Columns.Add(new DataGridViewTextBoxColumn{Name=name,HeaderText=title,Width=width,SortMode=DataGridViewColumnSortMode.Automatic});}
    private void RefreshRows()
    {
        DataGridViewColumn sorted=grid.SortedColumn;SortOrder order=grid.SortOrder;List<MinerSnapshot> items=manager.MinerSnapshots();int online=0,warn=0;grid.Rows.Clear();foreach(MinerSnapshot m in items){if(m.Connections>0)online++;if(m.Health>0&&m.Health<85)warn++;int index=grid.Rows.Add(m.Ip,m.HealthText+" "+m.Health,m.Connections,m.Worker,m.Agent,m.Endpoint,m.Ports,m.LastActivity.ToString("MM-dd HH:mm:ss"),m.Submitted+" / "+m.Accepted+" / "+m.Rejected,m.RejectPercent.ToString("0.00")+"%",m.LatencyMs+" ms",FormatHashrate(m.Hashrate10m),FormatHashrate(m.Hashrate1h),FormatHashrate(m.Hashrate24h),FormatBytes(m.Uploaded)+" / "+FormatBytes(m.Downloaded),m.Disconnects+" / "+m.Failures);DataGridViewRow row=grid.Rows[index];row.Tag=m;if(m.Connections==0)row.DefaultCellStyle.ForeColor=Color.Gray;else if(m.Health<60)row.DefaultCellStyle.BackColor=Color.MistyRose;else if(m.Health<85)row.DefaultCellStyle.BackColor=Color.LemonChiffon;}if(sorted!=null&&order!=SortOrder.None)grid.Sort(sorted,order==SortOrder.Ascending?ListSortDirection.Ascending:ListSortDirection.Descending);
        summary.Text="识别矿机："+items.Count+"    当前在线："+online+"    需要注意："+warn+"    相同 IP 已合并；点击刷新获取最新信息，点击表头排序。";
    }
    private void SortCompare(object sender,DataGridViewSortCompareEventArgs e){MinerSnapshot a=grid.Rows[e.RowIndex1].Tag as MinerSnapshot,b=grid.Rows[e.RowIndex2].Tag as MinerSnapshot;if(a==null||b==null)return;IComparable left=SortValue(e.Column.Name,a),right=SortValue(e.Column.Name,b);e.SortResult=left.CompareTo(right);e.Handled=true;}
    private static IComparable SortValue(string column,MinerSnapshot m){switch(column){case"IP":return IpNumber(m.Ip);case"Health":return m.Health;case"Connections":return m.Connections;case"LastActivity":return m.LastActivity;case"Shares":return m.Submitted;case"Reject":return m.RejectPercent;case"Latency":return m.LatencyMs;case"Hash10":return m.Hashrate10m;case"Hash1":return m.Hashrate1h;case"Hash24":return m.Hashrate24h;case"Traffic":return m.Uploaded+m.Downloaded;case"Disconnects":return m.Disconnects+m.Failures;case"Worker":return m.Worker??"";case"Agent":return m.Agent??"";case"Endpoint":return m.Endpoint??"";case"Ports":return m.Ports??"";default:return "";}}
    private static long IpNumber(string value){IPAddress ip;if(!IPAddress.TryParse(value,out ip))return Int64.MaxValue;byte[] b=ip.GetAddressBytes();if(b.Length!=4)return Int64.MaxValue;return ((long)b[0]<<24)|((long)b[1]<<16)|((long)b[2]<<8)|b[3];}
    private void ShowDetail(MinerSnapshot m){string accepted=m.LastAccepted==DateTime.MinValue?"尚未接受 Share":m.LastAccepted.ToString("yyyy-MM-dd HH:mm:ss");string message="矿机 IP："+m.Ip+"\r\n健康度："+m.HealthText+" "+m.Health+"\r\n当前连接："+m.Connections+"\r\n矿工名："+(m.Worker.Length==0?"尚未识别":m.Worker)+"\r\n矿机软件/型号："+(m.Agent.Length==0?"尚未识别":m.Agent)+"\r\n线路与端口："+m.Endpoint+" / "+m.Ports+"\r\n首次连接："+m.FirstSeen.ToString("yyyy-MM-dd HH:mm:ss")+"\r\n最近活动："+m.LastActivity.ToString("yyyy-MM-dd HH:mm:ss")+"\r\n最近接受："+accepted+"\r\n断线 / 失败："+m.Disconnects+" / "+m.Failures+"\r\n最近错误："+(m.LastError.Length==0?"无":m.LastError);MessageBox.Show(this,message,"矿机检修详情",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    private static string FormatBytes(long value){string[]u={"B","KB","MB","GB","TB"};double n=value;int i=0;while(n>=1024&&i<u.Length-1){n/=1024;i++;}return n.ToString(i==0?"0":"0.0")+u[i];}
    private static string FormatHashrate(double value){string[]u={"H/s","KH/s","MH/s","GH/s","TH/s","PH/s","EH/s"};int i=0;while(value>=1000&&i<u.Length-1){value/=1000;i++;}return value<=0?"--":value.ToString(value>=100?"0":value>=10?"0.0":"0.00")+" "+u[i];}
}

public sealed class BackupForm : Form
{
    private readonly List<ServerEditor> editors = new List<ServerEditor>();
    public List<ServerProfile> Profiles = new List<ServerProfile>();
    public BackupForm(List<ServerProfile> profiles)
    {
        Text="备用 VPS 设置"; Font=new Font("Microsoft YaHei UI",9F); ClientSize=new Size(690,430); StartPosition=FormStartPosition.CenterParent;
        TabControl tabs=new TabControl(); tabs.Dock=DockStyle.Fill;
        for(int i=0;i<2;i++) { ServerProfile p=i<profiles.Count?profiles[i].Copy():new ServerProfile{Name="备用VPS "+(i+1),Enabled=false,Port=443}; ServerEditor editor=new ServerEditor(p); editors.Add(editor); TabPage page=new TabPage("备用 VPS "+(i+1)); page.Controls.Add(editor); tabs.TabPages.Add(page); }
        FlowLayoutPanel buttons=new FlowLayoutPanel(); buttons.Dock=DockStyle.Bottom; buttons.Height=48; buttons.FlowDirection=FlowDirection.RightToLeft; buttons.Padding=new Padding(0,8,12,0);
        Button ok=new Button(); ok.Text="保存"; ok.AutoSize=true; ok.Click+=delegate { try { Profiles.Clear(); foreach(ServerEditor e in editors){ServerProfile p=e.Value(); if(p.Enabled) MainFormValidate(p); Profiles.Add(p);} DialogResult=DialogResult.OK; Close(); } catch(Exception ex){MessageBox.Show(this,ex.Message,"设置有误",MessageBoxButtons.OK,MessageBoxIcon.Warning);} };
        Button cancel=new Button(); cancel.Text="取消"; cancel.AutoSize=true; cancel.DialogResult=DialogResult.Cancel; buttons.Controls.Add(ok); buttons.Controls.Add(cancel);
        Controls.Add(tabs); Controls.Add(buttons); AcceptButton=ok; CancelButton=cancel;
    }
    private static void MainFormValidate(ServerProfile p) { if(String.IsNullOrWhiteSpace(p.Address)) throw new InvalidOperationException(p.Name+"：请填写 VPS 地址。"); if((p.SharedKey??"").Length<32||!Regex.IsMatch(p.SharedKey??"","^[A-Za-z0-9_-]+$")) throw new InvalidOperationException(p.Name+"：共享密钥格式不正确。"); if(String.IsNullOrWhiteSpace(p.CertificateSha256)&&String.IsNullOrWhiteSpace(p.ServerName)) throw new InvalidOperationException(p.Name+"：请填写证书名称或证书指纹。"); }
}

public sealed class ServerEditor : Panel
{
    private readonly CheckBox enabled=new CheckBox(); private readonly TextBox address=new TextBox(); private readonly NumericUpDown port=new NumericUpDown(); private readonly TextBox serverName=new TextBox(); private readonly TextBox pin=new TextBox(); private readonly TextBox key=new TextBox(); private readonly string profileName;
    public ServerEditor(ServerProfile p)
    {
        profileName=p.Name; Dock=DockStyle.Fill; TableLayoutPanel grid=new TableLayoutPanel(); grid.Dock=DockStyle.Top; grid.Padding=new Padding(18); grid.ColumnCount=2; grid.RowCount=6; grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,150)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        enabled.Text="启用这条备用线路"; enabled.Checked=p.Enabled; enabled.AutoSize=true; Add(grid,0,"状态",enabled);
        address.Text=p.Address; Add(grid,1,"VPS 地址",address); port.Minimum=1;port.Maximum=65535;port.Value=Math.Max(1,Math.Min(65535,p.Port));Add(grid,2,"TLS 端口",port);
        serverName.Text=p.ServerName;Add(grid,3,"证书名称（可选）",serverName);pin.Text=p.CertificateSha256;Add(grid,4,"证书 SHA-256（可选）",pin);key.Text=p.SharedKey;key.UseSystemPasswordChar=true;Add(grid,5,"独立共享密钥",key); Controls.Add(grid);
    }
    private static void Add(TableLayoutPanel g,int row,string text,Control c){g.RowStyles.Add(new RowStyle(SizeType.Absolute,48));Label l=new Label();l.Text=text;l.Dock=DockStyle.Fill;l.TextAlign=ContentAlignment.MiddleLeft;c.Dock=DockStyle.Fill;c.Margin=new Padding(3,8,3,8);g.Controls.Add(l,0,row);g.Controls.Add(c,1,row);}
    public ServerProfile Value(){return new ServerProfile{Name=profileName,Enabled=enabled.Checked,Address=address.Text.Trim(),Port=(int)port.Value,ServerName=serverName.Text.Trim(),CertificateSha256=pin.Text.Trim(),SharedKey=key.Text.Trim()};}
}

public static class Program
{
    [STAThread]
    public static void Main()
    {
        bool created;
        using (Mutex single = new Mutex(true, "Local\\MulinSenSecureRelayV2", out created)) {
            if (!created) { MessageBox.Show("木林森中转已经在运行，请查看任务栏右下角托盘。", "木林森中转", MessageBoxButtons.OK, MessageBoxIcon.Information); return; }
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new MainForm());
        }
    }
}
