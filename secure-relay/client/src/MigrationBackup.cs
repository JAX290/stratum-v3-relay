using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

public sealed class MigrationBackupData
{
    public string PackageId="",SourceLanIp="";
    public long Created,Expires;
    public AppConfig Config;
}

public sealed class MigrationSwitchPlan
{
    public string OldLanIp="",NewLanIp="";
    public List<string> MinerAddresses=new List<string>();

    public static MigrationSwitchPlan Create(MigrationBackupData backup,string newLanIp,ConfigurationValidationEvidence evidence)
    {
        if(backup==null||backup.Config==null)throw new ArgumentNullException("backup");
        if(evidence==null||!evidence.CertificateVerified||!evidence.SharedKeyAuthenticated||!evidence.PortsValidated||!evidence.VpsReachable||
            !String.Equals(evidence.ConfigurationFingerprint,LastKnownGoodStore.ComputeFingerprint(backup.Config),StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("新电脑尚未完成证书、密钥、端口和 VPS 连通性验证，不能提示切换矿机。");
        IPAddress address;if(String.IsNullOrWhiteSpace(newLanIp)||!IPAddress.TryParse(newLanIp,out address))throw new InvalidOperationException("新电脑没有可用的局域网 IPv4 地址。");
        MigrationSwitchPlan result=new MigrationSwitchPlan{OldLanIp=backup.SourceLanIp??"",NewLanIp=newLanIp};
        foreach(PortRoute route in PortRoute.Parse(backup.Config.Ports))result.MinerAddresses.Add("stratum+tcp://"+newLanIp+":"+route.LocalPort);
        return result;
    }
}

public static class MigrationBackupCodec
{
    private sealed class Envelope{public string format="",id="",salt="",nonce="",ciphertext="",mac="";public long expires;}
    private sealed class Profile{public string name="",address="",server_name="",certificate_sha256="",shared_key="";public bool enabled;public int port;}
    private sealed class Payload{public string source_lan_ip="",site_name="",listen_address="",ports="";public long created,expires;public int health_check_minutes;public bool auto_start,close_to_tray;public List<Profile> servers=new List<Profile>();}

    public static string Export(AppConfig config,string password,string sourceLanIp,DateTime nowUtc,int validHours)
    {
        byte[] salt=new byte[16],nonce=new byte[16],idBytes=new byte[16];using(RandomNumberGenerator rng=RandomNumberGenerator.Create()){rng.GetBytes(salt);rng.GetBytes(nonce);rng.GetBytes(idBytes);}
        return Create(config,password,sourceLanIp,nowUtc,validHours,ToHex(idBytes),salt,nonce);
    }

    public static MigrationBackupData Import(string json,string password,DateTime nowUtc)
    {
        if(nowUtc.Kind!=DateTimeKind.Utc)throw new ArgumentException("导入时间必须使用 UTC。");ValidatePassword(password);JavaScriptSerializer serializer=new JavaScriptSerializer();Envelope envelope;
        try{envelope=serializer.Deserialize<Envelope>(json);}catch{throw new InvalidDataException("迁移备份格式不正确。");}
        if(envelope==null||envelope.format!="MSRM1"||!Regex.IsMatch(envelope.id??"","^[a-f0-9]{32}$"))throw new InvalidDataException("迁移备份版本或编号不正确。");
        long now=new DateTimeOffset(nowUtc).ToUnixTimeSeconds();if(envelope.expires<=now)throw new InvalidDataException("迁移备份已经过期，请在旧电脑重新导出。");
        byte[] salt=AccessPackageCodec.Decode(envelope.salt,16),nonce=AccessPackageCodec.Decode(envelope.nonce,16),cipher=AccessPackageCodec.Decode(envelope.ciphertext,1,262144),expected=AccessPackageCodec.Decode(envelope.mac,32);
        byte[] keys=AccessPackageCodec.Pbkdf2Sha256(Encoding.UTF8.GetBytes(password),salt,200000,64);byte[] aad=Encoding.ASCII.GetBytes("MSRM1|"+envelope.id+"|"+envelope.expires);byte[] actual;
        using(HMACSHA256 mac=new HMACSHA256(AccessPackageCodec.Slice(keys,32,32)))actual=mac.ComputeHash(AccessPackageCodec.Join(aad,salt,nonce,cipher));
        if(!AccessPackageCodec.FixedEquals(actual,expected))throw new CryptographicException("迁移密码错误或备份文件已被修改。");
        byte[] plain=AccessPackageCodec.Xor(cipher,AccessPackageCodec.Keystream(AccessPackageCodec.Slice(keys,0,32),nonce,cipher.Length));Payload payload;
        try{payload=serializer.Deserialize<Payload>(Encoding.UTF8.GetString(plain));}catch{throw new InvalidDataException("迁移备份内容无法读取。");}
        Array.Clear(keys,0,keys.Length);Array.Clear(plain,0,plain.Length);
        if(payload==null||payload.expires!=envelope.expires||payload.created<=0||payload.expires<=payload.created||payload.expires-payload.created>168*3600)throw new InvalidDataException("迁移备份有效期不正确。");
        AppConfig config=FromPayload(payload);ValidateConfig(config);
        return new MigrationBackupData{PackageId=envelope.id,SourceLanIp=payload.source_lan_ip??"",Created=payload.created,Expires=payload.expires,Config=config};
    }

    public static string CreateForTest(AppConfig config,string password,string sourceLanIp,DateTime nowUtc,int validHours,string id,byte[] salt,byte[] nonce)
    {
        return Create(config,password,sourceLanIp,nowUtc,validHours,id,salt,nonce);
    }

    private static string Create(AppConfig config,string password,string sourceLanIp,DateTime nowUtc,int validHours,string id,byte[] salt,byte[] nonce)
    {
        if(nowUtc.Kind!=DateTimeKind.Utc)throw new ArgumentException("导出时间必须使用 UTC。");if(validHours<1||validHours>168)throw new ArgumentOutOfRangeException("validHours");ValidatePassword(password);if(!Regex.IsMatch(id??"","^[a-f0-9]{32}$"))throw new InvalidDataException("迁移备份编号不正确。");if(salt==null||salt.Length!=16||nonce==null||nonce.Length!=16)throw new ArgumentException("迁移备份随机数长度不正确。");
        AppConfig copy=LastKnownGoodStore.Clone(config??new AppConfig());copy.Normalize();ValidateConfig(copy);long created=new DateTimeOffset(nowUtc).ToUnixTimeSeconds(),expires=created+validHours*3600;Payload payload=ToPayload(copy,sourceLanIp,created,expires);JavaScriptSerializer serializer=new JavaScriptSerializer();byte[] plain=Encoding.UTF8.GetBytes(serializer.Serialize(payload));
        byte[] keys=AccessPackageCodec.Pbkdf2Sha256(Encoding.UTF8.GetBytes(password),salt,200000,64);byte[] cipher=AccessPackageCodec.Xor(plain,AccessPackageCodec.Keystream(AccessPackageCodec.Slice(keys,0,32),nonce,plain.Length));byte[] aad=Encoding.ASCII.GetBytes("MSRM1|"+id+"|"+expires);byte[] digest;
        using(HMACSHA256 mac=new HMACSHA256(AccessPackageCodec.Slice(keys,32,32)))digest=mac.ComputeHash(AccessPackageCodec.Join(aad,salt,nonce,cipher));Array.Clear(keys,0,keys.Length);Array.Clear(plain,0,plain.Length);
        return serializer.Serialize(new Envelope{format="MSRM1",id=id,expires=expires,salt=Convert.ToBase64String(salt),nonce=Convert.ToBase64String(nonce),ciphertext=Convert.ToBase64String(cipher),mac=Convert.ToBase64String(digest)});
    }

    private static Payload ToPayload(AppConfig config,string sourceLanIp,long created,long expires)
    {
        Payload payload=new Payload{source_lan_ip=sourceLanIp??"",site_name=config.SiteName,listen_address=config.ListenAddress,ports=config.Ports,health_check_minutes=config.HealthCheckMinutes,auto_start=config.AutoStart,close_to_tray=config.CloseToTray,created=created,expires=expires};
        foreach(ServerProfile item in config.Servers)payload.servers.Add(new Profile{name=item.Name,enabled=item.Enabled,address=item.Address,port=item.Port,server_name=item.ServerName,certificate_sha256=item.CertificateSha256,shared_key=item.SharedKey});return payload;
    }

    private static AppConfig FromPayload(Payload payload)
    {
        AppConfig config=new AppConfig{SiteName=payload.site_name??"",ListenAddress=payload.listen_address??"",Ports=payload.ports??"",HealthCheckMinutes=payload.health_check_minutes,AutoStart=payload.auto_start,CloseToTray=payload.close_to_tray,Servers=new List<ServerProfile>()};
        if(payload.servers!=null)foreach(Profile item in payload.servers)config.Servers.Add(new ServerProfile{Name=item.name??"",Enabled=item.enabled,Address=item.address??"",Port=item.port,ServerName=item.server_name??"",CertificateSha256=item.certificate_sha256??"",SharedKey=item.shared_key??""});config.Normalize();return config;
    }

    private static void ValidateConfig(AppConfig config)
    {
        if(config==null||String.IsNullOrWhiteSpace(config.SiteName))throw new InvalidDataException("迁移配置缺少矿场名称。");IPAddress listen;if(!IPAddress.TryParse(config.ListenAddress,out listen))throw new InvalidDataException("迁移配置的监听地址不正确。");PortRoute.Parse(config.Ports);int enabled=0;foreach(ServerProfile profile in config.Servers)if(profile!=null&&profile.Enabled){CompleteConfigurationValidator.ValidateProfile(profile);enabled++;}if(enabled==0)throw new InvalidDataException("迁移配置没有启用的 VPS。");
    }

    private static void ValidatePassword(string password){if(String.IsNullOrWhiteSpace(password)||password.Length<12)throw new InvalidDataException("迁移密码至少需要 12 个字符。");}
    private static string ToHex(byte[] value){StringBuilder result=new StringBuilder(value.Length*2);foreach(byte item in value)result.Append(item.ToString("x2"));return result.ToString();}
}
