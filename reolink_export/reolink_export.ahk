#Requires AutoHotkey v2.0
#SingleInstance Force
; =====================================================================
;  reolink_export.ahk  -  Daily NVR recording exporter for Reolink Client
;  AutoHotkey v2 only.
;
;  Usage:
;    reolink_export.ahk                 ; normal run (uses config.ini)
;    reolink_export.ahk /calibrate      ; capture UI coordinates
;    reolink_export.ahk /dryrun         ; go through steps, never click Download
;    reolink_export.ahk /channel 1      ; only channel 1
;    reolink_export.ahk /hour 3         ; only hour 03 (hourly mode)
;    reolink_export.ahk /fullday        ; force full-day mode
;    reolink_export.ahk /channel 1 /hour 3 /dryrun   ; smallest test
;
;  Abort at any time:  Ctrl + Alt + Q
; =====================================================================

SetTitleMatchMode 2
SetWorkingDir A_ScriptDir

; ---- globals ----------------------------------------------------------
global ConfigFile := A_ScriptDir "\config.ini"
global OVERRIDES  := Map()         ; command-line overrides ("Section.Key" => value)
global LOGFILE    := ""            ; set once the export date is known

; ---- emergency abort --------------------------------------------------
^!q::{
    try Log("ABORTED by user (Ctrl+Alt+Q)")
    ExitApp
}

; ---- entry point ------------------------------------------------------
if !FileExist(ConfigFile) {
    MsgBox("config.ini not found next to the script:`n" ConfigFile, "Reolink Export", "Iconx")
    ExitApp
}
ParseArgs()
Main()
ExitApp


; =====================================================================
;  Config helpers
; =====================================================================
Conf(section, key, default := "") {
    global ConfigFile, OVERRIDES
    k := section "." key
    if OVERRIDES.Has(k)
        return OVERRIDES[k]
    return IniRead(ConfigFile, section, key, default)
}

ConfInt(section, key, default) {
    v := Trim(Conf(section, key, default))
    if (v = "")
        v := default
    return Integer(v)
}

ParseArgs() {
    global OVERRIDES
    ; flags
    for a in A_Args {
        switch StrLower(a) {
            case "/calibrate", "--calibrate":
                OVERRIDES["General.CalibrationMode"] := "1"
            case "/dryrun", "--dryrun":
                OVERRIDES["General.DryRun"] := "1"
            case "/fullday", "--fullday":
                OVERRIDES["General.ExportMode"] := "full_day"
            case "/hourly", "--hourly":
                OVERRIDES["General.ExportMode"] := "hourly"
            case "/caldate", "--caldate":
                OVERRIDES["General.CalDateMode"] := "1"
        }
    }
    ; key/value pairs
    i := 1
    while (i <= A_Args.Length) {
        a := StrLower(A_Args[i])
        if ((a = "/channel" || a = "--channel") && i < A_Args.Length) {
            OVERRIDES["Test.SingleChannel"] := A_Args[i + 1]
            i += 2
            continue
        }
        if ((a = "/hour" || a = "--hour") && i < A_Args.Length) {
            OVERRIDES["Test.SingleHour"] := A_Args[i + 1]
            i += 2
            continue
        }
        i += 1
    }
}


; =====================================================================
;  Logging
; =====================================================================
Log(msg) {
    global LOGFILE
    ts := FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss")
    line := "[" ts "] " msg "`r`n"
    if (LOGFILE != "") {
        try FileAppend(line, LOGFILE)
    }
    OutputDebug(line)
}

LogTaskResult(dateUI, ch, t, taskStart, taskEnd, result, attempt) {
    if (result.Has("dryrun") && result["dryrun"])
        dl := "dryrun"
    else
        dl := result["downloaded"] ? "yes" : "no"
    Log(Format(
        "RESULT date={1} ch={2}({3}) range={4} ({5}-{6}) attempt={7} start={8} end={9} downloaded={10} files={11} size={12} error={13}",
        dateUI, ch.key, ch.label, t.label, t.start, t.end, attempt,
        taskStart, taskEnd, dl, result["count"], HumanSize(result["bytes"]), result["error"]))
}

