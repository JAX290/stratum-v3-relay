using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;

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
    public ServerProfile Copy() { return new ServerProfile { Name=Name, Enabled=Enabled, Address=Address, Port=Port,
        ServerName=ServerName, CertificateSha256=CertificateSha256, ProtectedToken=ProtectedToken, SharedKey=SharedKey }; }
}

public sealed class PortRoute
{
    public int LocalPort;
    public int RemotePort;
    public override string ToString() { return LocalPort == RemotePort ? LocalPort.ToString() : LocalPort + "=" + RemotePort; }
    public static List<PortRoute> Parse(string text)
    {
        List<PortRoute> result = new List<PortRoute>(); HashSet<int> locals = new HashSet<int>();
        foreach (string raw in (text ?? "").Split(',')) {
            string item=raw.Trim(); if(item.Length==0)continue; string[] parts=item.Split('='); int local,remote;
            if(parts.Length==1){if(!Int32.TryParse(parts[0],out local))throw new InvalidOperationException("本地端口格式不正确："+item);remote=local;}
            else if(parts.Length==2&&Int32.TryParse(parts[0],out local)&&Int32.TryParse(parts[1],out remote)){}
            else throw new InvalidOperationException("端口映射格式不正确："+item+"，请使用 本地端口=VPS端口");
            if(local<1||local>65535||remote<1||remote>65535)throw new InvalidOperationException("端口必须在 1 到 65535 之间："+item);
            if(!locals.Add(local))throw new InvalidOperationException("本地端口重复："+local);
            result.Add(new PortRoute{LocalPort=local,RemotePort=remote});
        }
        if(result.Count==0)throw new InvalidOperationException("至少需要一个本地端口。"); return result;
    }
}

[DataContract]
public sealed class AppConfig
{
    [DataMember] public string ServerAddress=""; [DataMember] public int ServerPort=443;
    [DataMember] public string ServerName=""; [DataMember] public string CertificateSha256="";
    [DataMember] public string ProtectedToken=""; [DataMember] public string ListenAddress="0.0.0.0";
    [DataMember] public string Ports="9999,10001,10002,10010,10011,10012,10020,10021,10022,10030,10031,10032,11001,11002,11003,11101,11102,11103,11201,11202,11203,11301,11302,11303";
    [DataMember] public bool AutoStart=false; [DataMember] public bool CloseToTray=true;
    [DataMember] public int HealthCheckMinutes=5; [DataMember] public string SiteName="";
    [DataMember] public List<ServerProfile> Servers=new List<ServerProfile>();
    [OnDeserializing] private void BeforeDeserialize(StreamingContext context){CloseToTray=true;HealthCheckMinutes=5;}
    public void Normalize(){if(String.IsNullOrWhiteSpace(SiteName))SiteName=Environment.MachineName;if(HealthCheckMinutes<1||HealthCheckMinutes>1440)HealthCheckMinutes=5;if(Servers==null)Servers=new List<ServerProfile>();if(Servers.Count==0)Servers.Add(new ServerProfile{Name="主VPS",Enabled=true,Address=ServerAddress,Port=ServerPort,ServerName=ServerName,CertificateSha256=CertificateSha256,ProtectedToken=ProtectedToken});while(Servers.Count<3)Servers.Add(new ServerProfile{Name=Servers.Count==1?"备用VPS 1":"备用VPS 2",Enabled=false,Port=443});for(int i=0;i<Servers.Count;i++){if(Servers[i]==null)Servers[i]=new ServerProfile();if(String.IsNullOrWhiteSpace(Servers[i].Name))Servers[i].Name=i==0?"主VPS":"备用VPS "+i;if(String.IsNullOrEmpty(Servers[i].SharedKey))Servers[i].SharedKey=ConfigStore.Unprotect(Servers[i].ProtectedToken);}}
}

public static class ConfigStore
{
    private static string folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"StratumSecureRelay");
    public static string Folder { get { return folder; } }
    public static string FilePath { get { return Path.Combine(Folder,"config.json"); } }
    internal static void SetServiceFolder(string value) { folder=Path.GetFullPath(value); }
    public static AppConfig Load(){try{using(FileStream stream=File.OpenRead(FilePath)){AppConfig value=(AppConfig)new DataContractJsonSerializer(typeof(AppConfig)).ReadObject(stream);value.Normalize();return value;}}catch{AppConfig value=new AppConfig();value.Normalize();return value;}}
    public static void Save(AppConfig config){config.Normalize();foreach(ServerProfile profile in config.Servers)profile.ProtectedToken=Protect(profile.SharedKey??"");ServerProfile primary=config.Servers[0];config.ServerAddress=primary.Address;config.ServerPort=primary.Port;config.ServerName=primary.ServerName;config.CertificateSha256=primary.CertificateSha256;config.ProtectedToken=primary.ProtectedToken;Directory.CreateDirectory(Folder);string temporary=FilePath+".tmp";using(FileStream stream=File.Create(temporary))new DataContractJsonSerializer(typeof(AppConfig)).WriteObject(stream,config);if(File.Exists(FilePath))File.Replace(temporary,FilePath,null);else File.Move(temporary,FilePath);}
    public static string Protect(string value){byte[] data=Encoding.UTF8.GetBytes(value);return Convert.ToBase64String(ProtectedData.Protect(data,null,DataProtectionScope.CurrentUser));}
    public static string Unprotect(string value){if(String.IsNullOrWhiteSpace(value))return "";try{return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(value),null,DataProtectionScope.CurrentUser));}catch{return "";}}
}
