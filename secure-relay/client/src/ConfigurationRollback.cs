using System;

public sealed class ConfigurationRollbackResult
{
    public bool Restored { get; private set; }
    public bool AlreadyCurrent { get; private set; }
    public AppConfig Config { get; private set; }
    public DateTime ValidatedUtc { get; private set; }

    internal ConfigurationRollbackResult(bool restored, bool alreadyCurrent, AppConfig config, DateTime validatedUtc)
    {
        Restored = restored;
        AlreadyCurrent = alreadyCurrent;
        Config = config;
        ValidatedUtc = validatedUtc;
    }
}

public sealed class ConfigurationRollbackCoordinator
{
    public ConfigurationRollbackResult RestoreIfDifferent(AppConfig failedConfig,
        LastKnownGoodStore store, Action<AppConfig> persist)
    {
        if (failedConfig == null) throw new ArgumentNullException("failedConfig");
        if (store == null) throw new ArgumentNullException("store");
        if (persist == null) throw new ArgumentNullException("persist");
        LastKnownGoodSnapshot snapshot = store.Load();
        string failedFingerprint = LastKnownGoodStore.ComputeFingerprint(failedConfig);
        if (String.Equals(failedFingerprint, snapshot.ConfigurationFingerprint, StringComparison.Ordinal))
            return new ConfigurationRollbackResult(false, true, snapshot.Config, snapshot.ValidatedUtc);
        persist(snapshot.Config);
        return new ConfigurationRollbackResult(true, false, snapshot.Config, snapshot.ValidatedUtc);
    }
}
