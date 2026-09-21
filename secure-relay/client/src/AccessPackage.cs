using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

public sealed class AccessPackageData
{
    public string PackageId="",SiteName="",ClientId="",Address="",ServerName="",CertificateSha256="",SharedKey="";
    public int Port;public long Created,Expires;
}

public static class AccessPackageCodec
{
    private sealed class Envelope{public string format="",id="",salt="",nonce="",ciphertext="",mac="";public long expires;public bool one_time;}
    private sealed class Payload{public string site_name="",client_id="",address="",server_name="",certificate_sha256="",shared_key="";public int port;public long created,expires;}
    public static AccessPackageData Decrypt(string json,string importCode,DateTime nowUtc)
    {
        if(nowUtc.Kind!=DateTimeKind.Utc)throw new ArgumentException("导入时间必须使用 UTC。");string code=NormalizeCode(importCode);JavaScriptSerializer serializer=new JavaScriptSerializer();Envelope envelope;
        try{envelope=serializer.Deserialize<Envelope>(json);}catch{throw new InvalidDataException("接入文件格式不正确。");}
        if(envelope==null||envelope.format!="MSRA1"||!envelope.one_time||!Regex.IsMatch(envelope.id??"","^[a-f0-9]{32}$"))throw new InvalidDataException("接入文件版本或一次性标识不正确。");
        long now=new DateTimeOffset(nowUtc).ToUnixTimeSeconds();if(envelope.expires<=now)throw new InvalidDataException("接入文件已经过期，请在 VPS 重新生成。");
        byte[] salt=Decode(envelope.salt,16),nonce=Decode(envelope.nonce,16),cipher=Decode(envelope.ciphertext,1,65536),expected=Decode(envelope.mac,32);
        byte[] keys=Pbkdf2Sha256(Encoding.ASCII.GetBytes(code),salt,200000,64);byte[] aad=Encoding.ASCII.GetBytes("MSRA1|"+envelope.id+"|"+envelope.expires+"|1");byte[] actual;
        using(HMACSHA256 mac=new HMACSHA256(Slice(keys,32,32)))actual=mac.ComputeHash(Join(aad,salt,nonce,cipher));
        if(!FixedEquals(actual,expected))throw new CryptographicException("导入口令错误或接入文件已被修改。");
        byte[] plain=Xor(cipher,Keystream(Slice(keys,0,32),nonce,cipher.Length));Payload payload;
        try{payload=serializer.Deserialize<Payload>(Encoding.UTF8.GetString(plain));}catch{throw new InvalidDataException("接入文件内容无法读取。");}
        Array.Clear(keys,0,keys.Length);Array.Clear(plain,0,plain.Length);
        if(payload==null||payload.expires!=envelope.expires||payload.created<=0||payload.expires<=payload.created||payload.expires-payload.created>72*3600)throw new InvalidDataException("接入文件有效期不正确。");
        if(String.IsNullOrWhiteSpace(payload.site_name)||String.IsNullOrWhiteSpace(payload.address)||payload.port<1||payload.port>65535||!Regex.IsMatch(payload.certificate_sha256??"","^[A-Fa-f0-9]{64}$")||(payload.shared_key??"").Length<32)throw new InvalidDataException("接入文件缺少必要的 VPS 身份资料。");
        return new AccessPackageData{PackageId=envelope.id,SiteName=payload.site_name,ClientId=payload.client_id,Address=payload.address,Port=payload.port,ServerName=payload.server_name??"",CertificateSha256=payload.certificate_sha256.ToUpperInvariant(),SharedKey=payload.shared_key,Created=payload.created,Expires=payload.expires};
    }
    public static AppConfig Apply(AppConfig current,AccessPackageData package)
    {
        AppConfig result=LastKnownGoodStore.Clone(current??new AppConfig());result.Normalize();if(String.IsNullOrWhiteSpace(result.SiteName))result.SiteName=package.SiteName;
        ServerProfile target=null;foreach(ServerProfile profile in result.Servers)if(profile!=null&&String.Equals(profile.Address,package.Address,StringComparison.OrdinalIgnoreCase)&&profile.Port==package.Port){target=profile;break;}
        if(target==null)foreach(ServerProfile profile in result.Servers)if(profile!=null&&(!profile.Enabled||String.IsNullOrWhiteSpace(profile.Address))){target=profile;break;}
        if(target==null){target=new ServerProfile{Name="备用VPS "+result.Servers.Count};result.Servers.Add(target);}target.Enabled=true;target.Address=package.Address;target.Port=package.Port;target.ServerName=package.ServerName;target.CertificateSha256=package.CertificateSha256;target.SharedKey=package.SharedKey;if(String.IsNullOrWhiteSpace(target.Name))target.Name=result.Servers.IndexOf(target)==0?"主VPS":"备用VPS "+result.Servers.IndexOf(target);
        return result;
    }
    public static string CreateForTest(AccessPackageData data,string code,string id,byte[] salt,byte[] nonce)
    {
        JavaScriptSerializer s=new JavaScriptSerializer();Payload payload=new Payload{site_name=data.SiteName,client_id=data.ClientId,address=data.Address,port=data.Port,server_name=data.ServerName,certificate_sha256=data.CertificateSha256,shared_key=data.SharedKey,created=data.Created,expires=data.Expires};byte[] plain=Encoding.UTF8.GetBytes(s.Serialize(payload));byte[] keys=Pbkdf2Sha256(Encoding.ASCII.GetBytes(NormalizeCode(code)),salt,200000,64);byte[] cipher=Xor(plain,Keystream(Slice(keys,0,32),nonce,plain.Length));byte[] aad=Encoding.ASCII.GetBytes("MSRA1|"+id+"|"+data.Expires+"|1");byte[] digest;using(HMACSHA256 mac=new HMACSHA256(Slice(keys,32,32)))digest=mac.ComputeHash(Join(aad,salt,nonce,cipher));return s.Serialize(new Envelope{format="MSRA1",id=id,expires=data.Expires,one_time=true,salt=Convert.ToBase64String(salt),nonce=Convert.ToBase64String(nonce),ciphertext=Convert.ToBase64String(cipher),mac=Convert.ToBase64String(digest)});
    }
    private static string NormalizeCode(string value){string code=Regex.Replace(value??"","[-\\s]","").ToUpperInvariant();if(!Regex.IsMatch(code,"^[A-F0-9]{24}$"))throw new InvalidDataException("导入口令应为 4 组字符。");return code;}
    internal static byte[] Decode(string value,int size){byte[] data;try{data=Convert.FromBase64String(value??"");}catch{throw new InvalidDataException("接入文件编码不正确。");}if(data.Length!=size)throw new InvalidDataException("接入文件字段长度不正确。");return data;}
    internal static byte[] Decode(string value,int min,int max){byte[] data;try{data=Convert.FromBase64String(value??"");}catch{throw new InvalidDataException("接入文件编码不正确。");}if(data.Length<min||data.Length>max)throw new InvalidDataException("接入文件内容长度不正确。");return data;}
    internal static byte[] Pbkdf2Sha256(byte[] password,byte[] salt,int iterations,int length){byte[] output=new byte[length];using(HMACSHA256 hmac=new HMACSHA256(password)){int offset=0,block=1;while(offset<length){byte[] counter={ (byte)(block>>24),(byte)(block>>16),(byte)(block>>8),(byte)block};byte[] u=hmac.ComputeHash(Join(salt,counter));byte[] value=(byte[])u.Clone();for(int round=1;round<iterations;round++){u=hmac.ComputeHash(u);for(int i=0;i<value.Length;i++)value[i]^=u[i];}int count=Math.Min(value.Length,length-offset);Buffer.BlockCopy(value,0,output,offset,count);offset+=count;block++;}}return output;}
    internal static byte[] Keystream(byte[] key,byte[] nonce,int length){byte[] output=new byte[length];using(HMACSHA256 hmac=new HMACSHA256(key)){int offset=0,counter=1;while(offset<length){byte[] block=hmac.ComputeHash(Join(nonce,new[]{(byte)(counter>>24),(byte)(counter>>16),(byte)(counter>>8),(byte)counter}));int count=Math.Min(block.Length,length-offset);Buffer.BlockCopy(block,0,output,offset,count);offset+=count;counter++;}}return output;}
    internal static byte[] Xor(byte[] left,byte[] right){byte[] output=new byte[left.Length];for(int i=0;i<left.Length;i++)output[i]=(byte)(left[i]^right[i]);return output;}
    internal static byte[] Slice(byte[] value,int offset,int count){byte[] output=new byte[count];Buffer.BlockCopy(value,offset,output,0,count);return output;}
    internal static byte[] Join(params byte[][] values){int size=0;foreach(byte[] value in values)size+=value.Length;byte[] output=new byte[size];int offset=0;foreach(byte[] value in values){Buffer.BlockCopy(value,0,output,offset,value.Length);offset+=value.Length;}return output;}
    internal static bool FixedEquals(byte[] left,byte[] right){if(left.Length!=right.Length)return false;int difference=0;for(int i=0;i<left.Length;i++)difference|=left[i]^right[i];return difference==0;}
}

public sealed class UsedAccessPackageStore
{
    private readonly string path;public UsedAccessPackageStore(string folder){path=Path.Combine(folder,"used-access-packages.txt");}
    public bool IsUsed(string id){if(!File.Exists(path))return false;foreach(string line in File.ReadAllLines(path))if(String.Equals(line.Trim(),id,StringComparison.OrdinalIgnoreCase))return true;return false;}
    public void MarkUsed(string id){if(!Regex.IsMatch(id??"","^[a-f0-9]{32}$"))throw new InvalidDataException("接入文件编号不正确。");Directory.CreateDirectory(Path.GetDirectoryName(path));if(IsUsed(id))throw new InvalidOperationException("这个一次性接入文件已经导入过。");string temporary=path+".tmp";List<string> lines=File.Exists(path)?new List<string>(File.ReadAllLines(path)):new List<string>();lines.Add(id);File.WriteAllLines(temporary,lines.ToArray(),Encoding.ASCII);if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);}
}
