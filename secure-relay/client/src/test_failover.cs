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
        return failures == 0 ? 0 : 1;
    }
}
