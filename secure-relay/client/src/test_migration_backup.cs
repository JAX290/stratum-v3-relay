using System;
using System.IO;

public static class MigrationBackupTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        AppConfig config=new AppConfig{SiteName="一号矿场",ListenAddress="0.0.0.0",Ports="9999,10001=10002",HealthCheckMinutes=7,AutoStart=true,CloseToTray=true};config.Servers.Add(new ServerProfile{Name="主VPS",Enabled=true,Address="relay.example.com",Port=443,ServerName="relay.example.com",CertificateSha256=new string('A',64),SharedKey=new string('b',64)});config.Servers.Add(new ServerProfile{Name="备用VPS 1",Enabled=true,Address="backup.example.com",Port=452,ServerName="backup.example.com",CertificateSha256=new string('C',64),SharedKey=new string('d',64)});config.Servers.Add(new ServerProfile{Name="备用VPS 2",Enabled=false,Port=443});
        DateTime now=new DateTime(2026,9,22,0,0,0,DateTimeKind.Utc);byte[] salt=new byte[16],nonce=new byte[16];for(int i=0;i<16;i++){salt[i]=(byte)(i+1);nonce[i]=(byte)(i+21);}string password="correct horse battery staple";string package=MigrationBackupCodec.CreateForTest(config,password,"192.168.1.10",now,72,"0123456789abcdef0123456789abcdef",salt,nonce);
        Check(!package.Contains(config.Servers[0].SharedKey)&&!package.Contains("relay.example.com"),"migration backup hides credentials and endpoints");
        MigrationBackupData restored=MigrationBackupCodec.Import(package,password,now.AddHours(1));Check(restored.Config.Servers.Count==3&&restored.Config.Servers[1].Address=="backup.example.com"&&restored.Config.Ports==config.Ports,"migration backup restores complete configuration");
        bool wrong=false;try{MigrationBackupCodec.Import(package,"wrong password value",now.AddHours(1));}catch(System.Security.Cryptography.CryptographicException){wrong=true;}Check(wrong,"wrong migration password is rejected");
        char[] changed=package.ToCharArray();int marker=package.IndexOf("ciphertext");changed[marker+20]=changed[marker+20]=='A'?'B':'A';bool tampered=false;try{MigrationBackupCodec.Import(new string(changed),password,now.AddHours(1));}catch{tampered=true;}Check(tampered,"modified migration backup is rejected");
        bool expired=false;try{MigrationBackupCodec.Import(package,password,now.AddHours(72));}catch(InvalidDataException){expired=true;}Check(expired,"expired migration backup is rejected");
        ConfigurationValidationEvidence incomplete=new ConfigurationValidationEvidence(LastKnownGoodStore.ComputeFingerprint(restored.Config),now.AddHours(1),true,true,false,true);bool blocked=false;try{MigrationSwitchPlan.Create(restored,"192.168.1.20",incomplete);}catch(InvalidOperationException){blocked=true;}Check(blocked,"miner switch prompt is blocked before complete validation");
        ConfigurationValidationEvidence complete=new ConfigurationValidationEvidence(LastKnownGoodStore.ComputeFingerprint(restored.Config),now.AddHours(1),true,true,true,true);MigrationSwitchPlan plan=MigrationSwitchPlan.Create(restored,"192.168.1.20",complete);Check(plan.OldLanIp=="192.168.1.10"&&plan.MinerAddresses.Count==2&&plan.MinerAddresses[1]=="stratum+tcp://192.168.1.20:10001","validated migration produces new miner addresses");
        return failures==0?0:1;
    }
}
