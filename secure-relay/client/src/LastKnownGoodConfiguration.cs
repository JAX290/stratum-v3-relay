using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;

public sealed class ConfigurationValidationEvidence
{
    public string ConfigurationFingerprint { get; private set; }
    public DateTime CompletedUtc { get; private set; }
    public bool CertificateVerified { get; private set; }
    public bool SharedKeyAuthenticated { get; private set; }
    public bool PortsValidated { get; private set; }
    public bool VpsReachable { get; private set; }

    public ConfigurationValidationEvidence(string configurationFingerprint, DateTime completedUtc,
        bool certificateVerified, bool sharedKeyAuthenticated, bool portsValidated, bool vpsReachable)
    {
        ConfigurationFingerprint = configurationFingerprint ?? "";
        CompletedUtc = completedUtc;
        CertificateVerified = certificateVerified;
        SharedKeyAuthenticated = sharedKeyAuthenticated;
        PortsValidated = portsValidated;
        VpsReachable = vpsReachable;
    }
}

public sealed class LastKnownGoodSnapshot
{
    public AppConfig Config { get; private set; }
    public DateTime ValidatedUtc { get; private set; }
    public string ConfigurationFingerprint { get; private set; }

    internal LastKnownGoodSnapshot(AppConfig config, DateTime validatedUtc, string fingerprint)
    {
        Config = config;
        ValidatedUtc = validatedUtc;
        ConfigurationFingerprint = fingerprint;
    }
}

public sealed class LastKnownGoodStore
{
    private const int SchemaVersion = 1;
    private const int MaximumFileBytes = 1024 * 1024;
    private static readonly TimeSpan MaximumEvidenceAge = TimeSpan.FromMinutes(15);
    private static readonly TimeSpan MaximumClockSkew = TimeSpan.FromMinutes(1);
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("MulinSenRelay.LastKnownGood.v1");
    private readonly string directory;

    public string FilePath { get { return Path.Combine(directory, "last-known-good.dat"); } }
    public string BackupPath { get { return FilePath + ".bak"; } }

    public LastKnownGoodStore(string storageDirectory)
    {
        if (String.IsNullOrWhiteSpace(storageDirectory)) throw new ArgumentException("存储目录不能为空。", "storageDirectory");
        directory = Path.GetFullPath(storageDirectory);
    }

    public static string ComputeFingerprint(AppConfig config)
    {
        if (config == null) throw new ArgumentNullException("config");
        AppConfig normalized = Clone(config);
        normalized.Normalize();
        using (MemoryStream stream = new MemoryStream())
        using (BinaryWriter writer = new BinaryWriter(stream, Encoding.UTF8)) {
            WriteString(writer, normalized.ListenAddress);
            WriteString(writer, normalized.Ports);
            writer.Write(normalized.AutoStart);
            writer.Write(normalized.CloseToTray);
            writer.Write(normalized.HealthCheckMinutes);
            WriteString(writer, normalized.SiteName);
            writer.Write(normalized.Servers.Count);
            foreach (ServerProfile profile in normalized.Servers) {
                WriteString(writer, profile.Name);
                writer.Write(profile.Enabled);
                WriteString(writer, profile.Address);
                writer.Write(profile.Port);
                WriteString(writer, profile.ServerName);
                WriteString(writer, NormalizeHex(profile.CertificateSha256));
                WriteString(writer, profile.SharedKey);
            }
            writer.Flush();
            using (SHA256 algorithm = SHA256.Create()) return ToHex(algorithm.ComputeHash(stream.ToArray()));
        }
    }

    public void Promote(AppConfig config, ConfigurationValidationEvidence evidence)
    {
        Promote(config, evidence, DateTime.UtcNow);
    }

