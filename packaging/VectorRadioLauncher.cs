using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Vector Radio")]
[assembly: AssemblyDescription("Launcher for Vector Radio")]
[assembly: AssemblyCompany("Vector Radio")]
[assembly: AssemblyProduct("Vector Radio")]
[assembly: AssemblyVersion("1.0.2.0")]
[assembly: AssemblyFileVersion("1.0.2.0")]

internal static class VectorRadioLauncher
{
    [STAThread]
    private static void Main()
    {
        var appDirectory = AppDomain.CurrentDomain.BaseDirectory;
        var python = Path.Combine(appDirectory, "runtime", "pythonw.exe");
        var entryPoint = Path.Combine(appDirectory, "main.py");

        if (!File.Exists(python) || !File.Exists(entryPoint))
        {
            MessageBox.Show(
                "Не знайдено приватний Python або main.py. Перевстановіть Vector Radio.",
                "Vector Radio",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                Arguments = Quote(entryPoint),
                WorkingDirectory = appDirectory,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            startInfo.EnvironmentVariables["PYTHONUTF8"] = "1";
            startInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Process.Start(startInfo);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                "Не вдалося запустити Vector Radio.\n\n" + exception.Message,
                "Vector Radio",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
