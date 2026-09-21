using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

public sealed class CompleteConfigurationValidator
{
    public async Task<ConfigurationValidationEvidence> ValidateAsync(AppConfig config,
        Func<ServerProfile, string, CancellationToken, Task<EndpointState>> profileProbe,
        CancellationToken cancellation)
    {
        if (config == null) throw new ArgumentNullException("config");
        if (profileProbe == null) throw new ArgumentNullException("profileProbe");
        if (String.IsNullOrWhiteSpace(config.SiteName))
            throw new InvalidOperationException("请填写矿场名称，便于离线告警识别。");

        IPAddress listenAddress;
        if (!IPAddress.TryParse(config.ListenAddress, out listenAddress))
            throw new InvalidOperationException("本地监听地址格式不正确。");
        List<PortRoute> routes = PortRoute.Parse(config.Ports);
        List<ServerProfile> enabled = new List<ServerProfile>();
        if (config.Servers != null) foreach (ServerProfile profile in config.Servers) {
            if (profile == null || !profile.Enabled) continue;
            ValidateProfile(profile);
            enabled.Add(profile);
        }
        if (enabled.Count == 0) throw new InvalidOperationException("至少启用一个 VPS。");

        ValidateLocalPorts(listenAddress, routes, cancellation);
        foreach (ServerProfile profile in enabled) {
            cancellation.ThrowIfCancellationRequested();
            await profileProbe(profile, config.SiteName, cancellation).ConfigureAwait(false);
        }

        DateTime completedUtc = DateTime.UtcNow;
        return new ConfigurationValidationEvidence(LastKnownGoodStore.ComputeFingerprint(config),
            completedUtc, true, true, true, true);
    }

    public static void ValidateProfile(ServerProfile profile)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        if (String.IsNullOrWhiteSpace(profile.Address))
            throw new InvalidOperationException(profile.Name + "：请填写 VPS 地址。");
        if ((profile.SharedKey ?? "").Length < 32 || !Regex.IsMatch(profile.SharedKey ?? "", "^[A-Za-z0-9_-]+$"))
            throw new InvalidOperationException(profile.Name + "：共享密钥格式不正确。");
        if (String.IsNullOrWhiteSpace(profile.CertificateSha256) && String.IsNullOrWhiteSpace(profile.ServerName))
            throw new InvalidOperationException(profile.Name + "：请填写证书名称或证书 SHA-256 指纹。");
    }

    private static void ValidateLocalPorts(IPAddress address, List<PortRoute> routes, CancellationToken cancellation)
    {
        List<TcpListener> listeners = new List<TcpListener>();
        try {
            foreach (PortRoute route in routes) {
                cancellation.ThrowIfCancellationRequested();
                TcpListener listener = new TcpListener(address, route.LocalPort);
                listener.Server.ExclusiveAddressUse = true;
                try { listener.Start(1); }
                catch (SocketException ex) {
                    listener.Stop();
                    throw new InvalidOperationException("本地端口 " + route.LocalPort + " 无法使用，可能已被其他程序占用。", ex);
                }
                listeners.Add(listener);
            }
        } finally {
            foreach (TcpListener listener in listeners) try { listener.Stop(); } catch { }
        }
    }
}
