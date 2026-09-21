using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

public static class ClientCoreTests
{
    private static int failures;
    private static void Check(bool condition,string name){if(condition)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        Check(Regex.IsMatch(AppBrand.Version,@"^\d+\.\d+\.\d+$")&&AppBrand.Title=="木林森中转 v"+AppBrand.Version,"visible application version");
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
        MinerConnection flood=new MinerConnection{State=new MinerState{Ip="192.168.1.21",Connections=1,FirstSeen=DateTime.Now,LastActivity=DateTime.Now}};for(int i=0;i<5000;i++)Feed(flood,"{\"id\":"+i+",\"method\":\"mining.submit\",\"params\":[\"account.worker\",\"job\",\"x\"]}\n",true);System.Reflection.FieldInfo pendingField=typeof(MinerConnection).GetField("pending",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);System.Collections.IDictionary pending=(System.Collections.IDictionary)pendingField.GetValue(flood);Check(pending.Count<=4096,"unanswered share tracking is bounded");
        MinerHistoryFile history=new MinerHistoryFile();history.Miners.Add(new MinerHistoryItem{Ip="192.168.1.20",Shares=new List<ShareHistoryItem>{new ShareHistoryItem{Time=DateTime.Now,Difficulty=1024}}});
        try{using(System.IO.MemoryStream stream=new System.IO.MemoryStream()){new System.Runtime.Serialization.Json.DataContractJsonSerializer(typeof(MinerHistoryFile)).WriteObject(stream,history);stream.Position=0;MinerHistoryFile restored=(MinerHistoryFile)new System.Runtime.Serialization.Json.DataContractJsonSerializer(typeof(MinerHistoryFile)).ReadObject(stream);Check(restored.Miners.Count==1&&restored.Miners[0].Shares.Count==1&&restored.Miners[0].Shares[0].Difficulty==1024,"24-hour history serializes");}}catch(Exception ex){Console.WriteLine("HISTORY ERROR "+ex.GetType().FullName+" "+ex.Message);failures++;}
        RelayManager relay=new RelayManager(delegate(string value){});System.Reflection.FieldInfo minerField=typeof(RelayManager).GetField("miners",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);Dictionary<string,MinerState> registry=(Dictionary<string,MinerState>)minerField.GetValue(relay);registry["203.0.113.250"]=new MinerState{Ip="203.0.113.250",Connections=4,FirstSeen=DateTime.Now,LastActivity=DateTime.Now};registry["203.0.113.251"]=new MinerState{Ip="203.0.113.251",Connections=0,FirstSeen=DateTime.Now,LastActivity=DateTime.Now};Check(relay.Snapshot().ActiveMiners==1&&relay.Snapshot().Active==0,"main count uses unique online IPs");
        CheckStreamBoundaries();
        CheckSnapshotIsolation(relay);
        CheckListenerRollback();
        CheckLocalHealth();
        return failures==0?0:1;
    }
    private static void CheckStreamBoundaries()
    {
        MinerState state=new MinerState{Connections=1,FirstSeen=DateTime.Now.AddHours(-2),LastActivity=DateTime.Now};
        MinerConnection connection=new MinerConnection{State=state};
        Feed(connection,"{\"method\":\"mining.set_difficulty\",\"params\":[64]}\n",false);
        Feed(connection,"{\"id\":7,\"method\":\"mining.sub",true);
        Check(state.Submitted==0,"partial request waits for remaining bytes");
        Feed(connection,"mit\",\"params\":[\"test.worker\"]}\ninvalid-json\n",true);
        Feed(connection,"{\"id\":7,\"result\":false,",false);
        Check(state.Rejected==0,"partial response waits for remaining bytes");
        Feed(connection,"\"error\":[20,\"rejected\"]}\n{\"id\":7,\"result\":true}\n",false);
        Check(state.Submitted==1&&state.Rejected==1&&state.Accepted==0,"rejected share and duplicate response counted once");
        Check(state.Snapshot(DateTime.Now).Hashrate1h==0,"rejected share does not add hashrate");
        DateTime now=DateTime.Now;
        state.Shares.Add(new ShareEvent{Time=now.AddHours(-25),Difficulty=1000});
        state.Shares.Add(new ShareEvent{Time=now.AddMinutes(-30),Difficulty=64});
        MinerSnapshot snapshot=state.Snapshot(now);
        Check(state.Shares.Count==1&&snapshot.Hashrate10m==0&&snapshot.Hashrate1h>0,"history expiry preserves rolling window boundaries");
    }
    private static void CheckSnapshotIsolation(RelayManager relay)
    {
        var field=typeof(RelayManager).GetField("endpointStates",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);
        var states=(Dictionary<string,EndpointState>)field.GetValue(relay);
        states["test"]=new EndpointState{Name="test",Online=true};
        RelaySnapshot first=relay.Snapshot();
        first.Endpoints[0].Online=false;
        first.Endpoints.Clear();
        Check(relay.Snapshot().Endpoints.Count==1&&relay.Snapshot().Endpoints[0].Online,"UI snapshot mutation cannot change endpoint state");
        Check(!relay.RemoveMiner("203.0.113.250"),"online miner cannot be removed");
    }
    private static void CheckListenerRollback()
    {
        var occupied=new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback,0);
        occupied.Server.ExclusiveAddressUse=true;
        occupied.Start();
        int port=((System.Net.IPEndPoint)occupied.LocalEndpoint).Port;
        RelayManager relay=new RelayManager(delegate(string value){});
        bool rejected=false;
        try {
            relay.Start(new AppConfig{ListenAddress="127.0.0.1"},new List<PortRoute>{
                new PortRoute{LocalPort=0,RemotePort=9999},new PortRoute{LocalPort=port,RemotePort=9999}});
        } catch(InvalidOperationException){rejected=true;}
        finally {occupied.Stop();}
        var field=typeof(RelayManager).GetField("listeners",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);
        Check(rejected&&!relay.IsRunning&&((System.Collections.ICollection)field.GetValue(relay)).Count==0,"port conflict rolls back all partially started listeners");
        relay.Stop();
        Check(!relay.Snapshot().Running,"stop remains safe after startup rollback");
    }
    private static void CheckLocalHealth()
    {
        RelayManager relay=new RelayManager(delegate(string value){});
        Check(!relay.LocalHealthCheck(),"stopped relay is not healthy");
        try {
            relay.Start(new AppConfig{ListenAddress="127.0.0.1"},new List<PortRoute>{new PortRoute{LocalPort=0,RemotePort=9999}});
            Check(relay.LocalHealthCheck(),"local health does not require a reachable pool or shares");
            var field=typeof(RelayManager).GetField("listeners",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);
            var listeners=(List<System.Net.Sockets.TcpListener>)field.GetValue(relay);
            listeners[0].Stop();
            Check(!relay.LocalHealthCheck(),"lost listening socket fails local health check");
        } finally {relay.Stop();}
    }
    private static void Feed(MinerConnection c,string value,bool fromMiner){byte[] data=System.Text.Encoding.UTF8.GetBytes(value);c.Observe(data,data.Length,fromMiner);}
}