HumanSize(bytes) {
    if (bytes < 1024)
        return bytes " B"
    units := ["KB", "MB", "GB", "TB"]
    val := bytes / 1.0
    i := 0
    while (val >= 1024 && i < units.Length) {
        val /= 1024
        i++
    }
    return Format("{:.2f} {}", val, units[i])
}


; =====================================================================
;  Main
; =====================================================================
Main() {
    global LOGFILE, ConfigFile, OVERRIDES

    ; --- compute export date ---
    offset     := ConfInt("General", "DateOffset", -1)
    ts         := DateAdd(A_Now, offset, "Days")
    dateUI     := FormatTime(ts, "yyyy/MM/dd")     ; matches Reolink UI
    dateFolder := FormatTime(ts, "yyyy-MM-dd")     ; matches export folders

    exportRoot := Conf("General", "ExportRoot", "D:\Reolink_export")
    dayDir     := exportRoot "\" dateFolder
    DirCreate(dayDir)
    LOGFILE := dayDir "\export_log.txt"

    Log("==================== Reolink export run start ====================")
    Log("scriptDir=" A_ScriptDir)
    Log("export date UI=" dateUI "  folder=" dateFolder "  offset=" offset " day(s)")
    Log("mode=" Conf("General", "ExportMode", "hourly")
        "  dryRun=" ConfInt("General", "DryRun", 0)
        "  exportRoot=" exportRoot)

    ; --- calibration mode short-circuits everything else ---
    if (ConfInt("General", "CalibrationMode", 0) = 1) {
        Log("Calibration mode enabled.")
        CalibrateUI()
        Log("Calibration finished.")
        return
    }
    if (OVERRIDES.Has("General.CalDateMode")) {
        Log("Calendar-calibration mode enabled.")
        CalibrateCalendar()
        Log("Calendar calibration finished.")
        return
    }

    ; --- launch / activate Reolink ---
    if !ActivateReolink() {
        Log("FATAL: could not activate Reolink Client. Aborting.")
        return
    }
    if (ConfInt("Window", "MaximizeWindow", 1) = 1) {
        try WinMaximize(Conf("Window", "WinTitle", "Reolink Client"))
        Sleep 800
    }

    ; --- navigate to Playback ---
    if (Conf("Coords", "PlaybackTab", "") != "")
        ClickAt("PlaybackTab")
    Sleep ConfInt("Waits", "AfterPlaybackMs", 3000)

    ; --- set date once up front ---
    SetDate(ts)

    ; --- build work lists ---
    channels := BuildChannels()
    tasks    := BuildTasks()
    retries  := ConfInt("Run", "Retries", 1)
    dryRun   := (ConfInt("General", "DryRun", 0) = 1)

    stagingDir := Conf("Monitor", "StagingDir", "")
    if (stagingDir = "")
        Log("WARN: [Monitor] StagingDir is empty. Downloaded files cannot be detected or moved. "
            "Set it to the folder Reolink Client downloads into.")

    Log("Channels to process: " channels.Length "   Tasks per channel: " tasks.Length)

    for ch in channels {
        chDir := dayDir "\" ch.key
        DirCreate(chDir)
        Log("---- Channel " ch.index " (" ch.label ") -> " chDir " ----")

        if !SelectChannel(ch.index)
            Log("WARN: could not select channel " ch.index " (no coordinate?). Continuing anyway.")
        Sleep ConfInt("Waits", "AfterChannelSelectMs", 2500)

        ; some Reolink builds reset the date when switching device; re-apply
        SetDate(ts)

        for t in tasks {
            ok := false
            attempt := 0
            loop (retries + 1) {
                attempt := A_Index
                taskStart := FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss")
                result := ""
                try {
                    result := ExportTask(ch, chDir, t, dryRun)
                    ok := result["ok"]
                } catch as e {
                    result := Map("ok", false, "downloaded", false, "count", 0,
                        "bytes", 0, "error", "EXCEPTION: " e.Message, "timedout", false)
                    Log("EXCEPTION ch=" ch.key " " t.label " attempt " attempt ": " e.Message)
                }
                taskEnd := FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss")
                LogTaskResult(dateUI, ch, t, taskStart, taskEnd, result, attempt)

                if ok
                    break
                if (attempt <= retries) {
                    Log("Retrying ch=" ch.key " " t.label " (recovering UI) ...")
                    RecoverUI()
                }
            }
        }
    }

    Log("==================== Reolink export run complete ====================")

    if (ConfInt("General", "KeepOpenAfter", 1) = 0) {
        try WinClose(Conf("Window", "WinTitle", "Reolink Client"))
        Log("Closed Reolink Client (KeepOpenAfter=0).")
    }
}


