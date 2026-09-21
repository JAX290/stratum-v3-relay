using System;
using System.Collections.Generic;

public sealed class RelayFailoverController
{
    private readonly object gate = new object();
    private FailoverPolicy policy = new FailoverPolicy();
    private readonly Dictionary<string, DateTime> lastConnectionFailures = new Dictionary<string, DateTime>(StringComparer.OrdinalIgnoreCase);
    private static readonly TimeSpan ConnectionFailureSpacing = TimeSpan.FromSeconds(5);

    public string CurrentEndpoint { get { lock (gate) return policy.CurrentEndpoint; } }

    public void Reset(IList<ServerProfile> profiles, DateTime nowUtc)
    {
        if (profiles == null) throw new ArgumentNullException("profiles");
        lock (gate) {
            policy = new FailoverPolicy();
            lastConnectionFailures.Clear();
            for (int index = 0; index < profiles.Count; index++) {
                ServerProfile profile = profiles[index];
                if (profile == null || !profile.Enabled || String.IsNullOrWhiteSpace(profile.Address)) continue;
                policy.TrySelect(profile.Name, index == 0, nowUtc);
                break;
            }
        }
    }

    public List<ServerProfile> ConnectionCandidates(IList<ServerProfile> profiles, DateTime nowUtc)
    {
        if (profiles == null) throw new ArgumentNullException("profiles");
        lock (gate) {
            List<ServerProfile> result = new List<ServerProfile>();
            string current = policy.CurrentEndpoint;
            for (int pass = 0; pass < 2; pass++) {
                for (int index = 0; index < profiles.Count; index++) {
                    ServerProfile profile = profiles[index];
                    if (profile == null || !profile.Enabled || String.IsNullOrWhiteSpace(profile.Address)) continue;
                    bool isCurrent = String.Equals(profile.Name, current, StringComparison.OrdinalIgnoreCase);
                    if ((pass == 0) != isCurrent) continue;
                    if (policy.CanAttempt(profile.Name, nowUtc) && policy.CanSelect(profile.Name, index == 0, nowUtc)) result.Add(profile);
                }
            }
            return result;
        }
    }

    public bool ConnectionSucceeded(ServerProfile profile, bool primary, DateTime nowUtc)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        lock (gate) {
            policy.ReportSuccess(profile.Name, primary, nowUtc);
            return policy.TrySelect(profile.Name, primary, nowUtc);
        }
    }

    public void ConnectionFailed(ServerProfile profile, DateTime nowUtc)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        lock (gate) {
            DateTime previous;
            if (lastConnectionFailures.TryGetValue(profile.Name, out previous) && nowUtc - previous < ConnectionFailureSpacing) return;
            lastConnectionFailures[profile.Name] = nowUtc;
            policy.ReportFailure(profile.Name, nowUtc);
        }
    }

    public bool ShouldProbe(ServerProfile profile, DateTime nowUtc)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        lock (gate) return policy.CanAttempt(profile.Name, nowUtc);
    }

    public void HealthSucceeded(ServerProfile profile, bool primary, DateTime nowUtc)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        lock (gate) {
            policy.ReportSuccess(profile.Name, primary, nowUtc);
            if (primary) policy.TrySelect(profile.Name, true, nowUtc);
        }
    }

    public void HealthFailed(ServerProfile profile, DateTime nowUtc)
    {
        if (profile == null) throw new ArgumentNullException("profile");
        lock (gate) policy.ReportFailure(profile.Name, nowUtc);
    }

    public FailoverEndpointPolicyState Snapshot(string name)
    {
        lock (gate) return policy.Snapshot(name);
    }
}
