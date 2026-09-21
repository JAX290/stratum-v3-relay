using System;
using System.IO;
using System.Runtime.Serialization.Json;

public static class RecoveryTests
{
    private static int failures;
    private static readonly DateTime Epoch=new DateTime(2026,9,21,0,0,0,DateTimeKind.Utc);
    private static void Check(bool condition,string name)
    {
        Console.WriteLine((condition?"PASS ":"FAIL ")+name);
        if (!condition) failures++;
    }
    private static RecoveryDecision Probe(RecoveryPolicy policy,int seconds,bool alive,DateTime? heartbeat,Action<RecoveryState> save)
    {
        return policy.Evaluate(Epoch.AddSeconds(seconds),true,alive,Epoch,heartbeat,save);
    }
    public static int Main()
    {
        RecoveryState saved=null;
        Action<RecoveryState> save=delegate(RecoveryState value){saved=value;};
        RecoveryPolicy policy=new RecoveryPolicy(new RecoveryState());
        Check(Probe(policy,0,true,null,save).Action==RecoveryAction.Wait,"startup grace period");
        Check(Probe(policy,59,true,null,save).Action==RecoveryAction.Wait,"startup grace boundary");
        Check(Probe(policy,60,true,null,save).Action==RecoveryAction.Wait,"first stale heartbeat waits");
        Check(Probe(policy,60,true,null,save).Action==RecoveryAction.Wait,"rapid polling cannot consume unhealthy probes");
        Check(Probe(policy,70,true,null,save).Action==RecoveryAction.Wait,"second stale heartbeat waits");
        Check(Probe(policy,80,true,null,save).Action==RecoveryAction.Restart&&saved.AttemptsUtc.Count==1,"third stale probe reserves recovery");
        Check(Probe(policy,90,false,null,save).Action==RecoveryAction.Wait,"restart failure rechecked");
        Probe(policy,100,false,null,save);
        Check(Probe(policy,110,false,null,save).Action==RecoveryAction.Wait,"cooldown prevents repeated restart");
        Check(Probe(policy,140,false,null,save).Action==RecoveryAction.Restart&&saved.AttemptsUtc.Count==2,"cooldown expiry permits next attempt");
        Probe(policy,150,false,null,save); Probe(policy,160,false,null,save);
        Check(Probe(policy,200,false,null,save).Action==RecoveryAction.Restart&&saved.AttemptsUtc.Count==3,"third recovery recorded");
        Probe(policy,210,false,null,save); Probe(policy,220,false,null,save);
        Check(Probe(policy,230,false,null,save).Action==RecoveryAction.Blocked&&saved.Blocked,"fourth recovery blocked and latched");
        RecoveryState serialized;
        using (MemoryStream stream=new MemoryStream()) {
            var serializer=new DataContractJsonSerializer(typeof(RecoveryState));
            serializer.WriteObject(stream,saved);stream.Position=0;
            serialized=(RecoveryState)serializer.ReadObject(stream);
        }
        policy=new RecoveryPolicy(serialized);
        Check(Probe(policy,2000,false,null,save).Action==RecoveryAction.Blocked,"watchdog restart preserves stop protection");
        Check(policy.Evaluate(Epoch.AddSeconds(2001),false,false,Epoch,null,save).Action==RecoveryAction.Stopped,"manual disable wins over automatic recovery");
        policy=new RecoveryPolicy(new RecoveryState());
        Probe(policy,80,false,null,save); Probe(policy,90,false,null,save);
        Check(Probe(policy,100,true,Epoch.AddSeconds(100),save).Action==RecoveryAction.Healthy,"healthy heartbeat clears consecutive failures");
        Check(Probe(policy,110,false,null,save).Action==RecoveryAction.Wait,"new failure starts fresh probes");
        Check(policy.Evaluate(Epoch.AddSeconds(120),false,false,Epoch,null,save).Action==RecoveryAction.Stopped,"manual stop prevents restart");
        Check(Probe(policy,130,false,null,save).Action==RecoveryAction.Wait,"reenable does not inherit unhealthy probe count");
        policy=new RecoveryPolicy(new RecoveryState());
        Probe(policy,80,false,null,save); Probe(policy,90,false,null,save);
        Check(Probe(policy,100,false,null,delegate {throw new IOException("sensitive diagnostic");}).Action==RecoveryAction.Blocked,"failed persistence prevents process action");
        Check(!policy.Snapshot().Reason.Contains("sensitive"),"storage failure message does not leak raw exception");
        Check(policy.Snapshot().AttemptsUtc.Count==0,"failed reservation consumes no completed attempt");
        policy=new RecoveryPolicy(new RecoveryState());
        Probe(policy,80,false,null,save); Probe(policy,90,false,null,save);
        Check(Probe(policy,100,false,null,null).Action==RecoveryAction.Blocked,"missing durable writer forbids restart");
        policy=new RecoveryPolicy(null);
        Check(Probe(policy,0,false,null,save).Action==RecoveryAction.Blocked,"missing history fails closed");
        policy=new RecoveryPolicy(new RecoveryState {Schema=2});
        Check(Probe(policy,0,false,null,save).Action==RecoveryAction.Blocked,"unknown state schema fails closed");
        policy=new RecoveryPolicy(new RecoveryState {LastObservedUtc=Epoch.AddSeconds(90)});
        Check(Probe(policy,80,false,null,save).Action==RecoveryAction.Blocked,"clock rollback stops recovery");
        policy=new RecoveryPolicy(new RecoveryState());
        Check(Probe(policy,80,true,Epoch.AddSeconds(90),save).Action==RecoveryAction.Blocked,"future heartbeat stops recovery");
        policy=new RecoveryPolicy(new RecoveryState());
        Check(Probe(policy,80,true,Epoch.AddSeconds(-10),save).Action==RecoveryAction.Wait,"previous process heartbeat does not prove current health");
        RecoveryState old=new RecoveryState();
        old.AttemptsUtc.Add(Epoch);
        policy=new RecoveryPolicy(old);old.AttemptsUtc.Clear();
        var copy=policy.Snapshot();copy.AttemptsUtc.Clear();
        Check(policy.Snapshot().AttemptsUtc.Count==1,"state snapshots and constructor are isolated");
        Probe(policy,580,false,null,save); Probe(policy,590,false,null,save);
        Check(Probe(policy,600,false,null,save).Action==RecoveryAction.Restart&&saved.AttemptsUtc.Count==1,"old budget expires at window boundary");
        policy=new RecoveryPolicy(new RecoveryState());
        Probe(policy,80,false,null,save);Probe(policy,90,false,null,save);
        bool calledBeforeAction=false;
        RecoveryDecision decision=Probe(policy,100,false,null,delegate(RecoveryState value){calledBeforeAction=value.AttemptsUtc.Count==1;value.AttemptsUtc.Clear();});
        Check(calledBeforeAction&&decision.Action==RecoveryAction.Restart&&policy.Snapshot().AttemptsUtc.Count==1,"reservation precedes action and writer cannot mutate policy");
        return failures==0?0:1;
    }
}