; =====================================================================
;  Work-list builders
; =====================================================================
BuildChannels() {
    num    := ConfInt("General", "NumChannels", 6)
    single := ConfInt("Test", "SingleChannel", 0)
    chans  := []
    loop num {
        i := A_Index
        if (single != 0 && single != i)
            continue
        key   := "CH" Format("{:02}", i)
        label := Conf("Channels", key, "Channel " i)
        chans.Push({ index: i, label: label, key: key })
    }
    return chans
}

BuildTasks() {
    mode       := Conf("General", "ExportMode", "hourly")
    singleHour := ConfInt("Test", "SingleHour", -1)
    tasks      := []

    if (mode = "full_day") {
        tasks.Push({ label: "FULLDAY", start: "00:00:00", end: "23:59:59" })
        return tasks
    }

    ; hourly (default)
    if (singleHour >= 0 && singleHour <= 23) {
        h := Format("{:02}", singleHour)
        tasks.Push({ label: h "h", start: h ":00:00", end: h ":59:59" })
        return tasks
    }
    loop 24 {
        hh := A_Index - 1
        h  := Format("{:02}", hh)
        tasks.Push({ label: h "h", start: h ":00:00", end: h ":59:59" })
    }
    return tasks
}


; =====================================================================
;  Reolink window control
; =====================================================================
ActivateReolink() {
    winTitle := Conf("Window", "WinTitle", "Reolink Client")
    exe      := Conf("General", "ReolinkExe", "")

    if WinExist(winTitle) {
        WinActivate(winTitle)
        WinWaitActive(winTitle, , 10)
        Sleep ConfInt("Waits", "AfterActivateMs", 2000)
        NormalizeReolinkWindow()
        return true
    }

    if (exe = "" || !FileExist(exe)) {
        Log("ERROR: Reolink executable not found: '" exe "'")
        return false
    }
    Log("Launching Reolink: " exe)
    try Run(exe)
    catch as e {
        Log("ERROR launching Reolink: " e.Message)
        return false
    }
    if !WinWait(winTitle, , 60) {
        Log("ERROR: Reolink window did not appear within 60s.")
        return false
    }
    WinActivate(winTitle)
    Log("Reolink launched; waiting for UI to settle.")
    Sleep ConfInt("Waits", "AfterLaunchMs", 15000)
    NormalizeReolinkWindow()
    return true
}

; Put Reolink on a fixed monitor (primary by default) and maximize it, so the
; maximized size - and therefore every calibrated coordinate - is consistent
; across multiple displays. Runs in both calibration and normal export.
NormalizeReolinkWindow() {
    winTitle := Conf("Window", "WinTitle", "ahk_exe Reolink.exe")
    mon      := Trim(Conf("Window", "ForceMonitor", "primary"))
    doMax    := (ConfInt("Window", "MaximizeWindow", 1) = 1)

    if (StrLower(mon) = "current") {
        if doMax
            try WinMaximize(winTitle)
        Sleep 400
        return
    }

    ; resolve target monitor number
    if (StrLower(mon) = "primary")
        monNum := MonitorGetPrimary()
    else
        monNum := Integer(mon)
    if (monNum < 1 || monNum > MonitorGetCount())
        monNum := MonitorGetPrimary()

    try {
        MonitorGetWorkArea(monNum, &L, &T, &R, &B)
        try WinRestore(winTitle)          ; un-maximize so it can be moved
        Sleep 200
        WinMove(L + 40, T + 40, , , winTitle)
        Sleep 200
        if doMax
            WinMaximize(winTitle)
        Sleep 400
        WinActivate(winTitle)
        Log("Reolink normalized to monitor " monNum " (work area " L "," T " - " R "," B ").")
    } catch as e {
        Log("WARN: could not move Reolink to monitor " monNum ": " e.Message)
    }
}

