using System;
using System.IO;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

// One thread owns this lease for the lifetime of the watchdog.
// The installer must provision a restricted directory before opening it.
public sealed class RecoverySession : IDisposable
{
    private readonly string path;
    private readonly Mutex lease;
    private readonly int ownerThread;
    private RecoveryPolicy policy;
    private bool disposed;

    public RecoverySession(string statePath, bool initialize)
    {
        path=Path.GetFullPath(statePath);
        if (!Directory.Exists(Path.GetDirectoryName(path)))
            throw new IOException("恢复记录目录不存在，请先完成安装。");
        string identity;
        using (SHA256 hash=SHA256.Create())
            identity=BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(path.ToUpperInvariant()))).Replace("-","");
        lease=new Mutex(false,"Global\\MulinsenRecovery-"+identity);
        ownerThread=Thread.CurrentThread.ManagedThreadId;
        bool acquired=false;
        try {
            try { acquired=lease.WaitOne(0); } catch (AbandonedMutexException) { acquired=true; }
            if (!acquired) throw new IOException("已有看门狗正在管理此中转，不能重复启动。");
            if (initialize) {
                // Never reset a previous installation, including a missing primary with a backup.
                if (File.Exists(path)||File.Exists(path+".bak"))
                    throw new IOException("恢复记录已存在，不能重新初始化次数。");
                Save(new RecoveryState());
            }
            RecoveryState restored=null;
            try {
                using (FileStream stream=File.OpenRead(path)) {
                    if (stream.Length>65536) throw new IOException();
                    restored=(RecoveryState)new DataContractJsonSerializer(typeof(RecoveryState)).ReadObject(stream);
                }
            } catch { /* Fail closed; never fall back to a backup with an older budget. */ }
            policy=new RecoveryPolicy(restored);
        } catch {
            if (acquired) lease.ReleaseMutex();
            lease.Dispose();
            throw;
        }
    }

    public RecoveryDecision Evaluate(DateTime nowUtc,bool enabled,bool alive,DateTime startedUtc,DateTime? heartbeatUtc)
    {
        RequireOwner();
        return policy.Evaluate(nowUtc,enabled,alive,startedUtc,heartbeatUtc,Save);
    }

    private void Save(RecoveryState state)
    {
        string temporary=path+"."+Guid.NewGuid().ToString("N")+".tmp";
        try {
            using (FileStream stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,
                4096,FileOptions.WriteThrough)) {
                new DataContractJsonSerializer(typeof(RecoveryState)).WriteObject(stream,state);
                stream.Flush(true);
            }
            if (File.Exists(path)) File.Replace(temporary,path,path+".bak");
            else File.Move(temporary,path);
        } finally {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    private void RequireOwner()
    {
        if (Thread.CurrentThread.ManagedThreadId!=ownerThread)
            throw new InvalidOperationException("看门狗必须在同一线程串行执行。");
        if (disposed) throw new ObjectDisposedException("RecoverySession");
    }

    public void Dispose()
    {
        if (disposed) return;
        RequireOwner();
        lease.ReleaseMutex();
        lease.Dispose();
        disposed=true;
    }
}
