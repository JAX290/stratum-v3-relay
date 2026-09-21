using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
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
using System.Web.Script.Serialization;
using Microsoft.Win32;

public static class NetworkHelper
{
    public static string GetLanIPv4()
    {
        string best = "";
        int bestScore = -1;
        try {
            foreach (NetworkInterface adapter in NetworkInterface.GetAllNetworkInterfaces()) {
                if (adapter.OperationalStatus != OperationalStatus.Up || adapter.NetworkInterfaceType == NetworkInterfaceType.Loopback || adapter.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
                string identity = (adapter.Name + " " + adapter.Description).ToLowerInvariant();
                if (identity.Contains("tailscale") || identity.Contains("vmware") || identity.Contains("virtual") || identity.Contains("hyper-v") || identity.Contains("clash") || identity.Contains("tap") || identity.Contains("vpn")) continue;
                IPInterfaceProperties properties = adapter.GetIPProperties();
                bool hasGateway = false;
                foreach (GatewayIPAddressInformation gateway in properties.GatewayAddresses) {
                    if (gateway.Address.AddressFamily == AddressFamily.InterNetwork && !gateway.Address.Equals(IPAddress.Any)) { hasGateway = true; break; }
                }
                if (!hasGateway) continue;
                foreach (UnicastIPAddressInformation address in properties.UnicastAddresses) {
                    if (address.Address.AddressFamily != AddressFamily.InterNetwork || !IsPrivate(address.Address)) continue;
                    int score = 100;
                    if (adapter.NetworkInterfaceType == NetworkInterfaceType.Wireless80211) score += 20;
                    if (adapter.NetworkInterfaceType == NetworkInterfaceType.Ethernet || adapter.NetworkInterfaceType == NetworkInterfaceType.GigabitEthernet) score += 15;
                    if (score > bestScore) { best = address.Address.ToString(); bestScore = score; }
                }
            }
        } catch { }
        return best;
    }

    private static bool IsPrivate(IPAddress address)
    {
        byte[] value = address.GetAddressBytes();
        return value.Length == 4 && (value[0] == 10 || (value[0] == 172 && value[1] >= 16 && value[1] <= 31) || (value[0] == 192 && value[1] == 168));
    }
}
