on run argv
    set wantedId to (item 1 of argv) as integer
    tell application "Music"
        set matches to (every AirPlay device whose id is wantedId and available is true)
        if (count of matches) is not 1 then error "AirPlay 输出已离线，请刷新后重选"
        set current AirPlay devices to matches
    end tell
    return "ok"
end run
