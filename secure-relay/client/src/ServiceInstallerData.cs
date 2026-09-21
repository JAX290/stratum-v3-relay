using System;
using System.IO;
using System.Runtime.Serialization.Json;

public static class ServiceInstallerData
{
    internal static void CreateSyntheticTestConfig(string path,int port)
    {
        if(port<1024||port>65535)throw new ArgumentOutOfRangeException("port");
        AppConfig config=new AppConfig{ListenAddress="127.0.0.1",Ports=port.ToString(),HealthCheckMinutes=1440,SiteName="服务隔离验收"};
        config.Servers.Add(new ServerProfile{Name="测试VPS",Enabled=true,Address="192.0.2.1",Port=452,
            CertificateSha256=new String('A',64),ProtectedToken=ConfigStore.Protect("test_only_012345678901234567890123456789")});
        using(FileStream stream=new FileStream(Path.GetFullPath(path),FileMode.CreateNew,FileAccess.Write,FileShare.None))
            new DataContractJsonSerializer(typeof(AppConfig)).WriteObject(stream,config);
    }
    public static AppConfig ReadDesktopConfig(string sourcePath)
    {
        FileInfo source=new FileInfo(Path.GetFullPath(sourcePath));
        if(!source.Exists||source.Length<=0||source.Length>1048576||
            (source.Attributes&FileAttributes.ReparsePoint)!=0)
            throw new InvalidDataException("找不到有效的桌面客户端配置。");
        AppConfig config;
        using(FileStream stream=new FileStream(source.FullName,FileMode.Open,FileAccess.Read,FileShare.Read))
            config=(AppConfig)new DataContractJsonSerializer(typeof(AppConfig)).ReadObject(stream);
        if(config==null)throw new InvalidDataException("桌面客户端配置无法读取。");
        config.Normalize();
        foreach(ServerProfile profile in config.Servers) {
            if(profile.Enabled&&String.IsNullOrEmpty(profile.SharedKey))
                throw new InvalidDataException("无法读取共享密钥，请使用保存配置的 Windows 用户执行安装。");
        }
        ServiceConfiguration.Validate(config);
        return config;
    }

    public static void Prepare(string sourcePath,string dataDirectory)
    {
        PrepareCore(sourcePath,dataDirectory,true);
    }
    internal static void PrepareForTests(string sourcePath,string dataDirectory)
    {
        PrepareCore(sourcePath,dataDirectory,false);
    }
    private static void PrepareCore(string sourcePath,string dataDirectory,bool verifyPermissions)
    {
        string directory=Path.GetFullPath(dataDirectory);
        if(verifyPermissions)ServiceStoragePermissions.VerifyDirectory(directory);
        AppConfig config=ReadDesktopConfig(sourcePath);
        byte[] encrypted=ServiceConfiguration.Encode(config);
        string finalPath=Path.Combine(directory,"service-config.dat");
        string temporary=finalPath+"."+Guid.NewGuid().ToString("N")+".tmp";
        try {
            using(FileStream stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)) {
                stream.Write(encrypted,0,encrypted.Length);stream.Flush(true);
            }
            if(verifyPermissions)File.SetAccessControl(temporary,ServiceStoragePermissions.CreateFileSecurity());
            if(File.Exists(finalPath))File.Replace(temporary,finalPath,finalPath+".bak");
            else File.Move(temporary,finalPath);
            if(verifyPermissions)ServiceStoragePermissions.VerifyFile(finalPath);
            if(!File.Exists(Path.Combine(directory,"recovery.json")))
                using(RecoverySession first=new RecoverySession(Path.Combine(directory,"recovery.json"),true)){}
            if(verifyPermissions) {
                ServiceStoragePermissions.ApplyFileSecurity(Path.Combine(directory,"recovery.json"));
                ServiceStoragePermissions.VerifyFile(Path.Combine(directory,"recovery.json"));
            }
        } finally {
            Array.Clear(encrypted,0,encrypted.Length);
            if(File.Exists(temporary))File.Delete(temporary);
            foreach(ServerProfile profile in config.Servers)profile.SharedKey="";
        }
    }

    public static void ValidatePrepared(string dataDirectory)
    {
        ValidatePreparedCore(dataDirectory,true);
    }
    internal static void ValidatePreparedForTests(string dataDirectory)
    {
        ValidatePreparedCore(dataDirectory,false);
    }
    private static void ValidatePreparedCore(string dataDirectory,bool verifyPermissions)
    {
        string directory=Path.GetFullPath(dataDirectory);
        if(verifyPermissions)ServiceStoragePermissions.VerifyDirectory(directory);
        string configPath=Path.Combine(directory,"service-config.dat");
        string recoveryPath=Path.Combine(directory,"recovery.json");
        if(verifyPermissions) {
            ServiceStoragePermissions.VerifyFile(configPath);
            ServiceStoragePermissions.VerifyFile(recoveryPath);
        }
        AppConfig config=ServiceConfiguration.Decode(File.ReadAllBytes(configPath));
        try {ServiceConfiguration.Validate(config);}
        finally{foreach(ServerProfile profile in config.Servers)profile.SharedKey="";}
        using(RecoverySession session=new RecoverySession(recoveryPath,false)){}
    }
}
