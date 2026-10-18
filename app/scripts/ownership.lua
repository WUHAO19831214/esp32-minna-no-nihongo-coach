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
  -- Signal polite exit to any foreground UI and stop competing UI tasks (main.lua only).
  -- Receivers and indicators are NEVER stopped here.
  pcall(thread.sync.queue_send,"ai_mac_mini_lifecycle","exit",0)
  stop(thread,mini.."main.lua",1500)
end

function M.deactivate(thread,storage)
  -- On deactivation, do not spawn background tasks; leave slots clean.
  return true
end

return M
