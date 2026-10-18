-- Coordinates screen/LED/button ownership and the 4-job firmware limit with ai_mac_mini & ai_badge.
local M={}
local own="/nand/apps/minna_nihongo/scripts/"
local mini="/nand/apps/ai_mac_mini/scripts/"
local air="/nand/apps/ai_badge/scripts/"
local pomo="/nand/apps/pomodoro_clock/scripts/"

local function matches(info,path)
  return type(info)=="string" and (info.."\n"):find("path="..path.."\n",1,true)~=nil
end

function M.jobs(thread,path)
  local ok,listing=thread.list("running")
  if not ok or type(listing)~="string" then return {} end
  local result={}
  for line in listing:gmatch("[^\n]+") do
    if matches(line,path) then result[#result+1]=line:match("^(.-) | ") end
  end
  return result
end

local function stop(thread,path,timeout)
  local jobs=M.jobs(thread,path)
  for _,id in ipairs(jobs) do
    local ok,info=thread.get(id)
    if ok and matches(info,path) then
      pcall(thread.stop,id,timeout or 1500)
    end
  end
  return true
end

function M.activate(thread)
  pcall(thread.sync.queue_send,"ai_mac_mini_lifecycle","exit",0)
  pcall(thread.sync.queue_send,"ai_badge_lifecycle","exit",0)
  stop(thread,mini.."main.lua",1500)
  stop(thread,mini.."indicator_worker.lua",1200)
  stop(thread,air.."main.lua",1500)
  stop(thread,air.."indicator_worker.lua",1200)
  stop(thread,pomo.."main.lua",1200)
end

function M.deactivate(thread,storage)
  -- Do not auto-spawn background indicator workers on exit so the 4-job pool always keeps a free slot for desktop launcher taps.
  return true
end

return M