RecoverUI() {
    ; best-effort: close any stray Download window so the next attempt starts clean
    try Send("{Escape}")
    Sleep 500
    if (Conf("Coords", "DownloadWindowClose", "") != "")
        ClickAt("DownloadWindowClose")
    Sleep 500
}


; =====================================================================
;  UI primitives (coordinate-driven, client-relative)
; =====================================================================
ClickAt(name) {
    coord := Conf("Coords", name, "")
    if (coord = "") {
        Log("WARN: missing coordinate for '" name "'.")
        return false
    }
    parts := StrSplit(coord, ",")
    if (parts.Length < 2) {
        Log("WARN: malformed coordinate for '" name "': '" coord "'.")
        return false
    }
    x := Integer(Trim(parts[1]))
    y := Integer(Trim(parts[2]))
    CoordMode "Mouse", "Client"
    Click x, y
    Sleep ConfInt("Waits", "GenericClickMs", 700)
    return true
}

SetField(coordName, text) {
    if !ClickAt(coordName)
        return false
    Send "^a"
    Sleep 150
    SendText(text)        ; literal: safe for ":" and "/"
    Sleep 150
    return true
}

SetDate(ts) {
    method := Conf("Date", "InputMethod", "calendar")
    dateUI := FormatTime(ts, "yyyy/MM/dd")

    if (method = "manual") {
        Log("Date input method = manual; assuming UI already shows " dateUI)
        return true
    }
    if (method = "calendar")
        return SetDateCalendar(ts)

    ; --- "type" method (text date field) ---
    if (Conf("Coords", "DateSelector", "") = "") {
        Log("WARN: no DateSelector coordinate; skipping date set.")
        return false
    }
    ClickAt("DateSelector")
    Sleep ConfInt("Waits", "AfterDateChangeMs", 2000)
    Send "^a"
    Sleep 150
    SendText(dateUI)
    Sleep 150
    Send "{Enter}"
    Sleep ConfInt("Waits", "AfterDateChangeMs", 2000)
    Log("Date typed: " dateUI)
    return true
}

; --- pick a date from the calendar popup using calibrated grid geometry ---
SetDateCalendar(ts) {
    ox := ConfInt("Calendar", "OriginX", 0)
    oy := ConfInt("Calendar", "OriginY", 0)
    cp := ConfInt("Calendar", "ColPitch", 0)
    rp := ConfInt("Calendar", "RowPitch", 0)
    if (ox = 0 || oy = 0 || cp = 0 || rp = 0) {
        Log("ERROR: calendar geometry not calibrated (run /caldate). Date NOT set.")
        return false
    }
    if (Conf("Coords", "DateSelector", "") = "") {
        Log("ERROR: no DateSelector coordinate. Date NOT set.")
        return false
    }

    ; open the calendar popup
    ClickAt("DateSelector")
    Sleep ConfInt("Waits", "AfterDateChangeMs", 2000)

    tY := Integer(FormatTime(ts, "yyyy"))
    tM := Integer(FormatTime(ts, "MM"))
    tD := Integer(FormatTime(ts, "dd"))
    cY := Integer(FormatTime(A_Now, "yyyy"))
    cM := Integer(FormatTime(A_Now, "MM"))

    ; calendar opens on the current month; step back if target is earlier
    back := (cY * 12 + cM) - (tY * 12 + tM)
    if (back > 0) {
        if (Conf("Coords", "CalPrevMonth", "") = "") {
            Log("WARN: previous-month nav needed (back=" back ") but CalPrevMonth not calibrated.")
        } else {
            loop back {
                ClickAt("CalPrevMonth")
                Sleep ConfInt("Waits", "AfterDateChangeMs", 2000)
            }
        }
    } else if (back < 0) {
        Log("WARN: target month is after current month (back=" back "); not handled.")
    }

    ; compute the day cell (7-col grid, WDay 1=Sun)
    firstTs := Format("{:04}{:02}01000000", tY, tM)
    fw  := Integer(FormatTime(firstTs, "WDay"))
    idx := (fw - 1) + (tD - 1)
    col := Mod(idx, 7)
    row := idx // 7
    x := ox + col * cp
    y := oy + row * rp

    CoordMode "Mouse", "Client"
    Click x, y
    Sleep ConfInt("Waits", "AfterDateChangeMs", 2000)
    Log(Format("Calendar selected {1}/{2}/{3} (back {4} mo, row {5} col {6} -> {7},{8})",
        tY, Format("{:02}", tM), Format("{:02}", tD), back, row, col, x, y))
    return true
}

