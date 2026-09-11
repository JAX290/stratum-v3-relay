using System;
using System.Collections.Generic;

public static class ClientCoreTests
{
    private static int failures;
    private static void Check(bool condition,string name){if(condition)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        List<PortRoute> routes=PortRoute.Parse("9999, 10041=10001");
        Check(routes.Count==2,"route count");
        Check(routes[0].LocalPort==9999&&routes[0].RemotePort==9999,"same-port route");
        Check(routes[1].LocalPort==10041&&routes[1].RemotePort==10001,"mapped route");
        bool duplicateRejected=false;try{PortRoute.Parse("9999,9999=10001");}catch(InvalidOperationException){duplicateRejected=true;}
        Check(duplicateRejected,"duplicate local port rejected");
        AppConfig legacy=new AppConfig{ServerAddress="192.0.2.1",ServerPort=452,SiteName="测试矿场"};legacy.Normalize();
        Check(legacy.Servers.Count>=3&&legacy.Servers[0].Address=="192.0.2.1"&&legacy.Servers[0].Port==452,"legacy config migration");
        Check(!legacy.Servers[1].Enabled&&!legacy.Servers[2].Enabled,"backup defaults disabled");
        Check(legacy.HealthCheckMinutes==5,"health check interval defaults to five minutes");
        legacy.HealthCheckMinutes=0;legacy.Normalize();Check(legacy.HealthCheckMinutes==5,"invalid health interval is repaired");
        AppConfig live=new AppConfig();live.Servers.Add(new ServerProfile{Name="主VPS",SharedKey="abcdef0123456789abcdef0123456789"});live.Normalize();
        Check(live.Servers[0].SharedKey=="abcdef0123456789abcdef0123456789","in-memory key preserved before save");
        MinerState miner=new MinerState{Ip="192.168.1.20",Connections=1,FirstSeen=DateTime.Now.AddMinutes(-10),LastActivity=DateTime.Now};
        MinerConnection connection=new MinerConnection{State=miner};
        Feed(connection,"{\"id\":1,\"method\":\"mining.subscribe\",\"params\":[\"Antminer/S19\"]}\n",true);
        Feed(connection,"{\"id\":null,\"method\":\"mining.set_difficulty\",\"params\":[1024]}\n",false);
        Feed(connection,"{\"id\":2,\"method\":\"mining.authorize\",\"params\":[\"account.worker01\",\"x\"]}\n",true);
        Feed(connection,"{\"id\":3,\"method\":\"mining.submit\",\"params\":[\"account.worker01\",\"job\",\"x\"]}\n",true);
        Feed(connection,"{\"id\":3,\"result\":true,\"error\":null}\n",false);
        MinerSnapshot snapshot=miner.Snapshot(DateTime.Now);
        Check(snapshot.Worker.Contains("account.worker01")&&snapshot.Agent.Contains("Antminer/S19"),"stratum identity parsed");
        Check(snapshot.Submitted==1&&snapshot.Accepted==1&&snapshot.Rejected==0,"accepted share tracked");
        Check(snapshot.Hashrate10m>0&&snapshot.Hashrate1h>0&&snapshot.Hashrate24h>0,"rolling hashrates estimated");
        MinerHistoryFile history=new MinerHistoryFile();history.Miners.Add(new MinerHistoryItem{Ip="192.168.1.20",Shares=new List<ShareHistoryItem>{new ShareHistoryItem{Time=DateTime.Now,Difficulty=1024}}});
        try{using(System.IO.MemoryStream stream=new System.IO.MemoryStream()){new System.Runtime.Serialization.Json.DataContractJsonSerializer(typeof(MinerHistoryFile)).WriteObject(stream,history);stream.Position=0;MinerHistoryFile restored=(MinerHistoryFile)new System.Runtime.Serialization.Json.DataContractJsonSerializer(typeof(MinerHistoryFile)).ReadObject(stream);Check(restored.Miners.Count==1&&restored.Miners[0].Shares.Count==1&&restored.Miners[0].Shares[0].Difficulty==1024,"24-hour history serializes");}}catch(Exception ex){Console.WriteLine("HISTORY ERROR "+ex.GetType().FullName+" "+ex.Message);failures++;}
        RelayManager relay=new RelayManager(delegate(string value){});System.Reflection.FieldInfo minerField=typeof(RelayManager).GetField("miners",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);Dictionary<string,MinerState> registry=(Dictionary<string,MinerState>)minerField.GetValue(relay);registry["203.0.113.250"]=new MinerState{Ip="203.0.113.250",Connections=4,FirstSeen=DateTime.Now,LastActivity=DateTime.Now};registry["203.0.113.251"]=new MinerState{Ip="203.0.113.251",Connections=0,FirstSeen=DateTime.Now,LastActivity=DateTime.Now};Check(relay.Snapshot().ActiveMiners==1&&relay.Snapshot().Active==0,"main count uses unique online IPs");
        return failures==0?0:1;
    }
    private static void Feed(MinerConnection c,string value,bool fromMiner){byte[] data=System.Text.Encoding.UTF8.GetBytes(value);c.Observe(data,data.Length,fromMiner);}
}
