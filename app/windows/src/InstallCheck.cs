using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace CrossPet
{
    /// <summary>安装目录是否完整（不依赖界面，tests/windows/LogicTests 测）。
    /// #24：一键更新时安装脚本先删旧目录再复制，钩子正占着 python\python.exe 删不掉，删到一半就停了——
    /// characters、integrations 没了，之后每次启动都崩，用户只看到「打不开」。1.3.7 起安装脚本不再原地删（见 install.ps1），
    /// 这里是兜底：启动前看一眼，缺了就提示，有下载好的安装包就用它修</summary>
    static class InstallCheck
    {
        public static readonly string[] Required = { "characters", "web", "integrations", "tools", "python" };

        /// <summary>安装目录里缺了哪些必需的文件夹（按 Required 的顺序）</summary>
        public static List<string> Missing(string installDir) =>
            Required.Where(d => !Directory.Exists(Path.Combine(installDir, d))).ToList();

        /// <summary>一键更新下载过的安装包（%TEMP%\CrossPet-update\&lt;版本&gt;\CrossPet）里，版本最新、本身完整、带 install.ps1 的那个；没有返回 null</summary>
        public static string RepairPackage(string updateRoot)
        {
            if (!Directory.Exists(updateRoot)) return null;
            return Directory.GetDirectories(updateRoot)
                .Select(v => (ver: Path.GetFileName(v), pkg: Path.Combine(v, "CrossPet")))
                .Where(x => File.Exists(Path.Combine(x.pkg, "install.ps1")) && Missing(x.pkg).Count == 0)
                .OrderByDescending(x => VersionKey(x.ver))
                .Select(x => x.pkg)
                .FirstOrDefault();
        }

        // v1.3.10 → 「0001.0003.0010」：按数字比，不按字母比（1.3.10 比 1.3.9 新）
        static string VersionKey(string tag) =>
            string.Join(".", tag.TrimStart('v', 'V').Split('-')[0].Split('.').Select(n => (int.TryParse(n, out var i) ? i : 0).ToString("D4")));
    }
}