DaysInMonth(y, m) {
    if (m = 2)
        return (Mod(y, 4) = 0 && (Mod(y, 100) != 0 || Mod(y, 400) = 0)) ? 29 : 28
    return (m = 4 || m = 6 || m = 9 || m = 11) ? 30 : 31
}

; Calibrate the calendar grid once. Captures 3 day cells in the CURRENT month
; plus the previous-month arrow, then derives origin + pitch (month-independent).
CalibrateCalendar() {
    global ConfigFile
    winTitle := Conf("Window", "WinTitle", "ahk_exe Reolink.exe")

    if !ActivateReolink()
        MsgBox("Could not activate Reolink. Open it on the Playback page, then re-run /caldate.",
            "Calendar Calibration", "Iconx")
    if (ConfInt("Window", "MaximizeWindow", 1) = 1) {
        try WinMaximize(winTitle)
        Sleep 800
    }

    cY := Integer(FormatTime(A_Now, "yyyy"))
    cM := Integer(FormatTime(A_Now, "MM"))
    firstTs := Format("{:04}{:02}01000000", cY, cM)
    fw   := Integer(FormatTime(firstTs, "WDay"))   ; 1 = Sun
    dim  := DaysInMonth(cY, cM)
    dSat := 1 + Mod(7 - fw, 7)                      ; first Saturday of the month
    monthName := FormatTime(A_Now, "MMMM yyyy")

    if (dSat = 1) {
        MsgBox("This month's 1st is a Saturday, which makes calibration ambiguous.`n"
            "Re-run /caldate in a different month, or tell me.", "Calendar Calibration", "Iconx")
        return
    }

    MsgBox(
        "Calendar calibration for " monthName ".`n`n"
        "1) Click the date field so the calendar popup opens and shows " monthName ".`n"
        "   (Leave it open for the next steps.)`n`n"
        "2) Capture these THREE day cells (hover the number, press F8):`n"
        "      - day 1`n"
        "      - day " dSat "  (the first Saturday, right side)`n"
        "      - day " dim "  (the LAST day of the month)`n`n"
        "3) Then capture the PREVIOUS-month arrow (the one that turns "
        monthName " into the month before).`n`n"
        "F8 = capture, F9 = skip, Esc = cancel.",
        "Calendar Calibration", "Iconi")

    r1 := CaptureOne("day 1", "hover the '1' cell")
    if (r1 = "CANCEL" || r1 = "") {
        ToolTip
        MsgBox("Day 1 not captured. Aborting.", "Calendar Calibration", "Iconx")
        return
    }
    rS := CaptureOne("day " dSat, "hover the first Saturday ('" dSat "')")
    if (rS = "CANCEL" || rS = "") {
        ToolTip
        MsgBox("Saturday not captured. Aborting.", "Calendar Calibration", "Iconx")
        return
    }
    rL := CaptureOne("day " dim, "hover the last day ('" dim "')")
    if (rL = "CANCEL" || rL = "") {
        ToolTip
        MsgBox("Last day not captured. Aborting.", "Calendar Calibration", "Iconx")
        return
    }
    rP := CaptureOne("Previous-month arrow", "hover the arrow that goes to the EARLIER month")
    ToolTip

    p1 := StrSplit(r1, ","), pS := StrSplit(rS, ","), pL := StrSplit(rL, ",")
    x1 := Integer(Trim(p1[1])), y1 := Integer(Trim(p1[2]))
    xS := Integer(Trim(pS[1]))
    yL := Integer(Trim(pL[2]))

    col1 := fw - 1                  ; day 1: row 0
    colS := 6                       ; first Saturday: row 0
    idxL := (fw - 1) + (dim - 1)
    rowL := idxL // 7

    colPitch := Round((xS - x1) / (colS - col1))
    originX  := Round(x1 - col1 * colPitch)
    originY  := y1
    if (rowL = 0) {
        MsgBox("Unexpected layout (last day in row 0). Aborting.", "Calendar Calibration", "Iconx")
        return
    }
    rowPitch := Round((yL - y1) / rowL)

    IniWrite(originX,    ConfigFile, "Calendar", "OriginX")
    IniWrite(originY,    ConfigFile, "Calendar", "OriginY")
    IniWrite(colPitch,   ConfigFile, "Calendar", "ColPitch")
    IniWrite(rowPitch,   ConfigFile, "Calendar", "RowPitch")
    IniWrite("calendar", ConfigFile, "Date",     "InputMethod")
    if (rP != "" && rP != "CANCEL")
        IniWrite(rP, ConfigFile, "Coords", "CalPrevMonth")

    Log(Format("Calendar geometry: OriginX={1} OriginY={2} ColPitch={3} RowPitch={4} PrevArrow={5}",
        originX, originY, colPitch, rowPitch, (rP != "" && rP != "CANCEL") ? rP : "(skipped)"))
    MsgBox(Format(
        "Calendar calibrated:`n`n  OriginX = {1}`n  OriginY = {2}`n  ColPitch = {3}`n  RowPitch = {4}`n`n"
        "[Date] InputMethod set to 'calendar'.`nTest with:  /channel 1 /dryrun",
        originX, originY, colPitch, rowPitch), "Calendar Calibration", "Iconi")
}

