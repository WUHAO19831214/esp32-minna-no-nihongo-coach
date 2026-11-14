on run argv
    set audioFile to POSIX file (item 1 of argv)
    set outputVolume to (item 2 of argv) as integer
    set looping to (item 3 of argv) is "true"
    tell application "Music"
        set shuffle enabled to false
        set song repeat to off
        if looping then set song repeat to one
        set sound volume to outputVolume
        set studyName to "大家的日语 · Mac mini"
        if not (exists user playlist studyName) then make new user playlist with properties {name:studyName}
        set studyList to user playlist studyName
        delete every track of studyList
        add audioFile to studyList
        play track 1 of studyList once (not looping)
        return persistent ID of current track
    end tell
end run
