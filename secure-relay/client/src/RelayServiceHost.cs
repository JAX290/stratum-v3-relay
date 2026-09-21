using System;
using System.IO;
using System.Reflection;
using System.ServiceProcess;
using System.Threading;
using System.Threading.Tasks;

// Separate executable. The desktop application's single-instance mutex and crash helper
// are intentionally not used by a Windows service.
public sealed class RelayServiceHost : ServiceBase
{
    public const string InstalledServiceName="MulinSenRelaySupervisor";
    public static readonly string DataFolder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"MulinSenRelayService");
    private readonly ManualResetEvent stop=new ManualResetEvent(false);
    private readonly ManualResetEvent ready=new ManualResetEvent(false);
    private Thread supervisorThread;
    private Exception startupFailure;
    public RelayServiceHost()
    {
        ServiceName=InstalledServiceName;CanStop=true;CanShutdown=true;AutoLog=false;
    }
    protected override void OnStart(string[] args)
    {
        if(supervisorThread!=null&&supervisorThread.IsAlive)throw new InvalidOperationException();
        stop.Reset();ready.Reset();startupFailure=null;
        supervisorThread=new Thread(RunSupervisor);supervisorThread.IsBackground=true;
        supervisorThread.Name="MulinSen supervisor";supervisorThread.Start();
        if(!ready.WaitOne(5000)||startupFailure!=null) {
            stop.Set();throw new InvalidOperationException("服务无法启动，请检查安装目录、配置和恢复记录。");
        }
    }
    private void RunSupervisor()
    {
        try {
            if(!File.Exists(Path.Combine(DataFolder,"service-config.dat")))throw new IOException();
            using(RecoverySession recovery=new RecoverySession(Path.Combine(DataFolder,"recovery.json"),false))
            using(WatchdogSupervisor supervisor=new WatchdogSupervisor(recovery,delegate {
                return new WorkerProcess(Assembly.GetExecutingAssembly().Location,"--worker");
            })) {
                ready.Set();
                while(!stop.WaitOne(0)) {
                    RecoveryDecision result=supervisor.Tick(DateTime.UtcNow);
                    WriteStatus(result);
                    if(result.Action==RecoveryAction.Blocked)break;
                    if(stop.WaitOne(10000))break;
                }
            }
        } catch(Exception error) {
            startupFailure=error;ready.Set();
            try{WriteStatus(new RecoveryDecision(RecoveryAction.Blocked,"服务异常，已停止自动恢复，请检查安装与磁盘权限。"));}catch{}
        } finally {
            // Ask SCM to reflect the stopped state instead of displaying a dead thread as running.
            if(!stop.WaitOne(0)) {ExitCode=1;Stop();}
        }
    }
    private static void WriteStatus(RecoveryDecision decision)
    {
        string path=Path.Combine(DataFolder,"service-status.txt"),temporary=path+".tmp";
        File.WriteAllText(temporary,DateTime.UtcNow.ToString("o")+Environment.NewLine+decision.Action+Environment.NewLine+decision.Message);
        if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);
    }
    protected override void OnStop()
    {
        stop.Set();
        if(supervisorThread!=null&&Thread.CurrentThread!=supervisorThread&&!supervisorThread.Join(10000))
            throw new InvalidOperationException("中转服务尚未完成停止，请检查服务状态。");
        if(Thread.CurrentThread!=supervisorThread)
            try{WriteStatus(new RecoveryDecision(RecoveryAction.Stopped,"服务已停止，不会自动拉起中转。"));}catch{}
    }
    protected override void OnShutdown(){OnStop();}

    public static int RunWorker()
    {
        if(Console.ReadLine()!="GO")return 2;
        RelayManager manager=null;
        try {
            ConfigStore.SetServiceFolder(DataFolder);
            byte[] encrypted=File.ReadAllBytes(Path.Combine(DataFolder,"service-config.dat"));
            AppConfig config=ServiceConfiguration.Decode(encrypted);
            manager=new RelayManager(delegate(string ignored){});
            manager.Start(config,PortRoute.Parse(config.Ports));
            DateTime lastSave=DateTime.UtcNow;
            string request;
            while((request=Console.ReadLine())!=null) {
                if(request=="STOP")break;
                long sequence;
                if(!request.StartsWith("PING ",StringComparison.Ordinal)||!Int64.TryParse(request.Substring(5),out sequence)||sequence<=0)continue;
                // Use the same thread pool and state lock as relay work. If those stop responding,
                // no PONG is produced; a separate timer cannot falsely certify this process.
                bool healthy=Task.Run(delegate {return manager.LocalHealthCheck();}).GetAwaiter().GetResult();
                if(!healthy)return 3;
                if(DateTime.UtcNow-lastSave>TimeSpan.FromSeconds(30)){manager.SaveMinerHistory();lastSave=DateTime.UtcNow;}
                Console.WriteLine("PONG "+sequence);Console.Out.Flush();
            }
            return 0;
        } catch {return 1;}
        finally {if(manager!=null){manager.Stop();manager.SaveMinerHistory();}}
    }
}

public static class RelayServiceProgram
{
    public static int Main(string[] args)
    {
        if(args.Length==1&&args[0]=="--worker")return RelayServiceHost.RunWorker();
        if(args.Length==1&&args[0]=="--service") {
            ServiceBase.Run(new RelayServiceHost());return 0;
        }
        Console.WriteLine("这是服务组件，请通过安装向导配置。不要用它替换桌面客户端。");
        return 2;
    }
}
