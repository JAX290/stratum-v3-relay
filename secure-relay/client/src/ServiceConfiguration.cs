using System;
using System.IO;
using System.Collections.Generic;
using System.Net;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

public static class ServiceConfiguration
{
    // Used only by the explicit installation/migration flow. No plaintext file is written.
    public static byte[] Encode(AppConfig source)
    {
        Validate(source);
        AppConfig copy=new AppConfig { ListenAddress=source.ListenAddress,Ports=source.Ports,
            SiteName=source.SiteName,HealthCheckMinutes=source.HealthCheckMinutes,Servers=new List<ServerProfile>() };
        foreach(ServerProfile profile in source.Servers) {
            ServerProfile item=profile.Copy();
            item.ProtectedToken=item.SharedKey;item.SharedKey="";copy.Servers.Add(item);
        }
        byte[] clear;
        using(MemoryStream stream=new MemoryStream()) {
            new DataContractJsonSerializer(typeof(AppConfig)).WriteObject(stream,copy);clear=stream.ToArray();
        }
        try{return ProtectedData.Protect(clear,null,DataProtectionScope.LocalMachine);}
        finally{Array.Clear(clear,0,clear.Length);foreach(ServerProfile p in copy.Servers)p.ProtectedToken="";}
    }
    public static AppConfig Decode(byte[] encrypted)
    {
        if(encrypted==null||encrypted.Length==0||encrypted.Length>1048576)throw new InvalidDataException("服务配置无效。");
        byte[] clear=ProtectedData.Unprotect(encrypted,null,DataProtectionScope.LocalMachine);
        try {
            AppConfig config;
            using(MemoryStream stream=new MemoryStream(clear))
                config=(AppConfig)new DataContractJsonSerializer(typeof(AppConfig)).ReadObject(stream);
            if(config==null||config.Servers==null)throw new InvalidDataException("服务配置不完整。");
            foreach(ServerProfile profile in config.Servers) {
                if(profile==null)throw new InvalidDataException("服务配置不完整。");
                profile.SharedKey=profile.ProtectedToken;profile.ProtectedToken="";
            }
            Validate(config);return config;
        } finally{Array.Clear(clear,0,clear.Length);}
    }
    public static void Validate(AppConfig config)
    {
        IPAddress address;
        if(config==null||!IPAddress.TryParse(config.ListenAddress,out address))throw new InvalidDataException("请检查本地监听地址。");
        PortRoute.Parse(config.Ports);
        if(config.HealthCheckMinutes<1||config.HealthCheckMinutes>1440)throw new InvalidDataException("请检查探测间隔。");
        int enabled=0;
        foreach(ServerProfile profile in config.Servers??new List<ServerProfile>()) {
            if(profile==null)throw new InvalidDataException("服务配置不完整。");
            if(!profile.Enabled)continue;enabled++;
            if(String.IsNullOrWhiteSpace(profile.Address)||profile.Address.IndexOfAny(new char[]{'\r','\n',' ','/','\\'})>=0||
                profile.Port<1||profile.Port>65535)throw new InvalidDataException("请检查 VPS 地址及端口。");
            if(!Regex.IsMatch(profile.SharedKey??"","^[A-Za-z0-9_-]{32,}$"))throw new InvalidDataException("请检查 VPS 共享密钥。");
            if(String.IsNullOrWhiteSpace(profile.CertificateSha256)) {
                if(String.IsNullOrWhiteSpace(profile.ServerName)||Uri.CheckHostName(profile.ServerName)==UriHostNameType.Unknown)
                    throw new InvalidDataException("请填写有效证书名称或 SHA-256 指纹。");
            } else if(!Regex.IsMatch(Regex.Replace(profile.CertificateSha256,"[ :\\-]",""),"^[A-Fa-f0-9]{64}$"))
                throw new InvalidDataException("证书指纹必须是完整 SHA-256。");
        }
        if(enabled==0)throw new InvalidDataException("至少启用一台 VPS。");
    }
}
