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
        AppConfig live=new AppConfig();live.Servers.Add(new ServerProfile{Name="主VPS",SharedKey="abcdef0123456789abcdef0123456789"});live.Normalize();
        Check(live.Servers[0].SharedKey=="abcdef0123456789abcdef0123456789","in-memory key preserved before save");
        return failures==0?0:1;
    }
}
