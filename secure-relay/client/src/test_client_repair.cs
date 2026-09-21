using System;
using System.Collections.Generic;

public static class ClientRepairTests
{
    private static int failures;
    private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        Check(LatestClientRelease.CompareVersions("2.4.0","2.3.9")>0,"new minor version is detected");
        Check(LatestClientRelease.CompareVersions("2.3.3","2.3.3")==0,"equal release is current");
        Check(LatestClientRelease.CompareVersions("bad","2.3.3")==0,"invalid remote version fails closed");
        AppConfig bad=new AppConfig();bad.Servers.Clear();bad.Ports="bad";
        List<ClientHealthIssue> issues=ClientHealthInspector.Inspect(bad,null,"C:\\relay.exe");
        Check(issues.Count==1&&issues[0].Code=="CONFIG","invalid configuration is reported before repair actions");
        Check(issues[0].Repairable&&!issues[0].RequiresAdministrator,"configuration rollback is a standard-user repair");
        LatestClientRelease release=new LatestClientRelease{Version="2.3.4"};Check(release.IsNewerThan("2.3.3"),"release object reports an available update");
        return failures==0?0:1;
    }
}
