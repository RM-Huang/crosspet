using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace CrossPet
{
    static class Program
    {
        const string WebView2Download = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";

        /// WPF 渲染线程挂了（0x88980406 UCEERR_RENDERTHREADFAILURE）：这之后窗口不会再重画，当作没事继续跑就是画面冻住
        static bool IsRenderThreadFailure(Exception e)
        {
            for (var x = e; x != null; x = x.InnerException)
                if ((uint)x.HResult == 0x88980406 || (x.Message ?? "").Contains("UCEERR_RENDERTHREADFAILURE")) return true;
            return false;
        }

        /// 自动重启一次（位置、角色、设置都在设置文件里，重开就回来了）。2 分钟内最多一次，免得反复重启
        static void RestartAfterRenderFailure()
        {
            var mark = Path.Combine(Store.Data, "render-restart.txt");
            try
            {
                if (File.Exists(mark) && (DateTime.UtcNow - File.GetLastWriteTimeUtc(mark)).TotalMinutes < 2) { Store.Log("渲染线程又挂了，2 分钟内已经重启过一次，不再自动重启"); return; }
                File.WriteAllText(mark, DateTime.Now.ToString("s"));
                Store.Log("渲染线程挂了，自动重启 CrossPet");
                // 等这个进程退出（单实例锁放开）再开新的：交给 cmd 等 2 秒
                var exe = Path.Combine(Store.Install, "CrossPet.exe");
                Process.Start(new ProcessStartInfo("cmd.exe", $"/d /c ping -n 3 127.0.0.1 >nul & start \"\" \"{exe}\"") { UseShellExecute = false, CreateNoWindow = true });
            }
            catch (Exception e) { Store.Log("自动重启失败: " + e.Message); return; }
            Environment.Exit(0);
        }

        /// <summary>安装目录缺东西（#24：上次一键更新删到一半）时不往下走——不然每次启动都崩、用户只看到「打不开」。
        /// 找得到下载好的完整安装包就问要不要用它修复，找不到就指去发布页重新下载</summary>
        static bool InstallComplete()
        {
            var missing = InstallCheck.Missing(Store.Install);
            if (missing.Count == 0) return true;
            Store.Log("安装不完整，缺少：" + string.Join("、", missing));
            var pkg = InstallCheck.RepairPackage(Path.Combine(Path.GetTempPath(), "CrossPet-update"));
            var what = $"CrossPet 的安装不完整（缺少 {string.Join("、", missing)}），多半是上次更新没装完。";
            if (pkg != null)
            {
                var ver = Path.GetFileName(Path.GetDirectoryName(pkg));
                if (MessageBox.Show($"{what}\n\n找到了已经下载好的安装包（{ver}），要现在用它修复吗？设置、角色和 AI 接入都会保留。",
                        "CrossPet", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes)
                {
                    Store.Log("用下载好的安装包修复：" + pkg);
                    Process.Start(new ProcessStartInfo("powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -File \"{Path.Combine(pkg, "install.ps1")}\"") { UseShellExecute = false });
                }
                return false;
            }
            if (MessageBox.Show($"{what}\n\n请重新下载安装（设置、角色和 AI 接入都会保留）。要现在打开下载页吗？",
                    "CrossPet", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes)
                Process.Start(new ProcessStartInfo("https://github.com/lokicorvus/crosspet/releases/latest") { UseShellExecute = true });
            return false;
        }

        // [STAThread] 必须紧挨着 Main：WPF 和 WebView2 都要求主线程是 STA（中间插了别的函数，Main 就成了 MTA，启动报 RPC_E_CHANGED_MODE）
        [STAThread]
        static void Main()
        {
            // 只开一个：再次打开时让已经在跑的那个回到右下角
            using (var mutex = new Mutex(true, @"Local\CrossPet-" + Environment.UserName, out var first))
            using (var show = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\CrossPet-Show-" + Environment.UserName))
            {
                if (!first) { show.Set(); return; }

                Directory.CreateDirectory(Store.Data);
                try { File.Delete(Pet.UserQuitFlag); } catch { }   // 自己打开了：恢复跟着 AI 出现
                try { Directory.CreateDirectory(Store.State); File.WriteAllText(Path.Combine(Store.State, "pet.pid"), Process.GetCurrentProcess().Id.ToString()); } catch { }   // DSH 插件看它判断桌宠在不在跑
                AppDomain.CurrentDomain.UnhandledException += (_, e) => Store.Log("崩溃: " + e.ExceptionObject);
                Store.Log($"启动 {Store.Version}，{Environment.OSVersion}，64 位进程 = {Environment.Is64BitProcess}");
                if (!InstallComplete()) return;
                System.Windows.Forms.Application.EnableVisualStyles();

                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.DispatcherUnhandledException += (_, e) =>
                {
                    Store.Log("界面异常: " + e.Exception);
                    e.Handled = true;
                    if (IsRenderThreadFailure(e.Exception)) RestartAfterRenderFailure();
                };

                // WebView2 运行时：Windows 11 自带；老的 Windows 10 可能没有，指引去微软官网装
                string version;
                try { version = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
                catch { version = null; }
                if (string.IsNullOrEmpty(version))
                {
                    if (MessageBox.Show("CrossPet 需要系统组件「Microsoft Edge WebView2 运行时」（Windows 11 自带）。\n\n要现在打开微软官网下载吗？",
                        "CrossPet", MessageBoxButton.YesNo, MessageBoxImage.Information) == MessageBoxResult.Yes)
                        Process.Start(new ProcessStartInfo(WebView2Download) { UseShellExecute = true });
                    return;
                }
                Store.Log("WebView2 运行时 " + version);
                Store.LoadSettings();

                app.Startup += async (_, __) =>
                {
                    try
                    {
                        var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Store.Data, "webview2"));
                        var pet = new Pet(env);
                        pet.Start();
                        new Thread(() =>
                        {
                            while (show.WaitOne()) app.Dispatcher.BeginInvoke(new Action(pet.Recenter));
                        }) { IsBackground = true }.Start();
                    }
                    catch (Exception e)
                    {
                        Store.Log("启动失败: " + e);
                        MessageBox.Show("CrossPet 启动失败：\n" + e.Message, "CrossPet");
                        app.Shutdown();
                    }
                };
                app.Run();
                try { File.Delete(Path.Combine(Store.State, "pet.pid")); } catch { }
            }
        }
    }
}
