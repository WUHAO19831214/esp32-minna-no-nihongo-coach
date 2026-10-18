local http=require("http_server")
local storage=require("storage")
local json=require("json")
local system=require("system")
local thread=require("thread")
local capability=require("capability")
local root=storage.join_path(storage.get_root_dir(),"apps","minna_nihongo")
local model=dofile(root.."/scripts/model.lua")
local cfg=json.decode(storage.read_file(root.."/config.json"))
local app=http.app("minna_nihongo")

for _,q in ipairs({
  {"minna_nihongo_feed",4096},
  {"minna_nihongo_health",2048},
  {"minna_nihongo_lifecycle",32}
}) do
  local ok,err=thread.sync.queue_create(q[1],{depth=2,item_size=q[2]})
  assert(ok or err=="exists",err)
end
local qok,qerr=thread.sync.queue_create("minna_nihongo_events",{depth=8,item_size=512})
assert(qok or qerr=="exists",qerr)

dofile(root.."/scripts/ownership.lua").activate(thread)

local latest,health,pending_event

local function authorize(req)
  local ok,d=pcall(json.decode,req.body)
  if not ok or type(d)~="table" or d.token~=cfg.token then return nil end
  return d
end

app:post("/feed",function(req)
  local d=authorize(req)
  if not d or type(d.data)~="table" then return {status=403,json={ok=false}} end
  if not model.validate(d.data) then return {status=400,json={ok=false,error="Invalid payload"}} end
  local raw=json.encode(d.data)
  latest=d.data
  if not d.data.reload_snapshot and not d.data.heartbeat and (
    type(d.data.cards_by_cat)=="table"
    or (type(d.data.cards)=="table" and #d.data.cards>0)
    or (type(d.data.quizzes)=="table" and #d.data.quizzes>0)
    or (type(d.data.tracks)=="table" and #d.data.tracks>0)
  ) then
    assert(storage.write_file(root.."/snapshot.json.tmp",raw))
    assert(storage.rename(root.."/snapshot.json.tmp",root.."/snapshot.json"))
  end
  local msg=raw
  if #msg>3900 then
    msg=json.encode({reload_snapshot=true,lesson=d.data.lesson,audio_cmd=d.data.audio_cmd})
  end
  local ok,err=thread.sync.queue_send("minna_nihongo_feed",msg,0)
  if not ok and err=="timeout" then
    thread.sync.queue_recv("minna_nihongo_feed",0)
    ok=thread.sync.queue_send("minna_nihongo_feed",msg,0)
  end
  return {json={ok=true,queued=ok==true}}
end)

app:get("/status",function()
  while true do
    local raw=thread.sync.queue_recv("minna_nihongo_health",0)
    if not raw then break end
    health=json.decode(raw)
  end
  return {json={ok=true,uptime_ms=system.millis(),ui=health,data=latest}}
end)

app:post("/events",function(req)
  local d=authorize(req)
  if not d then return {status=403,json={ok=false}} end
  if pending_event and d.ack==pending_event.id then pending_event=nil end
  while true do
    if not pending_event then
      local raw=thread.sync.queue_recv("minna_nihongo_events",0)
      if not raw then break end
      local ok,ev=pcall(json.decode,raw)
      if ok then pending_event=ev end
    end
    if pending_event then
      if system.millis()-(tonumber(pending_event.created_ms) or 0)<30000 then break end
      pending_event=nil
    end
  end
  return {json={ok=true,event=pending_event}}
end)

app:post("/control",function(req)
  local d=authorize(req)
  if not d then return {status=403,json={ok=false}} end
  if d.action=="publish" then
    local ok,out,err=capability.call("publish_app",{app_id="minna_nihongo"},{max_output_bytes=2048})
    return {json={ok=ok,result=out,error=err}}
  elseif d.action=="start" or d.action=="verify" then
    pcall(function() dofile(root.."/scripts/ownership.lua").activate(thread) end)
    local path=root.."/scripts/"..(d.action=="verify" and "verify.lua" or "main.lua")
    local ok,out=thread.start(path,d.args or {},{
      name=d.action=="verify" and "minna_nihongo_verify" or "minna_nihongo_ui",
      exclusive="minna_nihongo_ui",
      replace=true,
      timeout_ms=d.action=="verify" and 25000 or 0
    })
    return {json={ok=ok==true,result=out}}
  elseif d.action=="job" and type(d.id)=="string" then
    local ok,out=thread.get(d.id)
    return {json={ok=ok==true,result=out}}
  elseif d.action=="jobs" then
    local ok,out=thread.list("running")
    return {json={ok=ok==true,result=out}}
  elseif d.action=="stop" then
    local ok=thread.sync.queue_send("minna_nihongo_lifecycle","exit",0)
    return {json={ok=ok==true,result="Exit requested"}}
  end
  return {status=400,json={ok=false}}
end)

print("[minna_nihongo] receiver ready")
app:serve_forever()
