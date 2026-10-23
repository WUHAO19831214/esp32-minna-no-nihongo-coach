local display=require("display")
local storage=require("storage")
local system=require("system")
local delay=require("delay")
local json=require("json")
local thread=require("thread")
local capability=require("capability")
local root=storage.join_path(storage.get_root_dir(),"apps","minna_nihongo")
local model=dofile(root.."/scripts/model.lua")
local render=dofile(root.."/scripts/render.lua")
local s=model.new()

local function load_json(name)
  if not storage.exists(root.."/"..name) then return nil end
  local ok,d=pcall(json.decode,storage.read_file(root.."/"..name))
  return ok and d or nil
end

local cfg=load_json("config.json") or {}
local prefs=load_json("preferences.json") or {}
s.brightness=prefs.brightness or cfg.brightness or 75
s.volume=prefs.volume or 80
s.leds_muted=prefs.leds_muted==true
if prefs.haptic_enabled~=nil then s.haptic_enabled=prefs.haptic_enabled==true end
s.slow_tts=prefs.slow_tts==true
if prefs.single_loop~=nil then s.single_loop=(prefs.single_loop~=false) else s.single_loop=true end
if prefs.audio_target=="bt" or prefs.audio_target=="esp32" then
  s.audio_target=prefs.audio_target
end

local screen,strip,gpio,button_driver=nil,nil,nil,nil
local owns_display=false
local button_handles={}
local audio_out,player=nil,nil
local frames,total_ms,max_ms=0,0,0
local event_seq=0
local boot_tag=string.format("%.0f",system.millis())
local last_haptic=-1000

local function save_prefs()
  local p={
    brightness=s.brightness,
    volume=s.volume,
    leds_muted=s.leds_muted,
    haptic_enabled=s.haptic_enabled,
    slow_tts=s.slow_tts,
    single_loop=(s.single_loop~=false),
    audio_target=s.audio_target
  }
  assert(storage.write_file(root.."/preferences.json.tmp",json.encode(p)))
  assert(storage.rename(root.."/preferences.json.tmp",root.."/preferences.json"))
end

local function apply_hardware_volume()
  pcall(capability.call,"system_control",{operation="set_volume",volume=s.volume},{max_output_bytes=256})
  if audio_out and type(audio_out.set_volume)=="function" then
    pcall(audio_out.set_volume,audio_out,s.volume)
  end
  if player and type(player.set_volume)=="function" then
    pcall(player.set_volume,player,s.volume)
  end
end

local function apply_hardware_brightness()
  pcall(capability.call,"system_control",{operation="set_display",brightness=s.brightness},{max_output_bytes=256})
end

local function haptic()
  if not s.haptic_enabled then return end
  local now=system.millis()
  if now-last_haptic<60 then return end
  last_haptic=now
  pcall(capability.call,"system_control",{operation="vibrate",duration_ms=cfg.haptic_ms or 12},{max_output_bytes=256})
end

local function emit_event(ev,now)
  if not ev then return end
  event_seq=event_seq+1
  ev.id=boot_tag.."-"..tostring(event_seq)
  ev.created_ms=math.floor(now)
  local raw=json.encode(ev)
  local ok=thread.sync.queue_send("minna_nihongo_events",raw,0)
  if not ok then
    thread.sync.queue_recv("minna_nihongo_events",0)
    thread.sync.queue_send("minna_nihongo_events",raw,0)
  end
end

local function ensure_audio()
  if player then return true end
  local ok,audio=pcall(require,"audio")
  if not ok or not audio then return false end
  local o_ok,out=pcall(audio.open_output)
  if o_ok and out then
    audio_out=out
    local p_ok,pl=pcall(audio.player,{output=audio_out})
    if p_ok and pl then
      player=pl
      apply_hardware_volume()
      return true
    end
  end
  return false
end

local function stop_clip()
  if player then pcall(player.stop,player) end
end

local function play_clip_file()
  if s.volume<=0 or s.audio_target=="bt" then
    stop_clip()
    return false
  end
  local clip_path=root.."/clip.mp3"
  if not storage.exists(clip_path) then return false end
  if not ensure_audio() then return false end
  apply_hardware_volume()
  pcall(player.stop,player)
  local ok,played=pcall(player.play,player,clip_path)
  return ok and played==true
end

local function load_local_lesson(lesson_num)
  local fname=string.format("lessons/L%02d.json",math.floor(tonumber(lesson_num) or 6))
  local b=load_json(fname)
  if b then
    stop_clip()
    model.apply_lesson_bundle(s,b)
    return true
  end
  return false
end

