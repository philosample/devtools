-- UI adapter for Snoize MIDI Monitor 1.5.4. Never targets an existing user window.
on run argv
    set capturePath to item 1 of argv
    set preflightPath to item 2 of argv
    set captureTitle to item 3 of argv
    set secondsToCapture to (item 4 of argv) as real
    set maxEvents to item 5 of argv
    tell application "MIDI Monitor"
        activate
        open POSIX file capturePath
    end tell
    tell application "System Events"
        tell process "MIDI Monitor"
            set frontmost to true
            repeat 100 times
                if exists window captureTitle then exit repeat
                delay 0.1
            end repeat
            if not (exists window captureTitle) then error "Capture document did not open"
            set w to window captureTitle
            perform action "AXRaise" of w
            delay 0.2
            if name of front window is not captureTitle then error "Capture document is not frontmost"
            -- The event buffer is in the container with the Clear button.
            set eventContainer to missing value
            repeat with g in groups of w
                if exists button "Clear" of g then set eventContainer to g
            end repeat
            if eventContainer is missing value then error "Unsupported MIDI Monitor event controls"
            set value of text field 1 of eventContainer to maxEvents
            set focused of text field 1 of eventContainer to true
            key code 36
            if (value of text field 1 of eventContainer as text) is not maxEvents then error "Event buffer setting did not stick"
            -- Save resolved settings before disconnecting sources at the end.
            keystroke "s" using command down
            delay 0.3
            do shell script "/bin/cp -n " & quoted form of capturePath & " " & quoted form of preflightPath
            click button "Clear" of eventContainer
            set startedAt to (current date)
            delay secondsToCapture
            if name of front window is not captureTitle then error "Focus changed during capture; refusing further UI actions"
            -- Disconnect this document only. No global MIDI or hardware settings change.
            repeat with sa in scroll areas of w
                repeat with tree in outlines of sa
                    repeat with r in rows of tree
                        if settable of attribute "AXDisclosing" of r then
                            set value of attribute "AXDisclosing" of r to true
                        end if
                    end repeat
                    repeat with r in rows of tree
                        repeat with c in checkboxes of r
                            -- Never click a mixed category: that enables other inputs.
                            if value of c is 1 then click c
                        end repeat
                    end repeat
                end repeat
            end repeat
            set endedAt to (current date)
            keystroke "s" using command down
            delay 0.3
            keystroke "w" using command down
            return ((endedAt - startedAt) as text)
        end tell
    end tell
end run
