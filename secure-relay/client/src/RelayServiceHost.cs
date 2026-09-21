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
    public static readonly string DefaultDataFolder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"MulinSenRelayService");
    private readonly string dataFolder;
    private readonly ManualResetEvent stop=new ManualResetEvent(false);
    private readonly ManualResetEvent ready=new ManualResetEvent(false);
    private Thread supervisorThread;
    private Exception startupFailure;
    public RelayServiceHost() : this(InstalledServiceName,DefaultDataFolder) {}
    internal RelayServiceHost(string serviceName,string directory)
    {
        ServiceName=serviceName;dataFolder=Path.GetFullPath(directory);CanStop=true;CanShutdown=true;AutoLog=false;
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
            ServiceStoragePermissions.VerifyDirectory(dataFolder);
            if(!File.Exists(Path.Combine(dataFolder,"service-config.dat")))throw new IOException();
            ServiceStoragePermissions.VerifyFile(Path.Combine(dataFolder,"service-config.dat"));
            ServiceStoragePermissions.VerifyFile(Path.Combine(dataFolder,"recovery.json"));
            using(RecoverySession recovery=new RecoverySession(Path.Combine(dataFolder,"recovery.json"),false))
            using(WatchdogSupervisor supervisor=new WatchdogSupervisor(recovery,delegate {
                return new WorkerProcess(Assembly.GetExecutingAssembly().Location,"--worker \""+dataFolder+"\"");
            })) {
                ready.Set();
                while(!stop.WaitOne(0)) {
                    RecoveryDecision result=supervisor.TickCurrentTime();
                    WriteStatus(result,dataFolder);
                    if(result.Action==RecoveryAction.Blocked)break;
                    if(stop.WaitOne(10000))break;
                }
            }
        } catch(Exception error) {
            startupFailure=error;ready.Set();
            string message="服务异常，已停止自动恢复，请检查安装与磁盘权限。";
            if(Path.GetFileName(Assembly.GetExecutingAssembly().Location).EndsWith(".test.exe",StringComparison.OrdinalIgnoreCase))
                message+=" TEST: "+error.GetType().Name+": "+error.Message;
            try{WriteStatus(new RecoveryDecision(RecoveryAction.Blocked,message),dataFolder);}catch{}
        } finally {
            // Ask SCM to reflect the stopped state instead of displaying a dead thread as running.
            if(!stop.WaitOne(0)) {ExitCode=1;Stop();}
        }
    }
    private static void WriteStatus(RecoveryDecision decision,string directory)
    {
        string path=Path.Combine(directory,"service-status.txt"),temporary=path+".tmp";
        File.WriteAllText(temporary,DateTime.UtcNow.ToString("o")+Environment.NewLine+decision.Action+Environment.NewLine+decision.Message);
        if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);
    }
    protected override void OnStop()
    {
        stop.Set();
        if(supervisorThread!=null&&Thread.CurrentThread!=supervisorThread&&!supervisorThread.Join(10000))
            throw new InvalidOperationException("中转服务尚未完成停止，请检查服务状态。");
        if(Thread.CurrentThread!=supervisorThread)
            try{WriteStatus(new RecoveryDecision(RecoveryAction.Stopped,"服务已停止，不会自动拉起中转。"),dataFolder);}catch{}
    }
    protected override void OnShutdown(){OnStop();}

    public static int RunWorker(string directory)
    {
        Console.SetIn(new StreamReader(Console.OpenStandardInput(),new System.Text.UTF8Encoding(false,true),true));
        string startCommand=Console.ReadLine();
        if(startCommand!="GO")return 2;
        RelayManager manager=null;NetworkRecoveryMonitor networkRecovery=null;object runtimeGate=new object();bool workerActive=true;
        try {
            string dataFolder=Path.GetFullPath(directory);
            ServiceStoragePermissions.VerifyDirectory(dataFolder);
            ServiceStoragePermissions.VerifyFile(Path.Combine(dataFolder,"service-config.dat"));
            ConfigStore.SetServiceFolder(dataFolder);
            byte[] encrypted=File.ReadAllBytes(Path.Combine(dataFolder,"service-config.dat"));
            AppConfig config=ServiceConfiguration.Decode(encrypted);
            manager=new RelayManager(delegate(string ignored){});
            manager.Start(config,PortRoute.Parse(config.Ports));
            networkRecovery=new NetworkRecoveryMonitor(delegate{return workerActive;},NetworkHelper.GetLanIPv4,
                delegate(string reason){lock(runtimeGate){manager.Stop();manager.Start(config,PortRoute.Parse(config.Ports));}},
                delegate(string phase,string message){manager.SetRecoveryState(phase,message);});
            DateTime lastSave=DateTime.UtcNow;
            string request;
            while((request=Console.ReadLine())!=null) {
                if(request=="STOP")break;
                long sequence;
                if(!request.StartsWith("PING ",StringComparison.Ordinal)||!Int64.TryParse(request.Substring(5),out sequence)||sequence<=0)continue;
                // Use the same thread pool and state lock as relay work. If those stop responding,
                // no PONG is produced; a separate timer cannot falsely certify this process.
                bool healthy=Task.Run(delegate {lock(runtimeGate)return manager.LocalHealthCheck();}).GetAwaiter().GetResult();
                if(!healthy)return 3;
                if(DateTime.UtcNow-lastSave>TimeSpan.FromSeconds(30)){manager.SaveMinerHistory();lastSave=DateTime.UtcNow;}
                Console.WriteLine("PONG "+sequence);Console.Out.Flush();
            }
            return 0;
        } catch {return 1;}
        finally {workerActive=false;if(networkRecovery!=null)networkRecovery.Dispose();if(manager!=null){manager.Stop();manager.SaveMinerHistory();}}
    }
}

public static class RelayServiceProgram
{
    private static bool IsTestBuild()
    {
        return Path.GetFileName(Assembly.GetExecutingAssembly().Location).EndsWith(".test.exe",StringComparison.OrdinalIgnoreCase);
    }
    private static int ConfigurationFailure(Exception error)
    {
        if(IsTestBuild())Console.Error.WriteLine(error.GetType().Name+": "+error.Message);
        return 1;
    }
    public static int Main(string[] args)
    {
        if(args.Length==2&&args[0]=="--worker")return RelayServiceHost.RunWorker(args[1]);
        if(args.Length==3&&args[0]=="--prepare") {
            try{ServiceInstallerData.Prepare(args[1],args[2]);return 0;}catch(Exception error){return ConfigurationFailure(error);}
        }
        if(args.Length==2&&args[0]=="--validate-prepared") {
            try{ServiceInstallerData.ValidatePrepared(args[1]);return 0;}catch(Exception error){return ConfigurationFailure(error);}
        }
        if(args.Length==1&&args[0]=="--service") {
            ServiceBase.Run(new RelayServiceHost());return 0;
        }
        if(args.Length==2&&args[0]=="--service-test"&&
            IsTestBuild()) {
            ServiceBase.Run(new RelayServiceHost("MulinSenRelaySupervisorTest",args[1]));return 0;
        }
        if(args.Length==3&&args[0]=="--create-test-config"&&
            IsTestBuild()) {
            int port;try{if(!Int32.TryParse(args[2],out port))return 2;ServiceInstallerData.CreateSyntheticTestConfig(args[1],port);return 0;}catch(Exception error){return ConfigurationFailure(error);}
        }
        Console.WriteLine("这是服务组件，请通过安装向导配置。不要用它替换桌面客户端。");
        return 2;
    }
}
