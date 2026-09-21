using System;

public sealed class AdminAccessPolicy
{
    private DateTime unlockedUntilUtc=DateTime.MinValue;
    public DateTime UnlockedUntilUtc{get{return unlockedUntilUtc;}}
    public void Unlock(DateTime nowUtc,TimeSpan duration){Validate(nowUtc);if(duration<=TimeSpan.Zero)throw new ArgumentOutOfRangeException("duration");unlockedUntilUtc=nowUtc+duration;}
    public bool CanAccess(DateTime nowUtc){Validate(nowUtc);return nowUtc<unlockedUntilUtc;}
    public void Lock(){unlockedUntilUtc=DateTime.MinValue;}
    private static void Validate(DateTime value){if(value.Kind!=DateTimeKind.Utc)throw new ArgumentException("权限时间必须使用 UTC。");}
}
