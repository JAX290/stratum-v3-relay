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

[DataContract] public sealed class MinerHistoryFile { [DataMember]public List<MinerHistoryItem> Miners=new List<MinerHistoryItem>(); }
[DataContract] public sealed class ShareHistoryItem { [DataMember]public DateTime Time;[DataMember]public double Difficulty; }
[DataContract] public sealed class MinerHistoryItem
{
    [DataMember]public string Ip="",Endpoint="",LastError="";[DataMember]public int Disconnects,Failures,Submitted,Accepted,Rejected,LatencySamples;[DataMember]public long Uploaded,Downloaded,FirstSeenTicks,LastActivityTicks,LastAcceptedTicks;[DataMember]public double LatencyTotal;[DataMember]public List<int> LocalPorts=new List<int>();[DataMember]public List<string> Workers=new List<string>(),Agents=new List<string>();[DataMember]public List<ShareHistoryItem> Shares=new List<ShareHistoryItem>();
}
public static class MinerHistoryStore
{
    public static string FilePath { get { return Path.Combine(ConfigStore.Folder,"miner-history.json"); } }
    public static void Save(IEnumerable<MinerState> states)
    {
        DateTime cutoff=DateTime.Now.AddHours(-24);MinerHistoryFile file=new MinerHistoryFile();foreach(MinerState s in states){s.Shares.RemoveAll(delegate(ShareEvent e){return e.Time<cutoff;});if(s.LastActivity<cutoff)continue;MinerHistoryItem item=new MinerHistoryItem{Ip=s.Ip,Endpoint=s.Endpoint,LastError=s.LastError,Disconnects=s.Disconnects,Failures=s.Failures,Submitted=s.Submitted,Accepted=s.Accepted,Rejected=s.Rejected,LatencySamples=s.LatencySamples,Uploaded=s.Uploaded,Downloaded=s.Downloaded,LatencyTotal=s.LatencyTotal,FirstSeenTicks=s.FirstSeen.Ticks,LastActivityTicks=s.LastActivity.Ticks,LastAcceptedTicks=s.LastAccepted.Ticks,LocalPorts=new List<int>(s.LocalPorts),Workers=new List<string>(s.Workers),Agents=new List<string>(s.Agents)};foreach(ShareEvent e in s.Shares)item.Shares.Add(new ShareHistoryItem{Time=e.Time,Difficulty=e.Difficulty});file.Miners.Add(item);}
        Directory.CreateDirectory(ConfigStore.Folder);string temporary=FilePath+".tmp";using(FileStream stream=File.Create(temporary))new DataContractJsonSerializer(typeof(MinerHistoryFile)).WriteObject(stream,file);if(File.Exists(FilePath))File.Replace(temporary,FilePath,null);else File.Move(temporary,FilePath);
    }
    public static List<MinerHistoryItem> Load(){try{using(FileStream stream=File.OpenRead(FilePath)){MinerHistoryFile file=(MinerHistoryFile)new DataContractJsonSerializer(typeof(MinerHistoryFile)).ReadObject(stream);return file.Miners??new List<MinerHistoryItem>();}}catch{return new List<MinerHistoryItem>();}}
}
