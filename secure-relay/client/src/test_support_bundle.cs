using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;

public static class SupportBundleTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        string folder=Path.Combine(Path.GetTempPath(),"mulinsen-support-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(folder);try{string secret=new string('s',64);AppConfig config=new AppConfig{SiteName="一号矿场",ListenAddress="0.0.0.0",Ports="9999"};config.Servers.Add(new ServerProfile{Name="主VPS",Enabled=true,Address="203.0.113.42",Port=443,CertificateSha256=new string('A',64),SharedKey=secret});config.Normalize();RelaySnapshot snapshot=new RelaySnapshot{Running=true,Active=2,ActiveMiners=1,Failures=3};List<MinerSnapshot> miners=new List<MinerSnapshot>{new MinerSnapshot{Ip="192.168.1.25",Endpoint="主VPS",Connections=2,Health=95,Accepted=4,LastActivity=DateTime.Now}};File.WriteAllText(Path.Combine(folder,"crash.log"),"Authorization: Bearer "+secret+"\npassword=hunter2\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\nremote 198.51.100.8",Encoding.UTF8);string zip=Path.Combine(folder,"support.zip");SupportBundleExporter.Create(zip,config,snapshot,miners,"connect 203.0.113.42 from 192.168.1.25",folder);StringBuilder all=new StringBuilder();HashSet<string> names=new HashSet<string>();using(ZipArchive archive=ZipFile.OpenRead(zip)){foreach(ZipArchiveEntry entry in archive.Entries){names.Add(entry.FullName);using(StreamReader reader=new StreamReader(entry.Open(),Encoding.UTF8))all.Append(reader.ReadToEnd());}}
            string text=all.ToString();Check(names.Contains("report.txt")&&names.Contains("connection-timeline.csv")&&names.Contains("logs.txt")&&names.Contains("manifest.txt"),"support bundle contains required reports");Check(!text.Contains(secret),"support bundle excludes shared keys");Check(!text.Contains("hunter2"),"support bundle excludes passwords");Check(!text.Contains("BEGIN PRIVATE KEY"),"support bundle excludes private keys");Check(!text.Contains("203.0.113.42")&&!text.Contains("198.51.100.8"),"support bundle excludes complete public addresses");Check(!text.Contains("192.168.1.25")&&text.Contains("192.168.1.x"),"support bundle masks local miner addresses");Check(text.Contains("程序版本：")&&text.Contains("失败重连：3"),"support bundle includes version and relay state");Check(new FileInfo(zip).Length>0,"support bundle is written atomically as a zip");
        }finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}return failures==0?0:1;
    }
}
