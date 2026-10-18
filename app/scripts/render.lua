local R={fonts={}}
local C={
  bg=0xFF080D14,
  panel=0xFF131D2B,
  card=0xFF1A2638,
  border=0xFF28384E,
  white=0xFFF0F5FC,
  dim=0xFF8A99AE,
  teal=0xFF5CE6C0,
  blue=0xFF7CB2FF,
  orange=0xFFFFAD75,
  red=0xFFFF6E7A,
  green=0xFF42E39B,
  dark=0xFF0A111A
}

function R.init(display,root)
  R.metrics=dofile(root.."/scripts/font_metrics.lua")
  R.model=dofile(root.."/scripts/model.lua")
  R.fonts[16]=display.load_font(root.."/assets/cjk-16.dfn")
  R.fonts[20]=display.load_font(root.."/assets/cjk-20.dfn")
  R.fonts[26]=display.load_font(root.."/assets/cjk-26.dfn")
  R.fonts[40]=display.load_font(root.."/assets/rounded-40.dfn")
end

function R.close()
  for _,f in pairs(R.fonts) do pcall(f.close,f) end
  R.fonts={}
end

local function char_adv(code,size)
  local m=R.metrics[size]
  if code<128 then
    return (m and m.advance and m.advance[code]) or (size//2)
  end
  return (m and m.cjk_advance) or size
end

local function measure(v,size)
  v=tostring(v or "")
  local n=0
  local ok=pcall(function()
    for _,code in utf8.codes(v) do
      n=n+char_adv(code,size)
    end
  end)
  if not ok then n=#v*(size//2) end
  return n
end

local function truncate_utf8(v,size,maxwidth)
  v=tostring(v or "")
  if not maxwidth or measure(v,size)<=maxwidth then return v end
  local out={}
  local w=0
  local dots=char_adv(46,size)*2
  for _,code in utf8.codes(v) do
    local cw=char_adv(code,size)
    if w+cw+dots>maxwidth then break end
    out[#out+1]=utf8.char(code)
    w=w+cw
  end
  out[#out+1]=".."
  return table.concat(out)
end

local function text(sc,x,y,v,size,color,maxwidth)
  x=math.floor(x);y=math.floor(y)
  v=truncate_utf8(v,size,maxwidth)
  local f=R.fonts[size] or R.fonts[20]
  local col=color or C.white
  for _,code in utf8.codes(v) do
    sc:text(x,y,utf8.char(code),{font=f,color=col})
    x=x+char_adv(code,size)
  end
end

local function center(sc,y,v,size,color,maxwidth)
  v=truncate_utf8(v,size,maxwidth or 440)
  local w=measure(v,size)
  text(sc,(480-w)//2,y,v,size,color)
end

local function wrap_text(sc,x,y,v,size,color,maxwidth,maxlines,line_gap)
  v=tostring(v or "")
  local line=""
  local lw=0
  local row=0
  for _,code in utf8.codes(v) do
    local ch=utf8.char(code)
    local cw=char_adv(code,size)
    if lw+cw>maxwidth and #line>0 then
      row=row+1
      if row>=maxlines then
        text(sc,x,y+(row-1)*line_gap,line.."..",size,color,maxwidth)
        return row
      end
      text(sc,x,y+(row-1)*line_gap,line,size,color)
      line=ch;lw=cw
    else
      line=line..ch;lw=lw+cw
    end
  end
  if #line>0 and row<maxlines then
    row=row+1
    text(sc,x,y+(row-1)*line_gap,line,size,color,maxwidth)
  end
  return row
end

local function pill(sc,x,y,w,h,label,size,active,active_bg,active_fg)
  x=math.floor(x);y=math.floor(y);w=math.floor(w);h=math.floor(h)
  local bg=active and (active_bg or C.teal) or C.panel
  local fg=active and (active_fg or C.dark) or C.white
  sc:fill_round_rect(x,y,w,h,10,bg)
  local tw=measure(label,size)
  local lh=(R.metrics[size] and R.metrics[size].height) or size
  text(sc,x+math.max(4,(w-tw)//2),y+math.max(2,(h-lh)//2),label,size,fg,w-8)
end

local function draw_header(sc,s,linked)
  local lid=string.format("L%02d 第%d课",s.lesson,s.lesson)
  pill(sc,12,8,38,36,"<",20,false,C.panel,C.teal)
  pill(sc,54,8,100,36,lid,16,true,0xFF1D3248,C.teal)
  pill(sc,158,8,38,36,">",20,false,C.panel,C.teal)
  sc:fill_circle(210,26,4,linked and C.teal or C.orange)
  text(sc,220,17,linked and "大家的日语" or "大家的日语(离线)",16,C.white,122)
  pill(sc,346,8,80,36,"发音",16,false,0xFF1F364D,C.blue)
  pill(sc,432,8,36,36,"X",20,false,C.panel,C.dim)
end

local function draw_flashcards(sc,s)
  local M=R.model
  pill(sc,20,56,136,34,"单词 VOCAB",16,s.flash_cat=="vocab",C.teal,C.dark)
  pill(sc,164,56,136,34,"语法 GRAM",16,s.flash_cat=="grammar",C.blue,C.dark)
  pill(sc,308,56,152,34,"例句 SENT",16,s.flash_cat=="sentence",C.orange,C.dark)

  local card,idx,total=M.current_card(s)
  sc:fill_round_rect(20,98,440,250,16,C.panel)
  text(sc,36,110,card.tag or "词句卡片",16,C.teal,280)
  local count_str=string.format("%d / %d",idx,total)
  text(sc,444-measure(count_str,16),110,count_str,16,C.dim)

  wrap_text(sc,36,142,card.front or "",26,C.white,408,2,32)
  text(sc,36,210,card.sub or "",20,C.blue,408)
  sc:line(36,242,444,242,C.border)

  if s.card_flipped then
    wrap_text(sc,36,254,"释义: "..(card.back or ""),20,C.green,408,2,26)
    if card.detail and #card.detail>0 then
      text(sc,36,314,card.detail,16,C.orange,408)
    end
  else
    center(sc,274,"[ 点击卡片 或 按右键KEY1 翻面并朗读 ]",16,C.dim,400)
    center(sc,306,"按左键KEY2 切换下一张",16,C.dim,400)
  end

  pill(sc,20,358,128,52,"< 上一张",20,false)
  pill(sc,156,358,168,52,s.card_flipped and "再读一次" or "翻面/发音",20,true,C.teal,C.dark)
  pill(sc,332,358,128,52,"下一张 >",20,false)
end

local function draw_quiz(sc,s)
  local M=R.model
  local modes={{"fill_blank","填空"},{"correction","改错"},{"translate_zh_to_ja","中译日"},{"review","错题本"}}
  for i,m in ipairs(modes) do
    pill(sc,20+(i-1)*112,56,104,34,m[2],16,s.quiz_mode==m[1],C.blue,C.dark)
  end

  local q,idx,total=M.current_quiz(s)
  sc:fill_round_rect(20,96,440,98,14,C.panel)
  local hdr=string.format("[%s] %d/%d · %s",q.mode_label or "练习",idx,total,q.target_label or "语法")
  text(sc,34,104,hdr,16,C.teal,412)
  text(sc,34,128,q.prompt or "",20,C.white,412)
  text(sc,34,160,"提示: "..(q.hint or ""),16,C.dim,412)

  local opts=q.options or {"选项A","选项B","选项C"}
  local correct_idx=tonumber(q.correct) or 1
  for i=1,3 do
    local y=202+(i-1)*52
    local bg=C.card
    local fg=C.white
    local prefix=string.format("%d. ",i)
    if s.quiz_selected>0 then
      if i==correct_idx then
        bg=0xFF154538;fg=C.green;prefix="[○] "
      elseif i==s.quiz_selected then
        bg=0xFF4A1C24;fg=C.red;prefix="[×] "
      end
    end
    sc:fill_round_rect(20,y,440,46,12,bg)
    text(sc,36,y+12,prefix..tostring(opts[i] or ""),20,fg,408)
  end

  sc:fill_round_rect(20,360,304,52,12,C.panel)
  if s.quiz_selected>0 then
    local msg=(s.quiz_selected==correct_idx and "回答正确! " or "答错了: ")..(q.explanation or "")
    wrap_text(sc,30,366,msg,16,s.quiz_selected==correct_idx and C.green or C.orange,284,2,20)
  else
    text(sc,32,376,"点选选项作答，自动记入学情",16,C.dim,280)
  end
  pill(sc,332,360,128,52,"下一题 >",20,true,C.teal,C.dark)
end

local function draw_repeater(sc,s,now)
  local M=R.model
  local tr,idx,total=M.current_track(s)
  local pos,dur=M.current_play_sec(s,now)

  pill(sc,20,56,64,34,"< 轨",16,false)
  local tr_label=string.format("%s (%d/%d)",tr.title or tr.section or "音频",idx,total)
  pill(sc,90,56,300,34,tr_label,16,true,0xFF1C2D42,C.white)
  pill(sc,396,56,64,34,"轨 >",16,false)

  sc:fill_round_rect(20,96,440,132,16,C.panel)
  local vol_str=(tonumber(s.volume) or 80)==0 and "静音" or (tostring(s.volume or 80).."%")
  local out_lbl=(s.audio_target=="bt") and "蓝牙耳机" or "机身喇叭"
  local state_txt=s.ab_state=="loop" and ("AB 复读循环中 · "..out_lbl.." "..vol_str)
    or (s.ab_state=="a_set" and "已标起点 A，请按 B 点或右键开启复读")
    or (s.playing and ("正在播放 · "..out_lbl.." "..vol_str) or ("已暂停 · "..out_lbl.." "..vol_str))
  local state_col=s.ab_state=="loop" and C.orange or (s.playing and C.teal or C.dim)
  text(sc,36,106,state_txt,16,state_col,408)

  local pos_str=M.fmt_sec(pos)
  local dur_str="/ "..M.fmt_sec(dur)
  text(sc,36,132,pos_str,40,C.white)
  text(sc,182,144,dur_str,20,C.dim)
  local sl_on=(s.single_loop~=false)
  if s.ab_state=="loop" then
    local loop_badge=string.format("AB:%s-%s",M.fmt_sec(s.ab_a),M.fmt_sec(s.ab_b))
    pill(sc,284,132,164,38,loop_badge,16,true,C.orange,C.dark)
  else
    pill(sc,284,132,164,38,sl_on and "单曲循环: 开" or "单曲循环: 关",16,sl_on,C.teal,C.dark)
  end

  -- Timeline & A-B Region Bar (x=36..444, w=408)
  local bx,by,bw,bh=36,190,408,14
  sc:fill_round_rect(bx,by,bw,bh,7,0xFF283548)
  if s.ab_state~="off" then
    local ax=bx+math.floor((s.ab_a/math.max(1,dur))*bw)
    if s.ab_state=="loop" and s.ab_b>s.ab_a then
      local b_x=bx+math.floor((s.ab_b/math.max(1,dur))*bw)
      sc:fill_round_rect(ax,by,math.max(6,b_x-ax),bh,6,0xFF5A4228)
      sc:fill_rect(b_x-2,by-6,4,bh+12,C.orange)
    end
    sc:fill_rect(ax-2,by-6,4,bh+12,C.teal)
  end
  local fill_w=math.max(8,math.floor((pos/math.max(1,dur))*bw))
  sc:fill_round_rect(bx,by,fill_w,bh,7,s.ab_state=="loop" and C.orange or C.teal)
  sc:fill_circle(bx+math.min(bw,fill_w),by+bh//2,9,C.white)

  -- Row 1: Set A / Set B / Clear AB
  local a_lbl=s.ab_state~="off" and ("A点 "..M.fmt_sec(s.ab_a)) or "设 A 起点"
  local b_lbl=s.ab_state=="loop" and ("B点 "..M.fmt_sec(s.ab_b)) or "设 B 终点"
  pill(sc,20,236,142,50,a_lbl,20,s.ab_state~="off",C.teal,C.dark)
  pill(sc,170,236,142,50,b_lbl,20,s.ab_state=="loop",C.orange,C.dark)
  pill(sc,320,236,140,50,"清除 AB",20,false)

  -- Row 2: Fine-tune A/B by 1 second
  pill(sc,20,294,102,44,"A -1s",16,false)
  pill(sc,130,294,102,44,"A +1s",16,false)
  pill(sc,248,294,102,44,"B -1s",16,false)
  pill(sc,358,294,102,44,"B +1s",16,false)

  -- Row 3: Transport
  pill(sc,20,350,94,58,"-3秒",20,false)
  local play_lbl=s.playing and "暂停播放" or ((s.audio_target=="bt") and "播放 (蓝牙耳机)" or "播放 (机身喇叭)")
  pill(sc,122,350,236,58,play_lbl,20,true,s.playing and C.orange or C.teal,C.dark)
  pill(sc,366,350,94,58,"+3秒",20,false)
end

local function draw_lessons(sc,s)
  for i=1,25 do
    local row=(i-1)//5
    local col=(i-1)%5
    local x=20+col*89
    local y=58+row*47
    local active=(s.lesson==i)
    pill(sc,x,y,84,41,string.format("第%02d课",i),16,active,C.teal,C.dark)
  end

  local sum=s.data.summary or {}
  sc:fill_round_rect(20,300,440,114,14,C.panel)
  local line1=string.format("学情档案: 累计交互 %d 次 · 正确率 %d%% · 待复习 %d",
    tonumber(sum.total_interactions) or 0,
    tonumber(sum.accuracy) or 100,
    tonumber(sum.pending_reviews) or 0)
  text(sc,34,312,line1,16,C.teal,412)
  text(sc,34,340,"GNN 推荐重点: "..tostring(sum.gnn_focus or "判断句"),20,C.white,412)
  text(sc,34,372,tostring(sum.gnn_action or "点此直接进入错题巩固练习"),16,C.orange,412)
end

local function draw_bt_modal(sc,s)
  local is_bt=(s.audio_target=="bt")
  local devs=s.bt_devices or {}
  local max_p=math.max(1,math.ceil(#devs/4))
  local pg=math.max(1,math.min(max_p,tonumber(s.bt_page) or 1))

  -- Top bar (y=56..94)
  pill(sc,20,56,154,38,s.bt_scanning and "正在扫描周边.." or "扫描周边蓝牙",16,true,s.bt_scanning and C.orange or C.teal,C.dark)
  pill(sc,182,56,168,38,is_bt and "输出: 蓝牙音频" or "输出: 机身喇叭",16,is_bt,C.blue,C.dark)
  pill(sc,358,56,102,38,"返回设定",16,false)

  if #devs==0 then
    sc:fill_round_rect(20,104,440,244,14,C.panel)
    center(sc,196,s.bt_scanning and "正在搜索周边蓝牙音频设备，请稍候..." or "暂未发现设备，请点击「扫描周边蓝牙」",20,C.teal,400)
    center(sc,236,"请确保蓝牙音箱或耳机处于开机可发现状态",16,C.dim,400)
  else
    for row=1,4 do
      local idx=(pg-1)*4+row
      local dev=devs[idx]
      local ry=102+(row-1)*62
      if dev then
        local conn=(dev.connected==true)
        local paired=(dev.paired==true)
        local row_bg=conn and 0xFF13332D or C.panel
        sc:fill_round_rect(20,ry,440,56,12,row_bg)
        local dot_col=conn and C.green or (paired and C.blue or C.orange)
        sc:fill_circle(36,ry+28,5,dot_col)
        text(sc,48,ry+6,tostring(dev.name or dev.address or "蓝牙音频设备"),20,conn and C.green or C.white,268)
        local st_prefix=conn and "已连接 · 音频输出  " or (paired and "已配对 · 点击连接  " or "周边新设备 · 点击配对  ")
        text(sc,48,ry+32,st_prefix..tostring(dev.address or ""),16,conn and C.teal or C.dim,268)
        local btn_lbl=conn and "已连·断开" or (paired and "连接播放" or "配对连接")
        local btn_bg=conn and C.green or (paired and C.blue or C.orange)
        pill(sc,324,ry+8,126,40,btn_lbl,16,true,btn_bg,C.dark)
      else
        sc:fill_round_rect(20,ry,440,56,12,0xFF0E1622)
        text(sc,36,ry+18,"— 空闲设备槽位 (可点击上方扫描周边蓝牙) —",16,0xFF46566E,408)
      end
    end
  end

  -- Bottom bar (y=360..414)
  pill(sc,20,360,112,52,string.format("< 页 %d/%d",pg,max_p),16,false)
  pill(sc,140,360,200,52,"试听当前蓝牙音频",20,true,C.teal,C.dark)
  pill(sc,348,360,112,52,"下一页 >",16,false)
end

local function draw_settings(sc,s)
  if s.bt_modal then
    draw_bt_modal(sc,s)
    return
  end

  local vol=tonumber(s.volume) or 80
  local bri=tonumber(s.brightness) or 75
  local is_bt=(s.audio_target=="bt")
  local bt_name=tostring(s.bt_name or "未选择设备")
  local dev_count=#(s.bt_devices or {})

  -- Row 1: Volume Control (y=56..136)
  sc:fill_round_rect(20,56,440,80,14,C.panel)
  local vol_txt=vol==0 and "音量: 已静音 (0%) · 侧键可调" or string.format("播放音量: %d%% (右键+ / 左键-)",vol)
  text(sc,32,62,vol_txt,16,vol==0 and C.orange or C.teal,262)
  sc:fill_round_rect(306,66,138,10,5,0xFF233246)
  if vol>0 then
    sc:fill_round_rect(306,66,math.max(10,math.floor(138*vol/100)),10,5,C.teal)
  end
  pill(sc,30,88,92,40,vol==0 and "恢复音" or "静音",16,vol==0,C.orange,C.dark)
  pill(sc,132,88,102,40,"音量 -",20,false)
  pill(sc,244,88,102,40,"音量 +",20,true,C.teal,C.dark)
  pill(sc,356,88,94,40,"试听",20,false)

  -- Row 2: Bluetooth Audio Selection & Pairing (y=142..226)
  sc:fill_round_rect(20,142,440,84,14,C.panel)
  sc:fill_circle(36,158,5,s.bt_connected and C.green or (is_bt and C.blue or C.dim))
  local bt_hdr=string.format("蓝牙音频: %s (%s · %d台可选)",bt_name,s.bt_connected and "已连接" or "待连接",dev_count)
  text(sc,48,149,bt_hdr,16,s.bt_connected and C.green or C.blue,396)
  pill(sc,30,174,206,44,is_bt and "输出: 蓝牙音频" or "输出: 机身喇叭",20,is_bt,C.blue,C.dark)
  pill(sc,244,174,206,44,"搜索 / 选择蓝牙 >",20,true,C.teal,C.dark)

  -- Row 3: Screen Brightness (y=234..306)
  sc:fill_round_rect(20,234,440,72,14,C.panel)
  text(sc,32,244,string.format("屏幕背光亮度: %d%%",bri),16,C.blue,200)
  sc:fill_round_rect(32,272,196,12,6,0xFF233246)
  sc:fill_round_rect(32,272,math.max(14,math.floor(196*bri/100)),12,6,C.blue)
  pill(sc,244,248,100,44,"亮度 -",20,false)
  pill(sc,352,248,98,44,"亮度 +",20,false)

  -- Row 4: Peripheral switches (y=314..364)
  pill(sc,20,314,214,48,s.leds_muted and "侧边灯: 已关闭" or "侧边灯: 已开启",20,not s.leds_muted,C.teal,C.dark)
  pill(sc,246,314,214,48,s.haptic_enabled and "触控震动: 开启" or "触控震动: 关闭",20,s.haptic_enabled,C.blue,C.dark)

  -- Row 5: TTS Speed & Exit (y=370..420)
  pill(sc,20,370,214,48,s.slow_tts and "发音语速: 慢速" or "发音语速: 标准",20,s.slow_tts,C.orange,C.dark)
  pill(sc,246,370,214,48,"退出回桌面",20,false)
end

function R.draw(sc,s,now)
  local linked=R.model.linked(s,now)
  sc:begin({clear=C.bg})
  draw_header(sc,s,linked)

  if s.page==5 then
    draw_settings(sc,s)
  elseif s.page==1 then
    draw_flashcards(sc,s)
  elseif s.page==2 then
    draw_quiz(sc,s)
  elseif s.page==3 then
    draw_repeater(sc,s,now)
  else
    draw_lessons(sc,s)
  end

  sc:line(16,426,464,426,C.border)
  local tabs={"词句","练习","复读","选课","设定"}
  for i,name in ipairs(tabs) do
    local x=(i-1)*93+10
    if s.page==i then
      sc:fill_round_rect(x,434,86,38,10,0xFF1F364C)
    end
    local tw=measure(name,20)
    text(sc,x+(86-tw)//2,442,name,20,s.page==i and C.teal or C.dim)
  end
  sc:present()
end

return R
