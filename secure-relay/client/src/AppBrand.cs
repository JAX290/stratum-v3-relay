using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Net.NetworkInformation;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Win32;

public static partial class AppBrand
{
    public static Icon LoadIcon()
    {
        Icon icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        return icon ?? SystemIcons.Application;
    }
    public static void Apply(Form form, string suffix)
    {
        form.Text = Title + (suffix ?? "");
        form.Icon = LoadIcon();
    }
}