local function setup_peripherals()
  if cfg.indicator_hardware_enabled~=false then
    local ok,ls=pcall(function() return require("led_strip").new(38,6) end)
    if ok then strip=ls end
    if cfg.indicator_buttons_enabled~=false then
      local bok,drv=pcall(require,"button")
      if bok and drv then
        button_driver=drv
        for _,pin in ipairs({11,10}) do
          local h=drv.new(pin,0)
          if h then button_handles[#button_handles+1]=h end
        end
        if #button_handles==2 then
          local gok,gp=pcall(require,"gpio")
          if gok then gpio=gp end
        end
      end
    end
  end
end

local function cleanup()
  stop_clip()
  if player then pcall(player.close,player);player=nil end
  if audio_out then pcall(audio_out.close,audio_out);audio_out=nil end
  if strip then pcall(strip.clear,strip);pcall(strip.close,strip);strip=nil end
  if button_driver then
    for _,h in ipairs(button_handles) do pcall(button_driver.close,h) end
  end
  button_handles={}
  if screen then pcall(screen.close,screen);screen=nil end
  render.close()
end

local function handle_action_side_effects(act,now)
  if act=="lesson" then
    load_local_lesson(s.lesson)
  elseif act=="pause" or act=="key_l_pause" then
    stop_clip()
  elseif act=="brightness" then
    apply_hardware_brightness()
    save_prefs()
  elseif act=="volume" then
    apply_hardware_volume()
    if s.volume<=0 then stop_clip();s.playing=false end
    save_prefs()
  elseif act=="bt_toggle" or act=="bt_connect" then
    if s.audio_target=="bt" then stop_clip() end
    save_prefs()
  elseif act=="prefs" or act=="leds_toggle" then
    save_prefs()
  end
end

local function run()
  if not (args and args.lifecycle_prepared) then
    while thread.sync.queue_recv("minna_nihongo_lifecycle",0) do end
  end
  dofile(root.."/scripts/ownership.lua").activate(thread)
  local found,status=thread.get("minna_nihongo_receiver")
  if not found or not status:find("status=running",1,true) then
    thread.start(root.."/scripts/receiver.lua",{},{
      name="minna_nihongo_receiver",
      exclusive="minna_nihongo_receiver",
      replace=false,
      timeout_ms=0
    })
  end
  render.init(display,root)
  screen=display.open()
  owns_display=true
  local info=screen:info()
  assert(info.width==480 and info.height==480 and info.touch_available)
  setup_peripherals()
  apply_hardware_brightness()

  local seed=load_json("snapshot.json")
  if seed then model.feed(s,seed,system.millis()) end
  load_local_lesson(s.lesson)

  local began=system.millis()
  local last_draw,last_health,last_led_sig=-1000,-1000,""
  local last_key=""
  local buttons_armed=false

  while true do
    local now=system.millis()
    if thread.sync.queue_recv("minna_nihongo_lifecycle",0)=="exit" then break end

    while true do
      local raw=thread.sync.queue_recv("minna_nihongo_feed",0)
      if not raw then break end
      local ok,d=pcall(json.decode,raw)
      if ok and type(d)=="table" then
        if d.reload_snapshot then
          local prev_l=s.lesson
          local snap=load_json("snapshot.json")
          if snap then
            if type(snap.lesson)=="number" and math.floor(snap.lesson)~=prev_l then
              load_local_lesson(snap.lesson)
            end
            model.feed(s,snap,now)
          end
          if d.audio_cmd then model.feed(s,{heartbeat=true,audio_cmd=d.audio_cmd},now) end
        else
          model.feed(s,d,now)
        end
      end
    end

    if s.clip_seq>s.active_clip_seq then
      s.active_clip_seq=s.clip_seq
      s.waiting_next_chunk=false
      if s.playing then
        if s.audio_target=="bt" then
          stop_clip()
        else
          play_clip_file()
        end
        s.play_started_ms=now
      else
        stop_clip()
        if s.tts_pending then
          s.tts_pending=false
          play_clip_file()
        end
      end
    end

    if s.playing and s.audio_target~="bt" and player then
      local pok,pinfo=pcall(player.poll,player)
      if pok and pinfo and pinfo.state=="finished" then
        if s.ab_state=="loop" then
          s.ab_loops=s.ab_loops+1
          s.play_started_ms=now
          play_clip_file()
        elseif not s.waiting_next_chunk then
          local pos,dur=model.current_play_sec(s,now)
          if pos<dur-2 and s.play_clip_end and s.play_clip_end<dur then
            s.waiting_next_chunk=true
            s.play_base_sec=s.play_clip_end
            s.play_started_ms=now
            local tr=model.current_track(s)
            emit_event({
              type="audio_play",
              lesson=s.lesson,
              track_index=tr.index,
              start_sec=math.floor(s.play_base_sec),
              end_sec=dur,
              ab_state="off",
              single_loop=(s.single_loop~=false),
              volume=s.volume,
              audio_target=s.audio_target
            },now)
          elseif s.single_loop~=false then
            s.play_base_sec=0
            s.play_started_ms=now
            if (tonumber(s.play_clip_end) or dur)>=dur and dur<=36 then
              play_clip_file()
            else
              s.waiting_next_chunk=true
              local tr=model.current_track(s)
              emit_event({
                type="audio_play",
                lesson=s.lesson,
                track_index=tr.index,
                start_sec=0,
                end_sec=dur,
                ab_state="off",
                single_loop=true,
                volume=s.volume,
                audio_target=s.audio_target
              },now)
            end
          else
            s.playing=false
            s.play_base_sec=0
            stop_clip()
          end
        end
      end
    elseif s.playing and s.audio_target=="bt" and s.ab_state~="loop" then
      local pos,dur=model.current_play_sec(s,now)
      local clip_end=tonumber(s.play_clip_end) or dur
      if pos>=math.min(dur,clip_end) then
        if s.single_loop~=false then
          local was_partial=(s.play_base_sec>1)
          s.play_base_sec=0
          s.play_started_ms=now
          if was_partial then
            local tr=model.current_track(s)
            emit_event({
              type="audio_play",
              lesson=s.lesson,
              track_index=tr.index,
              start_sec=0,
              end_sec=dur,
              ab_state="off",
              single_loop=true,
              volume=s.volume,
              audio_target=s.audio_target
            },now)
          end
        else
          s.playing=false
          s.play_base_sec=0
        end
      end
    end

    local act,ev=model.touch(s,screen:touch().points[1],now)
    if act=="exit" then break end
    if act then
      haptic()
      handle_action_side_effects(act,now)
      if ev then emit_event(ev,now) end
    end

    if gpio then
      local l_lv,r_lv=gpio.get_level(11),gpio.get_level(10)
      if l_lv==1 and r_lv==1 then buttons_armed=true end
      if buttons_armed then
        local bact,bev=model.buttons(s,l_lv,r_lv,now)
        if bact then
          haptic()
          handle_action_side_effects(bact,now)
          if bev then emit_event(bev,now) end
        end
      end
    end

    if strip then
      local px=model.pixels(s,now,cfg.indicator_brightness or 18)
      local sig=json.encode(px)
      if sig~=last_led_sig then
        for i,c in ipairs(px) do strip:set_pixel(i-1,c[1],c[2],c[3]) end
        strip:refresh()
        last_led_sig=sig
      end
    end

    local pos_sec=model.current_play_sec(s,now)
    local key=table.concat({
      s.page,s.lesson,s.data_seq or 0,s.flash_cat,s.card_index,s.card_flipped and 1 or 0,
      s.quiz_mode,s.quiz_index,s.quiz_selected,
      s.track_slot,s.playing and 1 or 0,pos_sec,s.ab_state,s.ab_a,s.ab_b,s.ab_loops,(s.single_loop~=false) and 1 or 0,
      s.volume,s.brightness,s.leds_muted and 1 or 0,s.haptic_enabled and 1 or 0,s.slow_tts and 1 or 0,
      s.audio_target or "esp32",s.bt_connected and 1 or 0,s.bt_name or "",
      s.bt_modal and 1 or 0,s.bt_scanning and 1 or 0,s.bt_page or 1,#(s.bt_devices or {}),
      model.linked(s,now) and 1 or 0
    },"|")

    if key~=last_key and now-last_draw>=100 then
      local t0=system.millis()
      render.draw(screen,s,now)
      local dt=system.millis()-t0
      frames=frames+1;total_ms=total_ms+dt;max_ms=math.max(max_ms,dt)
      last_key=key;last_draw=now
    end

    if now-last_health>=1000 then
      local hraw=json.encode({
        page=s.page,
        lesson=s.lesson,
        flash_cat=s.flash_cat,
        quiz_mode=s.quiz_mode,
        playing=s.playing,
        play_sec=pos_sec,
        ab_state=s.ab_state,
        ab_a=s.ab_a,
        ab_b=s.ab_b,
        ab_loops=s.ab_loops,
        single_loop=(s.single_loop~=false),
        volume=s.volume,
        brightness=s.brightness,
        audio_target=s.audio_target,
        bt_connected=s.bt_connected,
        bt_name=s.bt_name,
        bt_devices_count=#(s.bt_devices or {}),
        linked=model.linked(s,now),
        frames=frames,
        mean_draw_ms=total_ms/math.max(1,frames),
        max_draw_ms=max_ms,
        uptime_ms=now,
        version="1.4.0"
      })
      local ok=thread.sync.queue_send("minna_nihongo_health",hraw,0)
      if not ok then
        thread.sync.queue_recv("minna_nihongo_health",0)
        thread.sync.queue_send("minna_nihongo_health",hraw,0)
      end
      last_health=now
    end

    if args and args.smoke_ms and now-began>=args.smoke_ms then break end
    delay.delay_ms(20)
  end
end

local ok,err=xpcall(run,debug.traceback)
cleanup()
if owns_display then
  pcall(function() dofile(root.."/scripts/ownership.lua").deactivate(thread,storage) end)
end
if not ok then print("[minna_nihongo] failed "..tostring(err));error(err) end
print(string.format("[minna_nihongo] closed frames=%d mean_draw_ms=%.1f",frames,total_ms/math.max(1,frames)))
