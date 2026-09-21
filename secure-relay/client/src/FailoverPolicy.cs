using System;
using System.Collections.Generic;

public sealed class FailoverEndpointPolicyState
{
    public string Name = "";
    public int ConsecutiveFailures;
    public int ConsecutiveSuccesses;
    public DateTime CooldownUntilUtc;
    public DateTime RecoverySinceUtc;
    public bool RequiresRecoveryObservation;
    public FailoverEndpointPolicyState Copy() { return (FailoverEndpointPolicyState)MemberwiseClone(); }
}

public sealed class FailoverPolicy
{
    private readonly int failureThreshold;
    private readonly int recoverySuccessThreshold;
    private readonly TimeSpan endpointCooldown;
    private readonly TimeSpan switchCooldown;
    private readonly TimeSpan recoveryObservation;
    private readonly Dictionary<string, FailoverEndpointPolicyState> states = new Dictionary<string, FailoverEndpointPolicyState>(StringComparer.OrdinalIgnoreCase);
    private string currentEndpoint = "";
    private DateTime lastSwitchUtc = DateTime.MinValue;

    public string CurrentEndpoint { get { return currentEndpoint; } }

    public FailoverPolicy() : this(3, 2, TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(30)) { }

    public FailoverPolicy(int failures, int recoverySuccesses, TimeSpan endpointRetryCooldown,
        TimeSpan endpointSwitchCooldown, TimeSpan primaryRecoveryObservation)
    {
        if (failures < 1) throw new ArgumentOutOfRangeException("failures");
        if (recoverySuccesses < 1) throw new ArgumentOutOfRangeException("recoverySuccesses");
        if (endpointRetryCooldown < TimeSpan.Zero) throw new ArgumentOutOfRangeException("endpointRetryCooldown");
        if (endpointSwitchCooldown < TimeSpan.Zero) throw new ArgumentOutOfRangeException("endpointSwitchCooldown");
        if (primaryRecoveryObservation < TimeSpan.Zero) throw new ArgumentOutOfRangeException("primaryRecoveryObservation");
        failureThreshold = failures;
        recoverySuccessThreshold = recoverySuccesses;
        endpointCooldown = endpointRetryCooldown;
        switchCooldown = endpointSwitchCooldown;
        recoveryObservation = primaryRecoveryObservation;
    }

    public FailoverEndpointPolicyState ReportFailure(string name, DateTime nowUtc)
    {
        ValidateInput(name, nowUtc);
        FailoverEndpointPolicyState state = Get(name);
        state.ConsecutiveFailures++;
        state.ConsecutiveSuccesses = 0;
        state.RecoverySinceUtc = DateTime.MinValue;
        if (state.ConsecutiveFailures >= failureThreshold) {
            state.CooldownUntilUtc = nowUtc + endpointCooldown;
            state.RequiresRecoveryObservation = true;
        }
        return state.Copy();
    }

    public FailoverEndpointPolicyState ReportSuccess(string name, bool primary, DateTime nowUtc)
    {
        ValidateInput(name, nowUtc);
        FailoverEndpointPolicyState state = Get(name);
        state.ConsecutiveFailures = 0;
        state.ConsecutiveSuccesses++;
        if (primary && state.RequiresRecoveryObservation) {
            if (state.RecoverySinceUtc == DateTime.MinValue) state.RecoverySinceUtc = nowUtc;
            if (state.ConsecutiveSuccesses >= recoverySuccessThreshold && nowUtc - state.RecoverySinceUtc >= recoveryObservation) {
                state.RequiresRecoveryObservation = false;
                state.CooldownUntilUtc = DateTime.MinValue;
            }
        } else {
            state.RequiresRecoveryObservation = false;
            state.RecoverySinceUtc = DateTime.MinValue;
            state.CooldownUntilUtc = DateTime.MinValue;
        }
        return state.Copy();
    }

    public bool CanAttempt(string name, DateTime nowUtc)
    {
        ValidateInput(name, nowUtc);
        return nowUtc >= Get(name).CooldownUntilUtc;
    }

    public bool TrySelect(string name, bool primary, DateTime nowUtc)
    {
        if (!CanSelect(name, primary, nowUtc)) return false;
        if (String.Equals(currentEndpoint, name, StringComparison.OrdinalIgnoreCase)) return true;
        currentEndpoint = name;
        lastSwitchUtc = nowUtc;
        return true;
    }

    public void ForceSelectVerified(string name,bool primary,DateTime nowUtc)
    {
        ValidateInput(name,nowUtc);ReportSuccess(name,primary,nowUtc);FailoverEndpointPolicyState state=Get(name);
        state.RequiresRecoveryObservation=false;state.RecoverySinceUtc=DateTime.MinValue;state.CooldownUntilUtc=DateTime.MinValue;
        currentEndpoint=name;lastSwitchUtc=nowUtc;
    }

    public bool CanSelect(string name, bool primary, DateTime nowUtc)
    {
        ValidateInput(name, nowUtc);
        FailoverEndpointPolicyState candidate = Get(name);
        if (nowUtc < candidate.CooldownUntilUtc || (primary && candidate.RequiresRecoveryObservation)) return false;
        if (String.Equals(currentEndpoint, name, StringComparison.OrdinalIgnoreCase)) return true;
        if (currentEndpoint.Length == 0) return true;
        FailoverEndpointPolicyState current = Get(currentEndpoint);
        bool currentUnavailable = nowUtc < current.CooldownUntilUtc;
        return currentUnavailable || nowUtc - lastSwitchUtc >= switchCooldown;
    }

    public FailoverEndpointPolicyState Snapshot(string name)
    {
        if (String.IsNullOrWhiteSpace(name)) throw new ArgumentException("线路名称不能为空。", "name");
        return Get(name).Copy();
    }

    private FailoverEndpointPolicyState Get(string name)
    {
        FailoverEndpointPolicyState state;
        if (!states.TryGetValue(name, out state)) {
            state = new FailoverEndpointPolicyState { Name = name };
            states[name] = state;
        }
        return state;
    }

    private static void ValidateInput(string name, DateTime nowUtc)
    {
        if (String.IsNullOrWhiteSpace(name)) throw new ArgumentException("线路名称不能为空。", "name");
        if (nowUtc.Kind != DateTimeKind.Utc) throw new ArgumentException("策略时间必须使用 UTC。", "nowUtc");
    }
}
