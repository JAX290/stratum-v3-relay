using System;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

// Installer and host share this check. Machine DPAPI alone does not isolate local users.
public static class ServiceStoragePermissions
{
    private const string SystemSid="S-1-5-18";
    private const string AdministratorsSid="S-1-5-32-544";
    private const string ServiceSid="S-1-5-19"; // LocalService

    public static DirectorySecurity CreateDirectorySecurity()
    {
        DirectorySecurity security=new DirectorySecurity();
        security.SetOwner(new SecurityIdentifier(AdministratorsSid));
        security.SetAccessRuleProtection(true,false);
        Add(security,SystemSid,FileSystemRights.FullControl);
        Add(security,AdministratorsSid,FileSystemRights.FullControl);
        Add(security,ServiceSid,FileSystemRights.Modify|FileSystemRights.Synchronize);
        return security;
    }
    private static void Add(DirectorySecurity security,string sid,FileSystemRights rights)
    {
        security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),rights,
            InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
    }
    public static bool IsRestricted(FileSystemSecurity security)
    {
        bool system=false,administrators=false,service=false;
        foreach(FileSystemAccessRule rule in security.GetAccessRules(true,true,typeof(SecurityIdentifier))) {
            if(rule.AccessControlType!=AccessControlType.Allow)continue;
            string sid=rule.IdentityReference.Value;
            if(sid!=SystemSid&&sid!=AdministratorsSid&&sid!=ServiceSid)return false;
            if((rule.PropagationFlags&PropagationFlags.InheritOnly)!=0)continue;
            FileSystemRights required=sid==ServiceSid?FileSystemRights.Modify:FileSystemRights.FullControl;
            if((rule.FileSystemRights&required)!=required)continue;
            if(sid==SystemSid)system=true;
            if(sid==AdministratorsSid)administrators=true;
            if(sid==ServiceSid)service=true;
        }
        return system&&administrators&&service;
    }
    public static void VerifyDirectory(string directory)
    {
        DirectoryInfo info=new DirectoryInfo(Path.GetFullPath(directory));
        if(!info.Exists||(info.Attributes&FileAttributes.ReparsePoint)!=0)
            throw new IOException("服务目录不存在或使用了目录链接，请重新检查安装。");
        DirectorySecurity security=info.GetAccessControl();
        if(!security.AreAccessRulesProtected||!HasTrustedOwner(security)||!IsRestricted(security))
            throw new UnauthorizedAccessException("服务目录权限不安全，已停止启动，请使用安装向导修复。");
    }
    public static void VerifyFile(string path)
    {
        FileInfo file=new FileInfo(path);
        if(!file.Exists||(file.Attributes&FileAttributes.ReparsePoint)!=0)
            throw new UnauthorizedAccessException("服务配置文件权限不安全，已停止启动。");
        FileSecurity security=file.GetAccessControl();
        if(!HasTrustedOwner(security)||!IsRestricted(security))
            throw new UnauthorizedAccessException("服务配置文件权限不安全，已停止启动。");
    }
    private static bool HasTrustedOwner(FileSystemSecurity security)
    {
        IdentityReference owner=security.GetOwner(typeof(SecurityIdentifier));
        return owner!=null&&(owner.Value==SystemSid||owner.Value==AdministratorsSid||owner.Value==ServiceSid);
    }
}
