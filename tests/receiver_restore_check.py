"""Exercise the real Lua receiver's explicit handoff with native modules mocked."""
from pathlib import Path
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
lua = LuaRuntime(unpack_returned_tuples=True)
owner = lua.execute((ROOT / "app/scripts/ownership.lua").read_text())
lua.globals().ownership = owner
lua.execute('''
handlers={}; starts={}; paths={}; readable=true
package.preload.http_server=function() return {app=function()
  return {post=function(self,p,fn) handlers[p]=fn end,get=function() end,serve_forever=function() end}
end} end
package.preload.storage=function() return {
  get_root_dir=function() return "/nand" end,
  join_path=function(...) return table.concat({...},"/") end,
  read_file=function() return "config" end,
  exists=function() return true end
} end
package.preload.json=function() return {decode=function(x)
  if x=="config" then return {token="t"} end
  return x
end} end
package.preload.system=function() return {millis=function() return 1 end} end
package.preload.capability=function() return {} end
package.preload.thread=function() return {
  sync={queue_create=function() return true end},
  list=function()
    if not readable then return false,"" end
    local lines={}
    for i,p in ipairs(paths) do lines[i]=i.." | running | path="..p end
    return true,table.concat(lines,"\\n")
  end,
  start=function(path,args,opts)
    starts[#starts+1]={path=path,opts=opts}
    paths[#paths+1]=path
    return true,"started"
  end
} end
dofile=function(path)
  if path:find("ownership.lua",1,true) then return ownership end
  return {validate=function() return true end}
end
''')
lua.execute((ROOT / "app/scripts/receiver.lua").read_text())
assert len(lua.globals().starts) == 0, "Receiver startup must remain passive"
callback = lua.globals().handlers["/control"]
own = "/nand/apps/minna_nihongo/scripts/receiver.lua"
air = "/nand/apps/ai_badge/scripts/receiver.lua"
indicator = "/nand/apps/ai_badge/scripts/indicator_worker.lua"
badge = "/nand/apps/ai_mac_mini/scripts/receiver.lua"


def call(paths, token="t"):
    lua.globals().paths = lua.table_from(paths)
    lua.globals().starts = lua.table()
    return callback(lua.table_from({"body": lua.table_from({
        "token": token, "action": "restore_badge_receiver", "path": "/nand/ignored.lua"
    })}))


result = call([own, air, indicator])
assert result.json.ok
assert len(lua.globals().starts) == 1
start = lua.globals().starts[1]
assert start.path == badge and start.opts.replace is False and start.opts.timeout_ms == 0
assert call([own, air, indicator, "/nand/other.lua"]).json.state == "slots_full"
assert len(lua.globals().starts) == 0
assert call([own, air], token="wrong").status == 403
assert len(lua.globals().starts) == 0
assert call([own, air, badge]).json.state == "already_running"
assert len(lua.globals().starts) == 0
lua.globals().readable = False
assert call([own, air]).json.state == "jobs_unknown"
assert len(lua.globals().starts) == 0
print("Lua receiver: passive startup, authorization, fixed path, slot limit, existing job and unknown state OK")
