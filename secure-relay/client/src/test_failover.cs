using System;

public static class FailoverPolicyTests
{
    private static int failures;
    private static void Check(bool condition, string name) { if (condition) Console.WriteLine("PASS " + name); else { Console.WriteLine("FAIL " + name); failures++; } }

    public static int Main()
    {
        DateTime start = new DateTime(2026, 9, 21, 0, 0, 0, DateTimeKind.Utc);
        FailoverPolicy policy = new FailoverPolicy(3, 2, TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(30));
        Check(policy.TrySelect("主VPS", true, start) && policy.CurrentEndpoint == "主VPS", "primary is selected initially");
        policy.ReportFailure("主VPS", start.AddSeconds(1));
        policy.ReportFailure("主VPS", start.AddSeconds(2));
        Check(policy.CanAttempt("主VPS", start.AddSeconds(2)), "two failures do not switch a line");
        FailoverEndpointPolicyState third = policy.ReportFailure("主VPS", start.AddSeconds(3));
        Check(!policy.CanAttempt("主VPS", start.AddSeconds(30)) && third.ConsecutiveFailures == 3, "third consecutive failure starts endpoint cooldown");
        Check(policy.TrySelect("备用VPS 1", false, start.AddSeconds(4)), "unavailable current line permits immediate backup switch");
        Check(policy.CanAttempt("主VPS", start.AddSeconds(63)), "endpoint can be probed at cooldown boundary");
        policy.ReportSuccess("主VPS", true, start.AddSeconds(63));
        Check(!policy.TrySelect("主VPS", true, start.AddSeconds(64)), "one primary success remains in recovery observation");
        policy.ReportSuccess("主VPS", true, start.AddSeconds(92));
        Check(policy.Snapshot("主VPS").RequiresRecoveryObservation, "recovery observation enforces minimum duration");
        policy.ReportSuccess("主VPS", true, start.AddSeconds(93));
        Check(!policy.TrySelect("主VPS", true, start.AddSeconds(93)), "switch cooldown prevents an early return to primary");
        Check(policy.TrySelect("主VPS", true, start.AddSeconds(124)), "stable primary returns after switch cooldown");
        policy.ReportFailure("备用VPS 1", start.AddSeconds(130));
        policy.ReportSuccess("备用VPS 1", false, start.AddSeconds(131));
        Check(policy.Snapshot("备用VPS 1").ConsecutiveFailures == 0, "successful backup probe clears failure streak");
        FailoverEndpointPolicyState copy = policy.Snapshot("主VPS"); copy.ConsecutiveFailures = 99;
        Check(policy.Snapshot("主VPS").ConsecutiveFailures == 0, "policy snapshots are isolated");
        CheckController(start);
        return failures == 0 ? 0 : 1;
    }

    private static void CheckController(DateTime start)
    {
        const string key="0123456789abcdef0123456789abcdef";
        ServerProfile primary=new ServerProfile{Name="主VPS",Enabled=true,Address="primary.example",ServerName="primary.example",SharedKey=key};
        ServerProfile backup=new ServerProfile{Name="备用VPS 1",Enabled=true,Address="backup.example",ServerName="backup.example",SharedKey=key};
        ServerProfile disabled=new ServerProfile{Name="备用VPS 2",Enabled=false};
        ServerProfile[] profiles=new[]{primary,backup,disabled};
        RelayFailoverController controller=new RelayFailoverController();controller.Reset(profiles,start);
        var candidates=controller.ConnectionCandidates(profiles,start);
        Check(candidates.Count==1&&Object.ReferenceEquals(candidates[0],primary),"controller initially routes only to primary");
        controller.ConnectionFailed(primary,start.AddSeconds(1));controller.ConnectionFailed(primary,start.AddSeconds(2));
        Check(controller.Snapshot(primary.Name).ConsecutiveFailures==1,"simultaneous miner failures count as one observation");
        controller.ConnectionFailed(primary,start.AddSeconds(6));
        candidates=controller.ConnectionCandidates(profiles,start.AddSeconds(6));
        Check(candidates.Count==1&&Object.ReferenceEquals(candidates[0],primary),"controller keeps traffic on primary before failure threshold");
        controller.ConnectionFailed(primary,start.AddSeconds(11));
        candidates=controller.ConnectionCandidates(profiles,start.AddSeconds(12));
        Check(candidates.Count==1&&Object.ReferenceEquals(candidates[0],backup)&&controller.ConnectionSucceeded(backup,false,start.AddSeconds(12)),"controller selects backup after primary threshold");
        candidates=controller.ConnectionCandidates(profiles,start.AddSeconds(13));
        Check(candidates.Count==1&&Object.ReferenceEquals(candidates[0],backup),"controller keeps new connections on selected backup");
        controller.HealthSucceeded(primary,true,start.AddSeconds(71));controller.HealthSucceeded(primary,true,start.AddSeconds(101));
        Check(controller.CurrentEndpoint=="备用VPS 1","healthy primary waits for switch cooldown");
        controller.HealthSucceeded(primary,true,start.AddSeconds(132));
        Check(controller.CurrentEndpoint=="主VPS","health probes return traffic to stable primary");
    }
}
