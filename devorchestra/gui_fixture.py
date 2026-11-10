"""Controller-owned, fixed WinForms fixture. No project code is loaded."""

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()

$form = [Windows.Forms.Form]::new()
$form.Text = 'DevOrchestra GUI verification fixture'
$form.ClientSize = [Drawing.Size]::new(580, 390)
$form.StartPosition = 'CenterScreen'
$form.BackColor = [Drawing.Color]::AliceBlue
$form.Font = [Drawing.Font]::new('Segoe UI', 11)

function Add-Control($control, $text, $x, $y, $width, $height) {
    $control.Text = $text
    $control.SetBounds($x, $y, $width, $height)
    $form.Controls.Add($control)
    return $control
}
function Add-Button($text, $x) {
    $button = [Windows.Forms.Button]::new()
    $button.FlatStyle = 'System'
    Add-Control $button $text $x 105 144 34
}

$heading = Add-Control ([Windows.Forms.Label]::new()) 'GUI verification fixture' 20 17 440 32
$status = Add-Control ([Windows.Forms.TextBox]::new()) 'Ready' 20 58 530 30
$status.ReadOnly = $true
$open = Add-Button 'Open details' 20
$apply = Add-Button 'Apply input' 174
$capture = Add-Button 'Capture' 328
$entry = Add-Control ([Windows.Forms.TextBox]::new()) '' 20 153 530 30
$list = Add-Control ([Windows.Forms.ListBox]::new()) '' 20 195 530 170
$list.IntegralHeight = $false
foreach ($index in 0..59) { [void]$list.Items.Add(('Fixture row {0:00}' -f $index)) }
$entry.Visible = $list.Visible = $apply.Enabled = $false
$open.Add_Click({
    $status.Text = 'Details opened'
    $entry.Visible = $list.Visible = $apply.Enabled = $true
})
$apply.Add_Click({ $status.Text = "Applied: $($entry.Text)" })

$script:captures = 0
$script:failed = $false
$capture.Add_Click({
    try {
        $number = ++$script:captures
        if ($number -notin 1, 2, 3) { throw 'Unexpected capture' }
        $bitmap = [Drawing.Bitmap]::new($form.Width, $form.Height)
        try {
            $form.DrawToBitmap($bitmap, [Drawing.Rectangle]::new(0, 0, $bitmap.Width, $bitmap.Height))
            $raw = [IO.Path]::GetFullPath("scene-$number.png")
            $bitmap.Save("$raw.tmp", [Drawing.Imaging.ImageFormat]::Png)
            [IO.File]::Move("$raw.tmp", $raw)
            # Uniformly enlarge the area containing the observed status and controls.
            $crop = [Drawing.Rectangle]::new(12, 40, 564, 366)
            $report = [Drawing.Bitmap]::new(880, 610)
            try {
                $draw = [Drawing.Graphics]::FromImage($report)
                try {
                    $draw.Clear([Drawing.Color]::White)
                    $draw.DrawString(@('Launch', 'Details opened', 'Input applied and list scrolled')[$number - 1],
                        $form.Font, [Drawing.Brushes]::MidnightBlue, 16, 12)
                    $draw.DrawImage($bitmap, [Drawing.Rectangle]::new(17, 44, 846, 549),
                        $crop, [Drawing.GraphicsUnit]::Pixel)
                    $draw.DrawRectangle([Drawing.Pens]::RoyalBlue, 37, 114, 804, 42)
                } finally { $draw.Dispose() }
                $output = [IO.Path]::GetFullPath("report-$number.png")
                $report.Save("$output.tmp", [Drawing.Imaging.ImageFormat]::Png)
                [IO.File]::Move("$output.tmp", $output)
            } finally { $report.Dispose() }
            if ($number -eq 3) {
                $first = [Drawing.Image]::FromFile([IO.Path]::GetFullPath('scene-1.png'))
                try {
                    $comparison = [Drawing.Bitmap]::new(1200, 480)
                    try {
                        $draw = [Drawing.Graphics]::FromImage($comparison)
                        try {
                            $draw.Clear([Drawing.Color]::White)
                            $draw.DrawString('Before interaction', $form.Font, [Drawing.Brushes]::MidnightBlue, 20, 16)
                            $draw.DrawString('After input, scroll and resize', $form.Font, [Drawing.Brushes]::MidnightBlue, 620, 16)
                            $draw.DrawImage($first, 20, 52, 560, [int](560 * $first.Height / $first.Width))
                            $draw.DrawImage($bitmap, 620, 52, 560, [int](560 * $bitmap.Height / $bitmap.Width))
                        } finally { $draw.Dispose() }
                        $file = [IO.Path]::GetFullPath('comparison.png')
                        $comparison.Save("$file.tmp", [Drawing.Imaging.ImageFormat]::Png)
                        [IO.File]::Move("$file.tmp", $file)
                    } finally { $comparison.Dispose() }
                } finally { $first.Dispose() }
            }
        } finally { $bitmap.Dispose() }
    } catch {
        $script:failed = $true
        $form.Close()
    }
})

$form.Add_Shown({
    $manifest = @{
        root = $form.Handle.ToInt64(); status = $status.Handle.ToInt64()
        open = $open.Handle.ToInt64(); apply = $apply.Handle.ToInt64()
        capture = $capture.Handle.ToInt64()
        entry = $entry.Handle.ToInt64(); list = $list.Handle.ToInt64()
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText([IO.Path]::GetFullPath('windows.tmp'), $manifest)
    [IO.File]::Move([IO.Path]::GetFullPath('windows.tmp'), [IO.Path]::GetFullPath('windows.json'))
})
try {
    [Windows.Forms.Application]::Run($form)
} finally {
    $form.Dispose()
}
if ($script:failed) { exit 3 }
"""
