using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

public static class ServiceTests
{
    private static int failures;
    private static readonly DateTime Epoch=new DateTime(2026,9,21,0,0,0,DateTimeKind.Utc);
    private static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
    private static bool Wait(Func<bool> condition,int milliseconds)
    {
        Stopwatch watch=Stopwatch.StartNew();
        while(watch.ElapsedMilliseconds<milliseconds){if(condition())return true;Thread.Sleep(20);}return condition();
    }
    public static int Main(string[] args)
    {
        try {
            if(args.Length>0&&args[0]=="--fixture")return Fixture(args[1]);
            if(args.Length>0&&args[0]=="--parent") {
                using(var child=new WorkerProcess(Assembly.GetExecutingAssembly().Location,"--fixture healthy")) {
                    File.WriteAllText(args[1],child.Id.ToString());Thread.Sleep(30000);
                }
                return 0;
            }
            var originalEncoding=Console.InputEncoding;
            try { Console.InputEncoding=new System.Text.UTF8Encoding(true);Run(); }
            finally {Console.InputEncoding=originalEncoding;}
            return failures==0?0:1;
        }catch(Exception error){Console.WriteLine("TEST ERROR: "+error.GetType().FullName+": "+error.Message);return 1;}
    }
    private static int Fixture(string mode)
    {
        Console.SetIn(new StreamReader(Console.OpenStandardInput(),new System.Text.UTF8Encoding(false,true),true));
        string startCommand=Console.ReadLine();
        if(startCommand!="GO")return 2;
        if(mode=="crash")return 7;
        string line;
        while((line=Console.ReadLine())!=null) {
            if(line=="STOP")return 0;
            if(mode=="hang") {Thread.Sleep(30000);continue;}
            if(line.StartsWith("PING ",StringComparison.Ordinal)) {
                Console.WriteLine(mode=="invalid"?"PONG 999999999":"PONG "+line.Substring(5));Console.Out.Flush();
            }
        }
        return 0;
    }
    private sealed class FakeWorker : IRelayWorker
    {
        public bool Alive {get;set;}
        public DateTime StartedUtc {get;set;}
        public DateTime? HeartbeatUtc {get;set;}
        public bool Stopped;
        public void Stop(){Stopped=true;Alive=false;}
        public void Dispose(){Stop();}
    }
    private static RecoveryAction Tick(WatchdogSupervisor supervisor,int seconds){return supervisor.Tick(Epoch.AddSeconds(seconds)).Action;}
    private static void Run()
    {
        string executable=Assembly.GetExecutingAssembly().Location;
        using(var worker=new WorkerProcess(executable,"--fixture healthy")) {
            Check(Wait(delegate{return worker.HeartbeatUtc.HasValue;},5000),"real child responds to challenged heartbeat");
            Check(worker.Alive,"responsive child remains alive");
            worker.Stop();Check(!worker.Alive,"stop terminates only owned child");
        }
        using(var worker=new WorkerProcess(executable,"--fixture crash"))
            Check(Wait(delegate{return !worker.Alive;},5000),"real child crash observed");
        using(var worker=new WorkerProcess(executable,"--fixture hang")) {
            Thread.Sleep(250);Check(worker.Alive&&!worker.HeartbeatUtc.HasValue,"hung child cannot claim healthy heartbeat");
            Stopwatch time=Stopwatch.StartNew();worker.Stop();
            Check(!worker.Alive&&time.ElapsedMilliseconds<6000,"hung child termination bounded");
        }
        using(var worker=new WorkerProcess(executable,"--fixture invalid")) {
            Thread.Sleep(250);Check(!worker.HeartbeatUtc.HasValue,"unmatched heartbeat rejected");
        }
        string directory=Path.Combine(Path.GetTempPath(),"mulinsen-service-tests-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
        string pidPath=Path.Combine(directory,"child-id.txt");
        Process parent=Process.Start(new ProcessStartInfo(executable,"--parent \""+pidPath+"\""){UseShellExecute=false,CreateNoWindow=true});
        try {
            if(!Wait(delegate{return File.Exists(pidPath)&&new FileInfo(pidPath).Length>0;},5000))throw new Exception("Parent fixture did not start");
            using(Process child=Process.GetProcessById(Int32.Parse(File.ReadAllText(pidPath)))) {
                parent.Kill();parent.WaitForExit(5000);
                Check(child.WaitForExit(5000),"supervisor crash kills orphan worker through Windows job");
            }
        }finally{if(!parent.HasExited)parent.Kill();parent.Dispose();}

        string record=Path.Combine(directory,"recovery.json");int starts=0;FakeWorker current=null;
        using(var recovery=new RecoverySession(record,true))
        using(var supervisor=new WatchdogSupervisor(recovery,delegate{starts++;current=new FakeWorker{Alive=true,StartedUtc=Epoch.AddSeconds(20)};return current;})) {
            Tick(supervisor,0);Tick(supervisor,10);
            Check(starts==0,"no child start before durable recovery decision");
            Check(Tick(supervisor,20)==RecoveryAction.Restart&&starts==1,"supervisor starts child after reservation");
            current.HeartbeatUtc=Epoch.AddSeconds(30);
            Check(Tick(supervisor,30)==RecoveryAction.Healthy,"worker response prevents recovery");
            current.Alive=false;Tick(supervisor,90);Tick(supervisor,100);FakeWorker previous=current;
            Check(Tick(supervisor,110)==RecoveryAction.Restart&&starts==2&&previous.Stopped,"crashed worker replaced after policy checks");
            supervisor.Dispose();int before=starts;
            Check(Tick(supervisor,200)==RecoveryAction.Stopped&&starts==before&&current.Stopped,"manual stop cannot be undone by subsequent tick");
        }
        int failedStarts=0;
        using(var recovery=new RecoverySession(Path.Combine(directory,"failed.json"),true))
        using(var supervisor=new WatchdogSupervisor(recovery,delegate{failedStarts++;throw new IOException();})) {
            Tick(supervisor,0);Tick(supervisor,10);Tick(supervisor,20);
            Tick(supervisor,60);Tick(supervisor,70);Tick(supervisor,80);
            Tick(supervisor,120);Tick(supervisor,130);Tick(supervisor,140);
            Tick(supervisor,180);Tick(supervisor,190);
            Check(Tick(supervisor,200)==RecoveryAction.Blocked&&failedStarts==3,"process launch failures consume budget and stop");
        }
        AppConfig config=new AppConfig{Ports="19999",ListenAddress="127.0.0.1"};
        config.Servers.Add(new ServerProfile{Address="192.0.2.1",CertificateSha256=new string('A',64),SharedKey="test_only_012345678901234567890123456789"});
        byte[] encrypted=ServiceConfiguration.Encode(config);
        Check(!System.Text.Encoding.UTF8.GetString(encrypted).Contains(config.Servers[0].SharedKey),"service configuration contains no plaintext key");
        AppConfig decoded=ServiceConfiguration.Decode(encrypted);
        Check(decoded.Servers[0].SharedKey==config.Servers[0].SharedKey&&decoded.Ports==config.Ports,"machine-protected configuration round trip");
        Check(config.Servers[0].ProtectedToken=="","export does not modify desktop credentials");
        encrypted[encrypted.Length/2]^=1;bool rejected=false;
        try{ServiceConfiguration.Decode(encrypted);}catch(System.Security.Cryptography.CryptographicException){rejected=true;}
        Check(rejected,"tampered service configuration rejected");
        config.Servers[0].CertificateSha256="bad";rejected=false;
        try{ServiceConfiguration.Encode(config);}catch(InvalidDataException){rejected=true;}
        Check(rejected,"invalid certificate identity blocks service export");
        var permissions=ServiceStoragePermissions.CreateDirectorySecurity();
        Check(permissions.AreAccessRulesProtected&&ServiceStoragePermissions.IsRestricted(permissions),"service ACL disables inheritance and limits principals");
        permissions.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(
            new System.Security.Principal.SecurityIdentifier("S-1-1-0"),System.Security.AccessControl.FileSystemRights.Read,
            System.Security.AccessControl.AccessControlType.Allow));
        Check(!ServiceStoragePermissions.IsRestricted(permissions),"world-readable service data rejected");
        Check(!ServiceStoragePermissions.IsRestricted(new System.Security.AccessControl.DirectorySecurity()),"missing service permissions rejected");
        Console.WriteLine("Service test artifacts: "+directory);
    }
}
