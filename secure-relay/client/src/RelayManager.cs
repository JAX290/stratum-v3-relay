using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
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
using System.Web.Script.Serialization;
using Microsoft.Win32;

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
    private readonly RelayFailoverController failover = new RelayFailoverController();
    private readonly Dictionary<string, MinerState> miners = new Dictionary<string, MinerState>();
    private string recoveryPhase="",recoveryMessage="";
    private DateTime recoveryUpdatedAt=DateTime.MinValue;
    // Only connection setup is limited. Established mining connections do not occupy a slot.
    // This prevents hundreds of reconnecting miners from creating hundreds of slow VPS probes at once.
    private readonly SemaphoreSlim connectionSetupSlots = new SemaphoreSlim(32, 32);
    private readonly HashSet<string> remoteActionIds=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    public event Action<RemoteClientAction> RemoteActionRequested;
    public bool IsRunning { get { return stop != null; } }
    public bool LocalHealthCheck()
    {
        if(!Snapshot().Running||listeners.Count==0)return false;
        try {foreach(TcpListener listener in listeners)if(!listener.Server.IsBound)return false;}
        catch {return false;}
        return true;
    }

    public RelayManager(Action<string> logger) { log = logger; LoadMinerHistory(); }

    public void Start(AppConfig config, IList<PortRoute> routes)
    {
        if (IsRunning) return;
        config.Normalize();
        failover.Reset(config.Servers, DateTime.UtcNow);
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

    public void SetRecoveryState(string phase,string message)
    {
        lock(stateLock){recoveryPhase=phase??"";recoveryMessage=message??"";recoveryUpdatedAt=DateTime.Now;}
        if(!String.IsNullOrWhiteSpace(message))log(message);
    }

    public void SelectVerifiedEndpoint(ServerProfile profile,bool primary)
    {
        failover.SelectVerifiedEndpoint(profile,primary,DateTime.UtcNow);
        log("检查并修复已选择验证通过的"+profile.Name+"，后续新连接使用该线路。");
    }

    private async void AcceptLoop(TcpListener listener, AppConfig config, PortRoute route, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested) {
            try {
                TcpClient miner = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
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
        bool setupSlot = false;
        try {
            miner.NoDelay = true;
            setupSlot = await connectionSetupSlots.WaitAsync(15000, cancellation).ConfigureAwait(false);
            if (!setupSlot) throw new IOException("VPS连接排队过多，已保护性拒绝本次连接，请稍后自动重试。");
            ServerProfile selected = null;
            Exception last = null;
            int minerPort = endpoint == null ? 1 : endpoint.Port;
            foreach (ServerProfile profile in failover.ConnectionCandidates(config.Servers, DateTime.UtcNow)) {
                try {
                    TlsConnection connection = await OpenTls(profile, 10000, cancellation).ConfigureAwait(false); tls = connection.Stream; remote = connection.Client;
                    string targetName = String.IsNullOrWhiteSpace(profile.ServerName) ? profile.Address : profile.ServerName.Trim();
                    string request = "CONNECT /relay/v1/" + route.RemotePort + " HTTP/1.1\r\n" +
                        "Host: " + targetName + "\r\nUser-Agent: MulinSenRelay/2.0\r\nAuthorization: Bearer " + profile.SharedKey + "\r\n" +
                        "X-Site-Name: " + SafeHeader(config.SiteName) + "\r\nX-Client-Version: " + AppBrand.Version + "\r\nX-Miner-IP: " + minerIp + "\r\nX-Miner-Port: " + minerPort + "\r\n\r\n";
                    byte[] requestBytes = Encoding.ASCII.GetBytes(request);
                    string response = await ExchangeHeaderWithTimeout(tls, remote, requestBytes, 10000, cancellation).ConfigureAwait(false);
                    if (!response.StartsWith("HTTP/1.1 200 ", StringComparison.Ordinal)) throw new IOException("VPS 拒绝认证或没有这个转发端口。");
                    bool primary=Object.ReferenceEquals(profile,config.Servers[0]);
                    string previousEndpoint=failover.CurrentEndpoint;
                    if(!failover.ConnectionSucceeded(profile,primary,DateTime.UtcNow)){
                        last=new IOException(profile.Name+"仍在恢复观察或切换冷却中。");
                        try{tls.Dispose();}catch{}try{remote.Close();}catch{}tls=null;remote=null;continue;
                    }
                    if(!String.Equals(previousEndpoint,profile.Name,StringComparison.OrdinalIgnoreCase))log("线路已切换："+previousEndpoint+" → "+profile.Name+"。后续新连接使用新线路，已有连接保持不变。");
                    selected = profile; MarkSuccess(profile, 0); SetMinerEndpoint(minerState,profile.Name); break;
                } catch (Exception ex) {
                    last = ex; MarkFailure(profile, FriendlyError(ex));
                    failover.ConnectionFailed(profile,DateTime.UtcNow);
                    try { if(tls!=null)tls.Dispose(); } catch{} try { if(remote!=null)remote.Close(); } catch{}
                    tls=null; remote=null;
                }
            }
            if (selected == null) throw new IOException("主、备用 VPS 都无法连接。" + (last == null ? "" : " " + FriendlyError(last)));
            connectionSetupSlots.Release(); setupSlot = false;
            log(source + " 已通过" + selected.Name + "加密连接，本地 " + route.LocalPort + " → VPS " + route.RemotePort + "；当前连接 " + number);
            Task up = CopyAndCount(miner.GetStream(), tls, true, minerState, cancellation);
            Task down = CopyAndCount(tls, miner.GetStream(), false, minerState, cancellation);
            await Task.WhenAny(up, down).ConfigureAwait(false);
            try { miner.Client.Shutdown(SocketShutdown.Both); } catch { }
            try { remote.Client.Shutdown(SocketShutdown.Both); } catch { }
            try { await Task.WhenAll(up, down).ConfigureAwait(false); } catch { }
        } catch (Exception ex) {
            Interlocked.Increment(ref failedConnections);
            MinerFailed(minerState,FriendlyError(ex));
            if (!cancellation.IsCancellationRequested) log(source + " 连接失败：" + FriendlyError(ex));
        } finally {
            if (setupSlot) connectionSetupSlots.Release();
            try { if (tls != null) tls.Dispose(); } catch { }
            try { miner.Close(); } catch { }
            try { if (remote != null) remote.Close(); } catch { }
            int remaining = Interlocked.Decrement(ref active);
            CloseMiner(minerState);
            log(source + " 已断开；当前连接 " + remaining);
        }
    }

    private async Task<TlsConnection> OpenTls(ServerProfile profile, int timeoutMs, CancellationToken cancellation)
    {
        TcpClient client = new TcpClient();
        try {
            await ConnectWithTimeout(client, profile.Address, profile.Port, timeoutMs, cancellation).ConfigureAwait(false);
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
            Task authDelay = Task.Delay(timeoutMs, cancellation);
            if (await Task.WhenAny(auth, authDelay).ConfigureAwait(false) != auth) {
                client.Close();
                ObserveFault(auth);
                if (cancellation.IsCancellationRequested) throw new OperationCanceledException(cancellation);
                throw new System.TimeoutException("TLS 握手超时。");
            }
            await auth.ConfigureAwait(false);
            return new TlsConnection { Stream=tls, Client=client };
        } catch { client.Close(); throw; }
    }

    private async void HealthLoop(AppConfig config, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested) {
            foreach (ServerProfile profile in EnabledProfiles(config)) {
                if(!failover.ShouldProbe(profile,DateTime.UtcNow))continue;
                bool primary=Object.ReferenceEquals(profile,config.Servers[0]);
                try {
                    await TestProfileAsync(profile, config.SiteName, cancellation).ConfigureAwait(false);
                    string previousEndpoint=failover.CurrentEndpoint;
                    failover.HealthSucceeded(profile,primary,DateTime.UtcNow);
                    if(primary&&!String.Equals(previousEndpoint,failover.CurrentEndpoint,StringComparison.OrdinalIgnoreCase))log("主线路已通过恢复观察："+previousEndpoint+" → "+failover.CurrentEndpoint+"。后续新连接恢复使用主线路。");
                } catch { failover.HealthFailed(profile,DateTime.UtcNow); }
            }
            try { await Task.Delay(TimeSpan.FromMinutes(config.HealthCheckMinutes), cancellation).ConfigureAwait(false); } catch { break; }
        }
    }

    public async Task<EndpointState> TestProfileAsync(ServerProfile profile, string siteName, CancellationToken cancellation)
    {
        DateTime begin = DateTime.UtcNow;
        try {
            TlsConnection connection = await OpenTls(profile, 8000, cancellation).ConfigureAwait(false);
            using (connection.Client) using (SslStream tls = connection.Stream) {
                string host = String.IsNullOrWhiteSpace(profile.ServerName) ? profile.Address : profile.ServerName.Trim();
                RelaySnapshot status=Snapshot();
                DateTime lastShare=DateTime.MinValue;foreach(MinerSnapshot miner in MinerSnapshots())if(miner.LastAccepted>lastShare)lastShare=miner.LastAccepted;RemoteActionReceipt receipt=new RemoteActionReceiptStore(ConfigStore.Folder).Load();string receiptHeaders=receipt==null?"":("X-Last-Action-Id: "+receipt.Id+"\r\nX-Last-Action-Status: "+receipt.Status+"\r\n");
                byte[] bytes = Encoding.ASCII.GetBytes("CONNECT /relay/v2/health HTTP/1.1\r\nHost: " + host + "\r\nAuthorization: Bearer " + profile.SharedKey + "\r\nX-Site-Name: " + SafeHeader(siteName) + "\r\nX-Client-Version: " + AppBrand.Version + "\r\nX-Miner-Count: " + status.ActiveMiners + "\r\nX-Active-Connections: " + status.Active + "\r\nX-Current-VPS: " + SafeHeader(failover.CurrentEndpoint) + "\r\nX-Last-Share: " + (lastShare==DateTime.MinValue?0:new DateTimeOffset(lastShare).ToUnixTimeSeconds()) + "\r\nX-Reconnect-Count: " + status.Failures + "\r\n"+receiptHeaders+"\r\n");
                string response = await ExchangeHeaderWithTimeout(tls, connection.Client, bytes, 8000, cancellation).ConfigureAwait(false);
                if (!response.StartsWith("HTTP/1.1 200 ", StringComparison.Ordinal)) throw new IOException("VPS拒绝认证，请检查共享密钥和服务版本。");
                RemoteClientAction remote=RemoteActionPolicy.ParseResponse(response);Action<RemoteClientAction> handler=null;if(remote!=null){lock(stateLock){if(remoteActionIds.Add(remote.Id))handler=RemoteActionRequested;}}if(handler!=null)handler(remote);
            }
            MarkSuccess(profile, (int)(DateTime.UtcNow - begin).TotalMilliseconds);
            return GetStateCopy(profile);
        } catch (Exception ex) {
            string message = FriendlyError(ex); MarkFailure(profile, message);
            throw new IOException(message, ex);
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
            int count = await input.ReadAsync(buffer, 0, buffer.Length, cancellation).ConfigureAwait(false);
            if (count <= 0) break;
            await output.WriteAsync(buffer, 0, count, cancellation).ConfigureAwait(false);
            if (upload) Interlocked.Add(ref uploadedBytes, count); else Interlocked.Add(ref downloadedBytes, count);
            ObserveMiner(connection,buffer,count,upload);
        }
    }

    private void MarkSuccess(ServerProfile p, int latency) { lock(stateLock) { EndpointState s=GetState(p); s.Online=true; s.LatencyMs=latency; s.LastError=""; s.LastCheck=DateTime.Now; } }
    private void MarkFailure(ServerProfile p, string error) { lock(stateLock) { EndpointState s=GetState(p); s.Online=false; s.Failures++; s.LastError=error; s.LastCheck=DateTime.Now; } }
    private EndpointState GetState(ServerProfile p) { EndpointState s; if (!endpointStates.TryGetValue(p.Name, out s)) { s=new EndpointState{Name=p.Name}; endpointStates[p.Name]=s; } return s; }
    private EndpointState GetStateCopy(ServerProfile p) { lock(stateLock) { return GetState(p).Copy(); } }

    public RelaySnapshot Snapshot()
    {
        RelaySnapshot value = new RelaySnapshot { Running=IsRunning, Active=Volatile.Read(ref active), Total=Interlocked.Read(ref totalConnections), Failures=Interlocked.Read(ref failedConnections), Uploaded=Interlocked.Read(ref uploadedBytes), Downloaded=Interlocked.Read(ref downloadedBytes), StartedAt=startedAt };
        lock(stateLock) { value.RecoveryPhase=recoveryPhase;value.RecoveryMessage=recoveryMessage;value.RecoveryUpdatedAt=recoveryUpdatedAt;foreach (EndpointState s in endpointStates.Values) { EndpointState copy=s.Copy();FailoverEndpointPolicyState policy=failover.Snapshot(copy.Name);copy.Selected=String.Equals(copy.Name,failover.CurrentEndpoint,StringComparison.OrdinalIgnoreCase);copy.Recovering=policy.RequiresRecoveryObservation;copy.ConsecutiveFailures=policy.ConsecutiveFailures;copy.CooldownUntilUtc=policy.CooldownUntilUtc;copy.RecoverySinceUtc=policy.RecoverySinceUtc;value.Endpoints.Add(copy); } foreach(MinerState miner in miners.Values)if(miner.Connections>0)value.ActiveMiners++; }
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

    private static async Task ConnectWithTimeout(TcpClient client, string host, int port, int timeoutMs, CancellationToken cancellation)
    {
        Task connect = client.ConnectAsync(host, port);
        Task delay = Task.Delay(timeoutMs, cancellation);
        if (await Task.WhenAny(connect, delay).ConfigureAwait(false) != connect) {
            client.Close();
            ObserveFault(connect);
            if (cancellation.IsCancellationRequested) throw new OperationCanceledException(cancellation);
            throw new System.TimeoutException("连接 VPS 超时。");
        }
        await connect.ConfigureAwait(false);
    }

    private static async Task<string> ExchangeHeaderWithTimeout(SslStream stream, TcpClient client, byte[] request, int timeoutMs, CancellationToken cancellation)
    {
        Task<string> exchange = WriteAndReadHeader(stream, request, cancellation);
        Task delay = Task.Delay(timeoutMs, cancellation);
        if (await Task.WhenAny(exchange, delay).ConfigureAwait(false) != exchange) {
            // Older .NET Framework SslStream versions may ignore cancellation while the peer stays silent.
            try { client.Close(); } catch { }
            ObserveFault(exchange);
            if (cancellation.IsCancellationRequested) throw new OperationCanceledException(cancellation);
            throw new System.TimeoutException("VPS认证响应超时。");
        }
        return await exchange.ConfigureAwait(false);
    }

    private static async Task<string> WriteAndReadHeader(SslStream stream, byte[] request, CancellationToken cancellation)
    {
        await stream.WriteAsync(request, 0, request.Length, cancellation).ConfigureAwait(false);
        await stream.FlushAsync(cancellation).ConfigureAwait(false);
        return await ReadHeader(stream, cancellation).ConfigureAwait(false);
    }

    private static void ObserveFault(Task task)
    {
        task.ContinueWith(delegate(Task failed) { Exception ignored = failed.Exception; }, CancellationToken.None,
            TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
    }

    private static async Task<string> ReadHeader(Stream stream, CancellationToken cancellation)
    {
        MemoryStream buffer = new MemoryStream();
        byte[] one = new byte[1];
        while (buffer.Length < 8192) {
            int count = await stream.ReadAsync(one, 0, 1, cancellation).ConfigureAwait(false);
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

public sealed class TlsConnection { public SslStream Stream; public TcpClient Client; }
