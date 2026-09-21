using System;
using System.Collections.Generic;
using System.Runtime.Serialization;

// Pure recovery gate shared by the future service host and independent watchdog.
// It never starts/kills a process. Restart is returned only after durable reservation.
public enum RecoveryAction { Healthy, Wait, Stopped, Restart, Blocked }

public sealed class RecoveryDecision
{
    public readonly RecoveryAction Action;
    public readonly string Message;
    public RecoveryDecision(RecoveryAction action, string message) { Action=action; Message=message; }
}

[DataContract]
public sealed class RecoveryState
{
    [DataMember] public int Schema=1;
    [DataMember] public List<DateTime> AttemptsUtc=new List<DateTime>();
    [DataMember] public bool Blocked;
    [DataMember] public DateTime LastObservedUtc=DateTime.SpecifyKind(DateTime.MinValue,DateTimeKind.Utc);
    [DataMember] public string Reason="";

    public RecoveryState Copy()
    {
        return new RecoveryState { Schema=Schema, AttemptsUtc=AttemptsUtc==null?null:new List<DateTime>(AttemptsUtc),
            Blocked=Blocked, LastObservedUtc=LastObservedUtc, Reason=Reason };
    }
}

public sealed class RecoveryPolicy
{
    public static readonly TimeSpan HeartbeatTimeout=TimeSpan.FromSeconds(60);
    public static readonly TimeSpan ProbeInterval=TimeSpan.FromSeconds(10);
    public static readonly TimeSpan Cooldown=TimeSpan.FromSeconds(60);
    public static readonly TimeSpan BudgetWindow=TimeSpan.FromMinutes(10);
    public const int MaximumAttempts=3;
    public const int RequiredUnhealthyProbes=3;
    private RecoveryState state;
    private int unhealthyProbes;
    private DateTime lastProbeUtc;

    // Missing/corrupt state must not silently reset the restart budget.
    // New installations explicitly supply a new RecoveryState.
    public RecoveryPolicy(RecoveryState restored)
    {
        if (restored==null || restored.Schema!=1 || restored.AttemptsUtc==null ||
            restored.AttemptsUtc.Count>MaximumAttempts) {
            state=new RecoveryState { Blocked=true, Reason="恢复记录缺失或损坏，请管理员检查后再启用。" };
        } else { state=restored.Copy(); }
    }

    public RecoveryState Snapshot() { return state.Copy(); }

    public RecoveryDecision Evaluate(DateTime nowUtc, bool enabled, bool processAlive,
        DateTime processStartedUtc, DateTime? heartbeatUtc, Action<RecoveryState> persist)
    {
        if (!enabled) {
            unhealthyProbes=0;
            lastProbeUtc=DateTime.MinValue;
            return Decide(RecoveryAction.Stopped,"已人工停用，不会自动启动中转。");
        }
        if (state.Blocked) return Decide(RecoveryAction.Blocked,state.Reason);
        if (nowUtc.Kind!=DateTimeKind.Utc || (state.LastObservedUtc!=DateTime.MinValue &&
            (state.LastObservedUtc.Kind!=DateTimeKind.Utc || nowUtc<state.LastObservedUtc)))
            return Block("电脑时间异常，已停止自动恢复，请检查时间设置。",persist);
        foreach (DateTime attempt in state.AttemptsUtc) {
            if (attempt.Kind!=DateTimeKind.Utc || attempt>nowUtc)
                return Block("恢复记录中的时间异常，已停止自动恢复。",persist);
        }
        state.LastObservedUtc=nowUtc;
        if (processAlive) {
            if (processStartedUtc.Kind!=DateTimeKind.Utc || processStartedUtc>nowUtc ||
                (heartbeatUtc.HasValue && (heartbeatUtc.Value.Kind!=DateTimeKind.Utc || heartbeatUtc.Value>nowUtc)))
                return Block("进程心跳时间异常，已停止自动恢复。",persist);
            bool currentHeartbeat=heartbeatUtc.HasValue && heartbeatUtc.Value>=processStartedUtc;
            if (currentHeartbeat && nowUtc-heartbeatUtc.Value<HeartbeatTimeout) {
                unhealthyProbes=0;
                lastProbeUtc=DateTime.MinValue;
                return Decide(RecoveryAction.Healthy,"中转进程有响应。");
            }
            if (nowUtc-processStartedUtc<HeartbeatTimeout)
                return Decide(RecoveryAction.Wait,"中转正在启动，等待本地心跳。");
        }
        if (lastProbeUtc!=DateTime.MinValue && nowUtc-lastProbeUtc<ProbeInterval)
            return Decide(RecoveryAction.Wait,"正在复查中转状态。");
        lastProbeUtc=nowUtc;
        unhealthyProbes++;
        if (unhealthyProbes<RequiredUnhealthyProbes)
            return Decide(RecoveryAction.Wait,"中转暂时未响应，正在连续复查。");

        state.AttemptsUtc.RemoveAll(delegate(DateTime time) { return nowUtc-time>=BudgetWindow; });
        if (state.AttemptsUtc.Count>=MaximumAttempts)
            return Block("10 分钟内已尝试恢复 3 次，已停止自动恢复，请管理员处理。",persist);
        foreach (DateTime attempt in state.AttemptsUtc) {
            if (nowUtc-attempt<Cooldown)
                return Decide(RecoveryAction.Wait,"正在恢复冷却期，稍后再检查。");
        }
        RecoveryState reservation=state.Copy();
        reservation.AttemptsUtc.Add(nowUtc);
        try {
            if (persist==null) throw new InvalidOperationException();
            persist(reservation.Copy());
        } catch {
            // Do not expose exception text, which can contain paths or credentials.
            state.Blocked=true;
            state.Reason="无法保存恢复记录，已停止自动恢复，请检查磁盘和权限。";
            return Decide(RecoveryAction.Blocked,state.Reason);
        }
        state=reservation;
        unhealthyProbes=0;
        return Decide(RecoveryAction.Restart,processAlive?
            "中转持续无响应，已记录本次恢复，请重新启动中转进程。":
            "中转进程已退出，已记录本次恢复，请重新启动中转进程。");
    }

    private RecoveryDecision Block(string reason, Action<RecoveryState> persist)
    {
        state.Blocked=true;
        state.Reason=reason;
        try { if (persist!=null) persist(state.Copy()); } catch { }
        return Decide(RecoveryAction.Blocked,reason);
    }
    private static RecoveryDecision Decide(RecoveryAction action,string message)
    {
        return new RecoveryDecision(action,message);
    }
}
