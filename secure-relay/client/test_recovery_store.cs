using System;
using System.IO;
using System.Threading;

public static class RecoveryStoreTests
{
    private static int failures;
    private static readonly DateTime Epoch=new DateTime(2026,9,21,0,0,0,DateTimeKind.Utc);
    private static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
    private static RecoveryAction Attempt(RecoverySession session,int seconds)
    {
        session.Evaluate(Epoch.AddSeconds(seconds),true,false,Epoch,null);
        session.Evaluate(Epoch.AddSeconds(seconds+10),true,false,Epoch,null);
        return session.Evaluate(Epoch.AddSeconds(seconds+20),true,false,Epoch,null).Action;
    }
    public static int Main()
    {
        try { return Run(); }
        catch(Exception error) { Console.WriteLine("STORE ERROR TYPE: "+error.GetType().FullName); Console.WriteLine(error.Message);return 1; }
    }
    private static int Run()
    {
        string directory=Path.Combine(Path.GetTempPath(),"mulinsen-recovery-store-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string path=Path.Combine(directory,"recovery.json");
        using(var session=new RecoverySession(path,true)) {
            Check(File.Exists(path),"first installation creates recovery record");
            Check(Attempt(session,0)==RecoveryAction.Restart,"durable reservation allows recovery");
            Check(File.Exists(path+".bak"),"atomic replacement retains previous record");
            bool secondRejected=false;
            Thread contender=new Thread(delegate(){try{using(var other=new RecoverySession(path,false)){} }catch(IOException){secondRejected=true;}});
            contender.Start();contender.Join();
            Check(secondRejected,"concurrent watchdog lease rejected");
            bool wrongThreadRejected=false;
            Thread wrongThread=new Thread(delegate(){try{session.Evaluate(Epoch,true,false,Epoch,null);}catch(InvalidOperationException){wrongThreadRejected=true;}});
            wrongThread.Start();wrongThread.Join();
            Check(wrongThreadRejected,"cross-thread evaluation rejected");
        }
        using(var session=new RecoverySession(path,false)) {
            Check(Attempt(session,30)==RecoveryAction.Wait,"restart preserves cooldown");
            Check(Attempt(session,60)==RecoveryAction.Restart,"second reservation after restart");
            Check(Attempt(session,120)==RecoveryAction.Restart,"third reservation persisted");
            Check(Attempt(session,180)==RecoveryAction.Blocked,"restart cannot erase recovery budget");
        }
        using(var session=new RecoverySession(path,false))
            Check(Attempt(session,2000)==RecoveryAction.Blocked,"stop protection survives disk reload");
        bool resetRejected=false;
        try{using(var session=new RecoverySession(path,true)){} }catch(IOException){resetRejected=true;}
        Check(resetRejected,"initialization cannot reset an existing budget");
        File.WriteAllText(path,"invalid state");
        using(var session=new RecoverySession(path,false))
            Check(Attempt(session,2100)==RecoveryAction.Blocked,"corrupt primary never falls back to lower backup budget");
        string missing=Path.Combine(directory,"missing.json");
        using(var session=new RecoverySession(missing,false))
            Check(Attempt(session,2200)==RecoveryAction.Blocked,"missing state never enables recovery");
        string locked=Path.Combine(directory,"locked.json");
        using(var session=new RecoverySession(locked,true)) {
            using(var blocker=new FileStream(locked,FileMode.Open,FileAccess.Read,FileShare.None))
                Check(Attempt(session,2300)==RecoveryAction.Blocked,"failed atomic replacement prevents restart");
            Check(Directory.GetFiles(directory,"*.tmp").Length==0,"failed write removes its temporary file");
        }
        Console.WriteLine("Recovery test artifacts: "+directory);
        return failures==0?0:1;
    }
}