SelectChannel(i) {
    ; The NVR channel is chosen from a dropdown ("Channel x/12"), not a list.
    ; Open the dropdown, then click the i-th configured channel item.
    if (Conf("Coords", "ChannelDropdown", "") = "") {
        Log("WARN: no ChannelDropdown coordinate.")
        return false
    }
    ClickAt("ChannelDropdown")
    Sleep ConfInt("Waits", "AfterChannelDropdownMs", 1200)

    item := "ChannelItem_" i
    if (Conf("Coords", item, "") = "") {
        Log("WARN: no " item " coordinate.")
        try Send("{Escape}")          ; close dropdown to avoid a stuck state
        return false
    }
    return ClickAt(item)
}

SetTimeRange(startStr, endStr) {
    SetField("StartTimeField", startStr)
    SetField("EndTimeField", endStr)
    return true
}


; =====================================================================
;  One export task (single channel + single time range)
; =====================================================================
ExportTask(ch, chDir, t, dryRun) {
    stagingDir := Conf("Monitor", "StagingDir", "")
    baseline   := SnapshotDir(stagingDir)

    ; open the Download window
    if !ClickAt("DownloadIcon")
        return Map("ok", false, "downloaded", false, "count", 0, "bytes", 0,
            "error", "no DownloadIcon coordinate", "timedout", false)
    Sleep ConfInt("Waits", "AfterOpenDownloadMs", 2500)

    ; Type = File, Stream = Clear/Fluent
    if (ConfInt("Download", "SetTypeStream", 1) = 1) {
        if (Conf("Coords", "TypeFileOption", "") != "")
            ClickAt("TypeFileOption")
        streamName := Conf("Download", "Stream", "Clear")
        if (streamName = "Clear" && Conf("Coords", "StreamClearOption", "") != "")
            ClickAt("StreamClearOption")
        else if (Conf("Coords", "StreamFluentOption", "") != "")
            ClickAt("StreamFluentOption")
    }

    ; time range
    SetTimeRange(t.start, t.end)
    Sleep 300

    ; Choose All
    ClickAt("ChooseAll")
    Sleep ConfInt("Waits", "AfterChooseAllMs", 1500)

    if dryRun {
        Log("DRY-RUN ch=" ch.key " " t.label " range " t.start "-" t.end " (Download NOT clicked)")
        ClickAt("DownloadWindowClose")
        Sleep ConfInt("Waits", "GenericClickMs", 700)
        return Map("ok", true, "downloaded", false, "count", 0, "bytes", 0,
            "error", "", "timedout", false, "dryrun", true)
    }

    ; Download
    if !ClickAt("DownloadButton")
        return Map("ok", false, "downloaded", false, "count", 0, "bytes", 0,
            "error", "no DownloadButton coordinate", "timedout", false)
    Sleep ConfInt("Waits", "AfterDownloadClickMs", 2500)

    result := WaitForDownloads(baseline, chDir)

    ; close Download window for a clean next iteration
    ClickAt("DownloadWindowClose")
    Sleep ConfInt("Waits", "GenericClickMs", 700)

    return result
}


