"""Reviewed source compiled into the standalone GUI test executable."""

SOURCE = r"""
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class GuiTest
{
    static T Place<T>(Form form, T control, string text, int x, int y, int width, int height) where T : Control
    {
        control.Text = text;
        control.SetBounds(x, y, width, height);
        form.Controls.Add(control);
        return control;
    }

    static Button Button(Form form, string text, int x)
    {
        return Place(form, new Button { FlatStyle = FlatStyle.System }, text, x, 105, 144, 34);
    }

    static void Save(Bitmap bitmap, string name)
    {
        using (var stream = new FileStream(name + ".tmp", FileMode.CreateNew, FileAccess.Write, FileShare.None))
            bitmap.Save(stream, ImageFormat.Png);
        File.Move(name + ".tmp", name);
    }

    static void Capture(Form form, string[] titles, int index)
    {
        using (var original = new Bitmap(form.Width, form.Height))
        {
            form.DrawToBitmap(original, new Rectangle(Point.Empty, original.Size));
            Save(original, "scene-" + (index + 1) + ".png");
            using (var report = new Bitmap(880, 610))
            {
                using (var graphics = Graphics.FromImage(report))
                {
                    graphics.Clear(Color.White);
                    graphics.DrawString(titles[index], form.Font, Brushes.MidnightBlue, 16, 12);
                    graphics.DrawImage(original, new Rectangle(17, 44, 846, 549),
                        new Rectangle(12, 40, 564, 366), GraphicsUnit.Pixel);
                    graphics.DrawRectangle(Pens.RoyalBlue, 37, 144, 804, 42);
                }
                Save(report, "report-" + (index + 1) + ".png");
            }
            if (index != 2) return;
            using (var stream = new MemoryStream(File.ReadAllBytes("scene-1.png"), false))
            using (var first = Image.FromStream(stream))
            using (var comparison = new Bitmap(1200, 480))
            {
                using (var graphics = Graphics.FromImage(comparison))
                {
                    graphics.Clear(Color.White);
                    graphics.DrawString(titles[0], form.Font, Brushes.MidnightBlue, 20, 16);
                    graphics.DrawString(titles[index], form.Font, Brushes.MidnightBlue, 620, 16);
                    graphics.DrawImage(first, 20, 52, 560, 560 * first.Height / first.Width);
                    graphics.DrawImage(original, 620, 52, 560, 560 * original.Height / original.Width);
                }
                Save(comparison, "comparison.png");
            }
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length != 0) return 2;
        try
        {
            var json = new JavaScriptSerializer();
            var config = json.Deserialize<Dictionary<string, object>>(File.ReadAllText("approved-form.json"));
            var names = (Dictionary<string, object>)config["controls"];
            var scenes = json.ConvertToType<Dictionary<string, object>[]>(config["scenes"]);
            var titles = Array.ConvertAll(scenes, item => (string)item["title"]);
            Application.EnableVisualStyles();
            using (var form = new Form())
            using (var font = new Font("Segoe UI", 11))
            {
                form.Text = (string)config["title"];
                form.StartPosition = FormStartPosition.CenterScreen;
                form.ClientSize = new Size(580, 390);
                form.BackColor = Color.AliceBlue;
                form.Font = font;
                Place(form, new Label(), form.Text, 20, 17, 530, 32);
                var status = Place(form, new TextBox { ReadOnly = true }, (string)config["initial"], 20, 58, 530, 30);
                var open = Button(form, (string)config["open_button"], 20);
                var apply = Button(form, (string)config["apply_button"], 174);
                var capture = Button(form, "Capture", 328);
                var entry = Place(form, new TextBox(), "", 20, 153, 530, 30);
                var list = Place(form, new ListBox { IntegralHeight = false }, "", 20, 195, 530, 170);
                for (int i = 0; i < 60; i++) list.Items.Add(string.Format("{0} {1:00}", config["row_label"], i));
                entry.Visible = list.Visible = apply.Enabled = false;
                EventHandler reveal = delegate
                {
                    status.Text = (string)config["opened"];
                    entry.Visible = list.Visible = apply.Enabled = true;
                };
                open.Click += reveal;
                form.Menu = new MainMenu(new[] {
                    new MenuItem("Actions", new[] { new MenuItem((string)config["open_button"], reveal) }),
                });
                apply.Click += delegate { status.Text = config["prefix"] + " " + entry.Text; };
                int count = 0;
                bool failed = false;
                capture.Click += delegate
                {
                    try { Capture(form, titles, count++); }
                    catch (Exception) { failed = true; form.Close(); }
                };
                form.Shown += delegate
                {
                    var handles = new Dictionary<string, long> {
                        {"root", form.Handle.ToInt64()}, {"capture", capture.Handle.ToInt64()},
                        {(string)names["status"], status.Handle.ToInt64()},
                        {(string)names["open"], open.Handle.ToInt64()},
                        {(string)names["apply"], apply.Handle.ToInt64()},
                        {(string)names["entry"], entry.Handle.ToInt64()},
                        {(string)names["list"], list.Handle.ToInt64()},
                    };
                    File.WriteAllText("windows.tmp", json.Serialize(handles));
                    File.Move("windows.tmp", "windows.json");
                };
                Application.Run(form);
                return failed ? 3 : 0;
            }
        }
        catch (Exception) { return 4; }
    }
}
"""
