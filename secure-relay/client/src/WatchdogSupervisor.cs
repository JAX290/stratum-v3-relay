using System;

public sealed class WatchdogSupervisor : IDisposable
{
    private readonly RecoverySession recovery;
    private readonly Func<IRelayWorker> start;
    private IRelayWorker worker;
    private bool stopped;
    public WatchdogSupervisor(RecoverySession recovery,Func<IRelayWorker> start)
    {
        if(recovery==null||start==null)throw new ArgumentNullException();
        this.recovery=recovery;this.start=start;
    }
    public RecoveryDecision Tick(DateTime nowUtc)
    {
        if(stopped)return new RecoveryDecision(RecoveryAction.Stopped,"已停止看门狗，不会自动拉起中转。");
        bool alive=worker!=null&&worker.Alive;
        DateTime started=worker==null?nowUtc:worker.StartedUtc;
        DateTime? heartbeat=worker==null?null:worker.HeartbeatUtc;
        return EvaluateAndAct(nowUtc,alive,started,heartbeat);
    }
    public RecoveryDecision TickCurrentTime()
    {
        if(stopped)return new RecoveryDecision(RecoveryAction.Stopped,"已停止看门狗，不会自动拉起中转。");
        bool alive=worker!=null&&worker.Alive;
        DateTime started=worker==null?DateTime.MinValue:worker.StartedUtc;
        DateTime? heartbeat=worker==null?null:worker.HeartbeatUtc;
        DateTime nowUtc=DateTime.UtcNow; // Captured after the concurrent heartbeat snapshot.
        if(worker==null)started=nowUtc;
        return EvaluateAndAct(nowUtc,alive,started,heartbeat);
    }
    private RecoveryDecision EvaluateAndAct(DateTime nowUtc,bool alive,DateTime started,DateTime? heartbeat)
    {
        RecoveryDecision decision=recovery.Evaluate(nowUtc,true,alive,started,heartbeat);
        if(decision.Action==RecoveryAction.Restart) {
            // The budget is durable before either stopping an unresponsive child or starting one.
            if(worker!=null){worker.Dispose();worker=null;}
            try {worker=start();}
            catch {return new RecoveryDecision(RecoveryAction.Wait,"中转启动失败，本次恢复次数已记录，稍后复查。");}
        }
        if(decision.Action==RecoveryAction.Blocked) {
            // Preserve the stop latch. An unresponsive old worker must not keep holding ports.
            if(worker!=null){worker.Dispose();worker=null;}
        }
        return decision;
    }
    public void Dispose()
    {
        if(stopped)return;
        stopped=true;
        if(worker!=null){worker.Dispose();worker=null;}
    }
}