    internal void Promote(AppConfig config, ConfigurationValidationEvidence evidence, DateTime nowUtc)
    {
        if (config == null) throw new ArgumentNullException("config");
        if (evidence == null) throw new ArgumentNullException("evidence");
        if (!evidence.CertificateVerified || !evidence.SharedKeyAuthenticated ||
            !evidence.PortsValidated || !evidence.VpsReachable)
            throw new InvalidOperationException("配置尚未完成证书、密钥、端口和 VPS 连通性验证。");
        if (evidence.CompletedUtc.Kind != DateTimeKind.Utc)
            throw new InvalidOperationException("配置验证时间必须使用 UTC。");
        if (evidence.CompletedUtc > nowUtc + MaximumClockSkew || nowUtc - evidence.CompletedUtc > MaximumEvidenceAge)
            throw new InvalidOperationException("配置验证结果已过期或时间无效。");
        string fingerprint = ComputeFingerprint(config);
        if (!FixedTimeEquals(fingerprint, evidence.ConfigurationFingerprint))
            throw new InvalidOperationException("验证结果与当前配置不匹配。");

        AppConfig storedConfig = PrepareForStorage(config);
        LastKnownGoodEnvelope envelope = new LastKnownGoodEnvelope {
            Schema = SchemaVersion,
            Fingerprint = fingerprint,
            ValidatedUtcTicks = evidence.CompletedUtc.Ticks,
            Config = storedConfig
        };
        byte[] serialized = Serialize(envelope);
        byte[] protectedBytes = null;
        string temporary = null;
        try {
            protectedBytes = ProtectedData.Protect(serialized, Entropy, DataProtectionScope.CurrentUser);
            Directory.CreateDirectory(directory);
            temporary = Path.Combine(directory, ".last-known-good-" + Guid.NewGuid().ToString("N") + ".tmp");
            using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                FileShare.None, 4096, FileOptions.WriteThrough)) {
                stream.Write(protectedBytes, 0, protectedBytes.Length);
                stream.Flush(true);
            }
            if (File.Exists(FilePath)) File.Replace(temporary, FilePath, BackupPath, true);
            else File.Move(temporary, FilePath);
            temporary = null;
        } finally {
            Array.Clear(serialized, 0, serialized.Length);
            if (protectedBytes != null) Array.Clear(protectedBytes, 0, protectedBytes.Length);
            if (temporary != null) try { File.Delete(temporary); } catch { }
        }
    }

    public LastKnownGoodSnapshot Load()
    {
        byte[] protectedBytes = null;
        byte[] serialized = null;
        try {
            FileInfo file = new FileInfo(FilePath);
            if (!file.Exists) throw new FileNotFoundException("尚未保存最后可用配置。", FilePath);
            if (file.Length <= 0 || file.Length > MaximumFileBytes) throw new InvalidDataException("最后可用配置文件大小无效。");
            protectedBytes = File.ReadAllBytes(FilePath);
            serialized = ProtectedData.Unprotect(protectedBytes, Entropy, DataProtectionScope.CurrentUser);
            LastKnownGoodEnvelope envelope = Deserialize(serialized);
            if (envelope == null || envelope.Schema != SchemaVersion || envelope.Config == null ||
                String.IsNullOrWhiteSpace(envelope.Fingerprint))
                throw new InvalidDataException("最后可用配置格式无效。");
            DateTime validatedUtc = new DateTime(envelope.ValidatedUtcTicks, DateTimeKind.Utc);
            envelope.Config.Normalize();
            string actualFingerprint = ComputeFingerprint(envelope.Config);
            if (!FixedTimeEquals(actualFingerprint, envelope.Fingerprint))
                throw new InvalidDataException("最后可用配置完整性校验失败。");
            return new LastKnownGoodSnapshot(envelope.Config, validatedUtc, envelope.Fingerprint);
        } catch (FileNotFoundException) {
            throw;
        } catch (InvalidDataException) {
            throw;
        } catch (Exception ex) {
            throw new InvalidDataException("最后可用配置无法解密或已被篡改。", ex);
        } finally {
            if (protectedBytes != null) Array.Clear(protectedBytes, 0, protectedBytes.Length);
            if (serialized != null) Array.Clear(serialized, 0, serialized.Length);
        }
    }

    private static AppConfig PrepareForStorage(AppConfig source)
    {
        AppConfig result = Clone(source);
        result.Normalize();
        foreach (ServerProfile profile in result.Servers) profile.ProtectedToken = ConfigStore.Protect(profile.SharedKey ?? "");
        ServerProfile primary = result.Servers[0];
        result.ServerAddress = primary.Address;
        result.ServerPort = primary.Port;
        result.ServerName = primary.ServerName;
        result.CertificateSha256 = primary.CertificateSha256;
        result.ProtectedToken = primary.ProtectedToken;
        return result;
    }

    internal static AppConfig Clone(AppConfig source)
    {
        AppConfig result = new AppConfig {
            ServerAddress = source.ServerAddress,
            ServerPort = source.ServerPort,
            ServerName = source.ServerName,
            CertificateSha256 = source.CertificateSha256,
            ProtectedToken = source.ProtectedToken,
            ListenAddress = source.ListenAddress,
            Ports = source.Ports,
            AutoStart = source.AutoStart,
            CloseToTray = source.CloseToTray,
            HealthCheckMinutes = source.HealthCheckMinutes,
            SiteName = source.SiteName,
            Servers = new List<ServerProfile>()
        };
        if (source.Servers != null) foreach (ServerProfile profile in source.Servers)
            result.Servers.Add(profile == null ? new ServerProfile() : profile.Copy());
        return result;
    }

    private static byte[] Serialize(LastKnownGoodEnvelope value)
    {
        using (MemoryStream stream = new MemoryStream()) {
            new DataContractJsonSerializer(typeof(LastKnownGoodEnvelope)).WriteObject(stream, value);
            return stream.ToArray();
        }
    }

    private static LastKnownGoodEnvelope Deserialize(byte[] value)
    {
        using (MemoryStream stream = new MemoryStream(value, false))
            return (LastKnownGoodEnvelope)new DataContractJsonSerializer(typeof(LastKnownGoodEnvelope)).ReadObject(stream);
    }

    private static void WriteString(BinaryWriter writer, string value)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(value ?? "");
        writer.Write(bytes.Length);
        writer.Write(bytes);
    }

    private static string NormalizeHex(string value)
    {
        return (value ?? "").Replace(" ", "").Replace(":", "").Trim().ToUpperInvariant();
    }

    private static string ToHex(byte[] value)
    {
        StringBuilder result = new StringBuilder(value.Length * 2);
        foreach (byte item in value) result.Append(item.ToString("X2"));
        return result.ToString();
    }

    private static bool FixedTimeEquals(string left, string right)
    {
        byte[] a = Encoding.ASCII.GetBytes(left ?? "");
        byte[] b = Encoding.ASCII.GetBytes(right ?? "");
        int difference = a.Length ^ b.Length;
        int length = Math.Min(a.Length, b.Length);
        for (int index = 0; index < length; index++) difference |= a[index] ^ b[index];
        return difference == 0;
    }

    [DataContract]
    private sealed class LastKnownGoodEnvelope
    {
        [DataMember] public int Schema;
        [DataMember] public string Fingerprint = "";
        [DataMember] public long ValidatedUtcTicks;
        [DataMember] public AppConfig Config;
    }
}
