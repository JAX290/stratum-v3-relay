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

public sealed class ShareEvent { public DateTime Time; public double Difficulty; }
public sealed class PendingShare { public DateTime Time; public double Difficulty; }
public sealed class MinerState
{
    public string Ip="",Endpoint="",LastError=""; public int Connections,Disconnects,Failures,Submitted,Accepted,Rejected; public long Uploaded,Downloaded; public double LatencyTotal; public int LatencySamples; public DateTime FirstSeen,LastActivity,LastAccepted;
    public readonly HashSet<int> LocalPorts=new HashSet<int>(); public readonly HashSet<int> RemotePorts=new HashSet<int>(); public readonly HashSet<string> Workers=new HashSet<string>(); public readonly HashSet<string> Agents=new HashSet<string>(); public readonly List<ShareEvent> Shares=new List<ShareEvent>();
    public MinerSnapshot Snapshot(DateTime now)
    {
        Shares.RemoveAll(delegate(ShareEvent e){return e.Time<now.AddHours(-24);});
        double reject=Submitted==0?0:(double)Rejected*100/Submitted; double idle=Math.Max(0,(now-LastActivity).TotalSeconds); int health=100;
        if(Connections==0)health=0;else{if(idle>120)health-=50;else if(idle>45)health-=15;if(reject>5)health-=30;else if(reject>1)health-=10;if(Failures>0)health-=Math.Min(15,Failures*3);if(LastAccepted!=DateTime.MinValue&&(now-LastAccepted).TotalMinutes>30)health-=15;}
        health=Math.Max(0,Math.Min(100,health));string text=Connections==0?"离线":health>=85?"健康":health>=60?"注意":"异常";
        return new MinerSnapshot{Ip=Ip,Health=health,HealthText=text,Connections=Connections,Worker=Join(Workers),Agent=Join(Agents),Endpoint=Endpoint,Ports=JoinPorts(LocalPorts),FirstSeen=FirstSeen,LastActivity=LastActivity,Uploaded=Uploaded,Downloaded=Downloaded,Disconnects=Disconnects,Failures=Failures,Submitted=Submitted,Accepted=Accepted,Rejected=Rejected,RejectPercent=reject,LatencyMs=LatencySamples==0?0:(int)(LatencyTotal/LatencySamples),LastAccepted=LastAccepted,Hashrate10m=Estimate(now,TimeSpan.FromMinutes(10)),Hashrate1h=Estimate(now,TimeSpan.FromHours(1)),Hashrate24h=Estimate(now,TimeSpan.FromHours(24)),LastError=LastError};
    }
    private double Estimate(DateTime now,TimeSpan window){DateTime cutoff=now-window;double sum=0;foreach(ShareEvent e in Shares)if(e.Time>=cutoff)sum+=e.Difficulty;double observed=Math.Min(window.TotalSeconds,Math.Max(60,(now-FirstSeen).TotalSeconds));return sum*4294967296.0/observed;}
    private static string Join(HashSet<string> values){string[] a=new string[values.Count];values.CopyTo(a);return String.Join("，",a);}
    private static string JoinPorts(HashSet<int> values){List<int>a=new List<int>(values);a.Sort();List<string>s=new List<string>();foreach(int v in a)s.Add(v.ToString());return String.Join(",",s.ToArray());}
}
public sealed class MinerSnapshot
{
    public string Ip="",HealthText="",Worker="",Agent="",Endpoint="",Ports="",LastError="";public int Health,Connections,Disconnects,Failures,Submitted,Accepted,Rejected,LatencyMs;public long Uploaded,Downloaded;public double RejectPercent,Hashrate10m,Hashrate1h,Hashrate24h;public DateTime FirstSeen,LastActivity,LastAccepted;
}
public sealed class MinerConnection
{
    public MinerState State; private readonly JavaScriptSerializer json=new JavaScriptSerializer(); private string upBuffer="",downBuffer=""; private double difficulty; private DateTime lastPendingCleanup=DateTime.MinValue; private readonly Dictionary<string,PendingShare> pending=new Dictionary<string,PendingShare>();
    public void Observe(byte[] bytes,int count,bool fromMiner)
    {
        string buffer=(fromMiner?upBuffer:downBuffer)+Encoding.UTF8.GetString(bytes,0,count);int lineEnd;
        while((lineEnd=buffer.IndexOf('\n'))>=0){string line=buffer.Substring(0,lineEnd).Trim();buffer=buffer.Substring(lineEnd+1);if(line.Length>0&&line.Length<1048576)Parse(line,fromMiner);}
        if(buffer.Length>262144)buffer="";if(fromMiner)upBuffer=buffer;else downBuffer=buffer;
    }
    private void Parse(string line,bool fromMiner)
    {
        DateTime now=DateTime.UtcNow;if(lastPendingCleanup==DateTime.MinValue||(now-lastPendingCleanup)>TimeSpan.FromMinutes(1)){List<string>expired=new List<string>();foreach(KeyValuePair<string,PendingShare> item in pending)if((now-item.Value.Time)>TimeSpan.FromMinutes(10))expired.Add(item.Key);foreach(string key in expired)pending.Remove(key);if(pending.Count>4096)pending.Clear();lastPendingCleanup=now;}Dictionary<string,object> message;try{message=json.DeserializeObject(line) as Dictionary<string,object>;}catch{return;}if(message==null)return;
        object methodValue;string method=message.TryGetValue("method",out methodValue)&&methodValue!=null?methodValue.ToString():"";IList parameters=Parameters(message);
        if(fromMiner){
            if(method=="mining.subscribe"&&parameters.Count>0&&parameters[0]!=null)AddIdentity(State.Agents,parameters[0]);
            else if(method=="mining.authorize"&&parameters.Count>0&&parameters[0]!=null)AddIdentity(State.Workers,parameters[0]);
            else if(method=="mining.submit"&&parameters.Count>0){State.Submitted++;if(parameters[0]!=null)AddIdentity(State.Workers,parameters[0]);object id;if(message.TryGetValue("id",out id)){if(pending.Count>=4096)pending.Clear();pending[json.Serialize(id)]=new PendingShare{Time=DateTime.UtcNow,Difficulty=difficulty};}}
            return;
        }
        if(method=="mining.set_difficulty"&&parameters.Count>0){double value;if(Double.TryParse(Convert.ToString(parameters[0],System.Globalization.CultureInfo.InvariantCulture),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out value))difficulty=value;return;}
        object responseId;if(!message.TryGetValue("id",out responseId))return;PendingShare share;if(!pending.TryGetValue(json.Serialize(responseId),out share))return;pending.Remove(json.Serialize(responseId));
        object result,error;bool accepted=message.TryGetValue("result",out result)&&result is bool&&(bool)result&&(!message.TryGetValue("error",out error)||error==null);double latency=(DateTime.UtcNow-share.Time).TotalMilliseconds;State.LatencyTotal+=latency;State.LatencySamples++;
        if(accepted){State.Accepted++;State.LastAccepted=DateTime.Now;State.Shares.Add(new ShareEvent{Time=DateTime.Now,Difficulty=share.Difficulty});State.LastError="";}else{State.Rejected++;State.LastError="Share 被矿池拒绝";}
    }
    private static void AddIdentity(HashSet<string> values,object value){if(values.Count<32)values.Add(value.ToString());}
    private static IList Parameters(Dictionary<string,object> message){object value;if(message.TryGetValue("params",out value)&&value is IList)return (IList)value;return new object[0];}
}