; =====================================================================
;  Download monitoring + file move
;  Strategy: Reolink writes into StagingDir (its configured download
;  folder). We snapshot before, wait until new files stabilise, then
;  move them into the per-channel target folder.
; =====================================================================
SnapshotDir(dir) {
    m := Map()
    if (dir = "" || !DirExist(dir))
        return m
    Loop Files, dir "\*.*", "R" {
        m[A_LoopFileFullPath] := A_LoopFileSize
    }
    return m
}

IsPartial(path) {
    exts := StrSplit(Conf("Monitor", "PartialExtensions", ".tmp,.part,.download,.crdownload"), ",")
    SplitPath(path, , , &ext)
    ext := "." StrLower(ext)
    for e in exts {
        e := StrLower(Trim(e))
        if (e != "" && ext = e)
            return true
    }
    return false
}

; Signature of a directory tree: "<fileCount>/<totalBytes>". Changes whenever a
; file is added, removed, or grows - used as an activity heartbeat.
DirSignature(dir) {
    if (dir = "" || !DirExist(dir))
        return "0/0"
    cnt := 0
    total := 0
    Loop Files, dir "\*.*", "R" {
        cnt++
        total += A_LoopFileSize
    }
    return cnt "/" total
}

WaitForDownloads(baseline, targetDir) {
    stagingDir := Conf("Monitor", "StagingDir", "")
    tempDir    := Conf("Monitor", "TempDir", "")
    timeoutMs  := ConfInt("Waits", "DownloadTimeoutMs", 5400000)   ; 90 min default
    pollMs     := ConfInt("Waits", "PollIntervalMs", 3000)
    quietMs    := ConfInt("Monitor", "QuietSeconds", 120) * 1000
    graceMs    := ConfInt("Monitor", "NoFileGraceMs", 120000)

    if (stagingDir = "" || !DirExist(stagingDir)) {
        return Map("ok", false, "downloaded", false, "count", 0, "bytes", 0,
            "error", "StagingDir missing/invalid", "timedout", false)
    }

    ; Reolink writes the in-progress file to TempDir and only drops the finished
    ; .mp4 into StagingDir minutes later. Watch BOTH: as long as either changes,
    ; a download is still active. "Done" = both quiet for quietMs.
    startT       := A_TickCount
    lastActivity := A_TickCount
    prevSig      := ""
    seenNew      := false
    timedOut     := false

    loop {
        sig := DirSignature(stagingDir) "|" DirSignature(tempDir)
        if (sig != prevSig) {
            prevSig := sig
            lastActivity := A_TickCount
        }

        ; count finished (non-partial) new files in staging
        newCount := 0
        for path, size in SnapshotDir(stagingDir) {
            if (!baseline.Has(path) && !IsPartial(path))
                newCount++
        }
        if (newCount > 0)
            seenNew := true

        if (seenNew && (A_TickCount - lastActivity >= quietMs))
            break                       ; a file landed and none since for quietMs -> done
        if (!seenNew && (A_TickCount - startT >= graceMs))
            break                       ; nothing ever started within grace -> empty day
        if (A_TickCount - startT >= timeoutMs) {
            timedOut := true
            break
        }
        Sleep pollMs
    }

    ; move completed new files into the target folder
    cur   := SnapshotDir(stagingDir)
    moved := 0
    bytes := 0
    for path, size in cur {
        if baseline.Has(path)
            continue
        if IsPartial(path)
            continue                    ; leave still-downloading files alone
        SplitPath(path, &fname)
        dest := targetDir "\" fname
        try {
            FileMove(path, dest, 1)
            moved++
            bytes += size
        } catch as e {
            Log("WARN: move failed for " path " : " e.Message)
        }
    }

    downloaded := (moved > 0)
    err := ""
    if timedOut
        err := "timeout after " timeoutMs "ms (moved " moved " file(s))"
    ; if we timed out but still captured files, treat as success to avoid re-downloading duplicates
    ok := (!timedOut) || (moved > 0)
    return Map("ok", ok, "downloaded", downloaded, "count", moved, "bytes", bytes,
        "error", err, "timedout", timedOut)
}


