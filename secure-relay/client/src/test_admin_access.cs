using System;

public static class AdminAccessTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        DateTime now=new DateTime(2026,9,22,0,0,0,DateTimeKind.Utc);AdminAccessPolicy policy=new AdminAccessPolicy();
        Check(!policy.CanAccess(now),"operator mode is the default");policy.Unlock(now,TimeSpan.FromMinutes(15));
        Check(policy.CanAccess(now.AddMinutes(14)),"administrator session remains active inside its window");
        Check(!policy.CanAccess(now.AddMinutes(15)),"administrator session expires at the boundary");
        policy.Unlock(now,TimeSpan.FromMinutes(15));policy.Lock();Check(!policy.CanAccess(now),"manual lock returns to operator mode");
        bool rejected=false;try{policy.Unlock(DateTime.Now,TimeSpan.FromMinutes(1));}catch(ArgumentException){rejected=true;}Check(rejected,"local time cannot bypass expiry rules");
        return failures==0?0:1;
    }
}
