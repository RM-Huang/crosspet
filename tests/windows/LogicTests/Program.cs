using System;
using System.IO;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using CrossPet;

static class Program
{
    static int fails;
    static void Check(string name, bool ok) { Console.WriteLine((ok ? "PASS " : "FAIL ") + name); if (!ok) fails++; }

    static int Main()
    {
        var root = Path.Combine(Path.GetTempPath(), "crosspet-sprite-test", "characters");
        string file, mime;

        Check("普通立绘", SpritePath.TryResolve(root, "/characters/claude/idle.webp", out file, out mime)
            && file == Path.GetFullPath(Path.Combine(root, "claude", "idle.webp")) && mime == "image/webp");
        Check("png", SpritePath.TryResolve(root, "/characters/gpt/edge_left.png", out file, out mime) && mime == "image/png");
        Check("扩展名大写", SpritePath.TryResolve(root, "/characters/gpt/IDLE.WEBP", out file, out mime) && mime == "image/webp");
        Check("中文角色名（网址编码）", SpritePath.TryResolve(root, "/characters/%E5%8F%AF%E8%90%9D/idle.webp", out file, out mime)
            && file == Path.GetFullPath(Path.Combine(root, "可萝", "idle.webp")));
        Check("文件名里有空格（%20）", SpritePath.TryResolve(root, "/characters/my%20pet/idle%201.webp", out file, out mime));

        Check("角色目录是 ..", !SpritePath.TryResolve(root, "/characters/%2E%2E/settings.webp", out file, out mime));
        Check("文件名里编码的斜杠", !SpritePath.TryResolve(root, "/characters/claude/..%2F..%2Fsettings.webp", out file, out mime));
        Check("文件名里编码的反斜杠", !SpritePath.TryResolve(root, "/characters/claude/..%5C..%5Cx.webp", out file, out mime));
        Check("盘符冒号", !SpritePath.TryResolve(root, "/characters/claude/C%3Ax.webp", out file, out mime));
        Check("character.json 不给", !SpritePath.TryResolve(root, "/characters/gpt/character.json", out file, out mime));
        Check("数据目录里别的文件不给", !SpritePath.TryResolve(root, "/settings.json", out file, out mime));
        Check("多一层目录不给", !SpritePath.TryResolve(root, "/characters/a/b/c.webp", out file, out mime));
        Check("空的文件名", !SpritePath.TryResolve(root, "/characters/claude/", out file, out mime));
        Check("结尾是点", !SpritePath.TryResolve(root, "/characters/claude./idle.webp", out file, out mime));
        Check("控制字符", !SpritePath.TryResolve(root, "/characters/claude/a%00.webp", out file, out mime));

        // 藏起来时鼠标穿透（WS_EX_TRANSPARENT = 0x20）：只动这一位，别的样式原样保留
        const int layered = 0x80000, noActivate = 0x08000000, toolWindow = 0x80, transparent = 0x20;
        int ex = layered | noActivate | toolWindow;
        Check("藏起来：加上鼠标穿透", ConcealStyle.ClickThrough(ex, true) == (ex | transparent));
        Check("叫回来：去掉鼠标穿透", ConcealStyle.ClickThrough(ex | transparent, false) == ex);
        Check("重复藏不会出错", ConcealStyle.ClickThrough(ConcealStyle.ClickThrough(ex, true), true) == (ex | transparent));
        Check("没藏时叫回来不变", ConcealStyle.ClickThrough(ex, false) == ex);

        // Gemini（Antigravity）额度排版，样本和 macOS 的 tests/antigravity_quota_probe.sh 共用
        string Agy(string name)
        {
            var path = Path.Combine(AppContext.BaseDirectory, "../../../../../fixtures/antigravity", name + ".json");
            var q = AntigravityQuota.Parse(Js(JsonDocument.Parse(File.ReadAllText(path)).RootElement));
            return q == null ? "null" : q["text"] + " | low=" + q["low"];
        }
        var a1 = Agy("ultra-only-model");
        Check("Gemini：不可用的模型不算进额度（" + a1 + "）", a1.StartsWith("Gemini 剩余 100%") && a1.Contains(" ｜ Claude/GPT 剩余 100% |") && a1.EndsWith("low=False"));
        var a2 = Agy("gemini-used-up");
        Check("Gemini：真用完了还是 0%（" + a2 + "）", a2.StartsWith("Gemini 剩余 0%") && a2.Contains(" ｜ Claude/GPT 剩余 100% |") && a2.EndsWith("low=True"));
        var a3 = Agy("two-pools-with-unavailable");
        Check("Gemini：两个池 + 不可用模型（" + a3 + "）", a3.StartsWith("Gemini 剩余 52%") && a3.Contains(" ｜ Claude/GPT 剩余 99% |") && !a3.Contains(" 0%") && a3.EndsWith("low=False"));

        var s1 = Agy("summary-live");
        Check("Gemini 汇总：两组各一行，98%（" + s1 + "）", s1.StartsWith("Gemini 剩余 100%") && s1.Contains(" ｜ Claude/GPT 剩余 98% |") && s1.EndsWith("low=False"));
        var s2 = Agy("summary-gemini-used-up");
        Check("Gemini 汇总：比例被省略算 0%（" + s2 + "）", s2.StartsWith("Gemini 剩余 0%") && s2.EndsWith("low=True"));
        var s3 = Agy("summary-two-windows");
        Check("Gemini 汇总：两个窗口各一行（" + s3 + "）", s3.StartsWith("Gemini 今日 剩余 40%") && s3.Contains(" ｜ Gemini 本周 剩余 100%") && s3.Contains(" ｜ Claude/GPT 剩余 98% |"));
        Check("Gemini 汇总：空的不显示", Agy("summary-empty") == "null");

        // 网页大小核对（只记日志不修正）：量两次、两次一致且和画布差 2% 以上才算对不上——拖滑块时网页还没排完，量到的是半路的数
        Check("网页大小：对得上不记", !ZoomCheck.Off(260, 362, 260, 362, 260, 362));
        Check("网页大小：差 1% 以内不记（取整误差）", !ZoomCheck.Off(260, 362, 258.5, 360, 258.5, 360));
        Check("网页大小：两次都偏小且一致才记", ZoomCheck.Off(260, 362, 208, 289.6, 208, 289.6));
        Check("网页大小：两次量的不一样（还在变）不记", !ZoomCheck.Off(488, 477, 594, 531, 415, 408));
        Check("网页大小：量到的数不对劲（0 或负）不记", !ZoomCheck.Off(260, 362, 0, 0, 0, 0));
        // 系统缩放被算了两遍（反馈：125%、WebView2 155，网页 224×298 应为 280×372）：按偏差把网页缩放调回来；别的偏差不动
        bool Near(double? v, double want) => v.HasValue && Math.Abs(v.Value - want) < 0.005;
        Check("缩放修正：偏差正好是系统缩放 → 修正 0.8", Near(ZoomCheck.Fix(280, 372, 224, 298, 1.25, 1), 0.8));
        Check("缩放修正：150% 时偏差 1/1.5 → 修正 0.667", Near(ZoomCheck.Fix(280, 372, 186.7, 248, 1.5, 1), 0.667));
        Check("缩放修正：对得上不修", ZoomCheck.Fix(280, 372, 280, 372, 1.25, 1) == null);
        Check("缩放修正：宽高偏得不一样（拖滑块半路）不修", ZoomCheck.Fix(488, 477, 594, 531, 1.25, 1) == null);
        Check("缩放修正：100% 时偏小 0.8 不像算两遍，不修", ZoomCheck.Fix(280, 372, 224, 298, 1.0, 1) == null);
        Check("缩放修正：修过以后又好了（偏大回去）→ 撤销成 1", Near(ZoomCheck.Fix(280, 372, 350, 465, 1.25, 0.8), 1));
        Check("缩放修正：量到的数不对劲不修", ZoomCheck.Fix(280, 372, 0, 0, 1.25, 1) == null);

        // 主线程必须是 STA：WPF 和 WebView2 都要求。1.3.7-test 里 [STAThread] 被新加的 InstallComplete 隔开、落到了它头上，
        // Main 变成 MTA，WebView2 一初始化就报 RPC_E_CHANGED_MODE（「无法在设置线程模式后对其加以更改」），桌宠打不开
        string FindUp(string rel) { for (var d = new DirectoryInfo(AppContext.BaseDirectory); d != null; d = d.Parent) { var f = Path.Combine(d.FullName, rel); if (File.Exists(f)) return f; } return null; }
        var programCs = FindUp(Path.Combine("app", "windows", "src", "Program.cs"));
        Check("启动：找得到 Program.cs", programCs != null);
        if (programCs != null)
            Check("启动：[STAThread] 紧挨着 static void Main", System.Text.RegularExpressions.Regex.IsMatch(File.ReadAllText(programCs), @"\[STAThread\]\s*static\s+void\s+Main\s*\("));

        // 安装是否完整（#24：一键更新删到一半，characters、integrations 没了，之后每次启动都崩）
        var icRoot = Path.Combine(Path.GetTempPath(), "crosspet-installcheck-" + Guid.NewGuid().ToString("N"));
        string Make(string dir, params string[] parts) { Directory.CreateDirectory(dir); foreach (var x in parts) Directory.CreateDirectory(Path.Combine(dir, x)); return dir; }
        var full = Make(Path.Combine(icRoot, "full"), "characters", "web", "integrations", "tools", "python");
        Check("安装检查：齐全的不缺", InstallCheck.Missing(full).Count == 0);
        var half = Make(Path.Combine(icRoot, "half"), "web", "tools", "python");
        Check("安装检查：删到一半的列出缺了哪些", string.Join(",", InstallCheck.Missing(half)) == "characters,integrations");
        Check("安装检查：目录不存在时全缺", InstallCheck.Missing(Path.Combine(icRoot, "nope")).Count == 5);
        var cache = Path.Combine(icRoot, "CrossPet-update");
        Check("修复包：没有缓存时找不到", InstallCheck.RepairPackage(cache) == null);
        var oldPkg = Make(Path.Combine(cache, "v1.3.3", "CrossPet"), "characters", "web", "integrations", "tools", "python");
        File.WriteAllText(Path.Combine(oldPkg, "install.ps1"), "");
        var badPkg = Make(Path.Combine(cache, "v1.3.6", "CrossPet"), "web");   // 下载 / 解压没完成的
        File.WriteAllText(Path.Combine(badPkg, "install.ps1"), "");
        var newPkg = Make(Path.Combine(cache, "v1.3.4", "CrossPet"), "characters", "web", "integrations", "tools", "python");
        File.WriteAllText(Path.Combine(newPkg, "install.ps1"), "");
        Check("修复包：挑版本最新的完整安装包（不完整的跳过）", InstallCheck.RepairPackage(cache) == newPkg);
        try { Directory.Delete(icRoot, true); } catch { }

        Console.WriteLine(fails == 0 ? "全部通过" : $"{fails} 项失败");
        return fails == 0 ? 0 : 1;
    }

    // 把 JSON 转成和 Windows 上 JavaScriptSerializer 一样的形状：对象 → Dictionary，数组 → object[]，整数 → int，小数 → decimal
    static object Js(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject().ToDictionary(p => p.Name, p => Js(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(Js).ToArray(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.TryGetInt32(out var i) ? (object)i : e.GetDecimal(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };
}
