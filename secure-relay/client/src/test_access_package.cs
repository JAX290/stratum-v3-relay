using System;
using System.IO;

public static class AccessPackageTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        long now=1789980000;AccessPackageData source=new AccessPackageData{SiteName="一号矿场",ClientId="farm-a",Address="relay.example.com",Port=443,ServerName="relay.example.com",CertificateSha256=new string('A',64),SharedKey=new string('b',64),Created=now-60,Expires=now+3600};
        string id="0123456789abcdef0123456789abcdef",code="ABCDEF-123456-7890AB-CDEF12";byte[] salt=new byte[16],nonce=new byte[16];for(int i=0;i<16;i++){salt[i]=(byte)(i+1);nonce[i]=(byte)(20+i);}
        string package=AccessPackageCodec.CreateForTest(source,code,id,salt,nonce);Check(!package.Contains(source.SharedKey)&&!package.Contains(source.Address),"encrypted package hides credentials and public address");
        DateTime time=DateTimeOffset.FromUnixTimeSeconds(now).UtcDateTime;AccessPackageData decoded=AccessPackageCodec.Decrypt(package,code,time);Check(decoded.Address==source.Address&&decoded.SharedKey==source.SharedKey&&decoded.PackageId==id,"valid encrypted package round trips");
        bool wrong=false;try{AccessPackageCodec.Decrypt(package,"000000-000000-000000-000000",time);}catch(System.Security.Cryptography.CryptographicException){wrong=true;}Check(wrong,"wrong import code is rejected");
        bool expired=false;try{AccessPackageCodec.Decrypt(package,code,DateTimeOffset.FromUnixTimeSeconds(source.Expires).UtcDateTime);}catch(InvalidDataException){expired=true;}Check(expired,"expired package is rejected");
        AppConfig config=new AppConfig();config.Normalize();AppConfig applied=AccessPackageCodec.Apply(config,decoded);Check(applied.Servers[0].Address==source.Address&&applied.Servers[0].SharedKey==source.SharedKey,"package fills an empty primary profile");
        string folder=Path.Combine(Path.GetTempPath(),"mulinsen-package-"+Guid.NewGuid().ToString("N"));try{UsedAccessPackageStore store=new UsedAccessPackageStore(folder);Check(!store.IsUsed(id),"new package is unused");store.MarkUsed(id);Check(store.IsUsed(id),"used package is persisted");bool duplicate=false;try{store.MarkUsed(id);}catch(InvalidOperationException){duplicate=true;}Check(duplicate,"one-time package cannot be marked twice");}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
        return failures==0?0:1;
    }
}
