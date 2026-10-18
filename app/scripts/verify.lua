-- Bounded hardware verification of CJK font rendering, 4 study/repeater pages, touch, and audio player.
local storage=require("storage")
local json=require("json")
local system=require("system")
local display=require("display")
local delay=require("delay")
local thread=require("thread")
local root=storage.join_path(storage.get_root_dir(),"apps","minna_nihongo")
local M=dofile(root.."/scripts/model.lua")
local R=dofile(root.."/scripts/render.lua")
local s=M.new()
local screen
local report={version="1.0.0",checks={},clock=system.date("%Y-%m-%d %H:%M:%S")}

local function run()
  dofile(root.."/scripts/ownership.lua").activate(thread)
  if storage.exists(root.."/snapshot.json") then
    local d=json.decode(storage.read_file(root.."/snapshot.json"))
    assert(M.feed(s,d,system.millis()),"Snapshot must validate")
  end
  R.init(display,root)
  screen=display.open()
  report.screen=screen:info()
  assert(report.screen.width==480 and report.screen.touch_available)

  for page=1,5 do
    s.page=page
    if page==3 then s.ab_state="loop";s.ab_a=3;s.ab_b=12;s.playing=true;s.play_started_ms=system.millis()-4000 end
    local t0=system.millis()
    R.draw(screen,s,t0)
    screen:touch()
    report.checks[#report.checks+1]={page=page,render_ms=system.millis()-t0,ok=true}
    delay.delay_ms(220)
  end

  if storage.exists(root.."/clip.mp3") then
    local aok,audio=pcall(require,"audio")
    if aok and audio then
      local out=audio.open_output()
      if out then
        local pl=audio.player({output=out})
        if pl then
          local played=pl:play(root.."/clip.mp3")
          delay.delay_ms(600)
          local st=pl:poll()
          report.audio={played=played,state=st and st.state or "unknown"}
          pl:stop();pl:close()
        end
        out:close()
      end
    end
  end

  report.ok=true
end

local ok,err=xpcall(run,debug.traceback)
if screen then pcall(screen.close,screen) end
R.close()
if not ok then report.ok=false;report.error=tostring(err) end
assert(storage.write_file(root.."/verification.json",json.encode(report)))
print(json.encode(report))
if not ok then error(err) end
