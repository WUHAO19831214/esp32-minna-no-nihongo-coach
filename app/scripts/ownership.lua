-- Coordinates screen/LED/button ownership and the 4-job firmware limit with ai_mac_mini & ai_badge.
local M={}
local own="/nand/apps/minna_nihongo/scripts/"
local mini="/nand/apps/ai_mac_mini/scripts/"
local air="/nand/apps/ai_badge/scripts/"

local function matches(info,path)
  return type(info)=="string" and (info.."\n"):find("path="..path.."\n",1,true)~=nil
end

function M.jobs(thread,path)
  local ok,listing=thread.list("running")
  if not ok then return {} end
  local result={}
  for line in listing:gmatch("[^\n]+") do
    if matches(line,path) then result[#result+1]=line:match("^(.-) | ") end
  end
  return result
end

local function stop(thread,path)
  local jobs=M.jobs(thread,path)
  for _,id in ipairs(jobs) do
    local ok,info=thread.get(id)
    if ok and matches(info,path) then
      thread.stop(id,2000)
    end
  end
  return true
end

function M.activate(thread)
  stop(thread,mini.."main.lua")
  stop(thread,mini.."indicator_worker.lua")
  stop(thread,air.."main.lua")
  stop(thread,air.."indicator_worker.lua")
end

function M.deactivate(thread,storage)
  local mini_rec=mini.."receiver.lua"
  if #M.jobs(thread,mini_rec)==0 and storage.exists(mini_rec) then
    thread.start(mini_rec,{},{name="ai_mac_mini_receiver",exclusive="ai_mac_mini_receiver",replace=false,timeout_ms=0})
  end
  local mini_ind=mini.."indicator_worker.lua"
  if #M.jobs(thread,mini_ind)==0 and storage.exists(mini_ind) then
    thread.start(mini_ind,{},{name="ai_mac_mini_indicators",exclusive="ai_mac_mini_indicators",replace=false,timeout_ms=0})
  end
end

return M