; =====================================================================
;  Calibration mode
; =====================================================================
CalibrateUI() {
    global ConfigFile
    winTitle := Conf("Window", "WinTitle", "Reolink Client")

    if !ActivateReolink() {
        MsgBox("Could not activate Reolink Client.`nOpen it manually, navigate to Playback, then re-run calibration.",
            "Reolink Calibration", "Iconx")
    }
    if (ConfInt("Window", "MaximizeWindow", 1) = 1) {
        try WinMaximize(winTitle)
        Sleep 800
    }

    elements := [
        ["PlaybackTab",          "the Playback tab / button"],
        ["DateSelector",         "the date selector field (bottom of the timeline)"],
        ["DownloadIcon",         "the Download icon that opens the Download window"],
        ["ChannelDropdown",      "the channel dropdown box (e.g. 'Channel 6/12') - capture it CLOSED"],
        ["ChannelItem_1",        "1st wanted channel - now OPEN the dropdown, then hover this item"],
        ["ChannelItem_2",        "2nd wanted channel in the OPEN dropdown list"],
        ["ChannelItem_3",        "3rd wanted channel in the OPEN dropdown list"],
        ["ChannelItem_4",        "4th wanted channel in the OPEN dropdown list"],
        ["ChannelItem_5",        "5th wanted channel in the OPEN dropdown list"],
        ["ChannelItem_6",        "6th wanted channel in the OPEN dropdown list"],
        ["TypeFileOption",       "Type = File option (Download window)"],
        ["StreamClearOption",    "Stream = Clear option (Download window)"],
        ["StreamFluentOption",   "Stream = Fluent option (optional)"],
        ["StartTimeField",       "the Start time input field (Download window)"],
        ["EndTimeField",         "the End time input field (Download window)"],
        ["ChooseAll",            "the Choose All checkbox / button"],
        ["DownloadButton",       "the Download button (Download window)"],
        ["DownloadWindowClose",  "the X / Close button of the Download window"]
    ]

    MsgBox(
        "Calibration mode.`n`n"
        "For each item, the tooltip shows live mouse coordinates:`n"
        "  - Hover the mouse over the element in Reolink`n"
        "  - F8 = capture     F9 = skip     Esc = cancel`n`n"
        "Coordinates are CLIENT-relative to the Reolink window, so keep the`n"
        "window MAXIMIZED and do not move/resize it after calibrating.`n`n"
        "Tip: open the Download window manually before capturing the items`n"
        "that live inside it (time fields, Choose All, Download, Close).",
        "Reolink Calibration", "Iconi")

    for pair in elements {
        name := pair[1]
        desc := pair[2]
        res := CaptureOne(name, desc)
        if (res = "CANCEL") {
            ToolTip
            MsgBox("Calibration cancelled. Items captured so far are saved.", "Reolink Calibration", "Iconi")
            return
        }
        if (res = "")
            continue                    ; skipped
        IniWrite(res, ConfigFile, "Coords", name)
        Log("Calibrated " name " = " res)
    }
    ToolTip
    MsgBox("Calibration complete.`nCoordinates written to [Coords] in config.ini.", "Reolink Calibration", "Iconi")
}

CaptureOne(name, desc) {
    winTitle := Conf("Window", "WinTitle", "Reolink Client")
    try WinActivate(winTitle)
    CoordMode "Mouse", "Client"
    Loop {
        MouseGetPos &mx, &my
        ToolTip "CALIBRATE: " name "`n" desc "`n`nClient X=" mx "   Y=" my . "`n`n[F8] capture    [F9] skip    [Esc] cancel"
        if GetKeyState("F8", "P") {
            KeyWait "F8"
            ToolTip
            return mx "," my
        }
        if GetKeyState("F9", "P") {
            KeyWait "F9"
            ToolTip
            return ""
        }
        if GetKeyState("Escape", "P") {
            KeyWait "Escape"
            ToolTip
            return "CANCEL"
        }
        Sleep 30
    }
}
