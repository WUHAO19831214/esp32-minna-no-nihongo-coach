local M={}

local function clamp(v,lo,hi)
  if v<lo then return lo elseif v>hi then return hi else return v end
end

function M.default_data()
  local vocab_cards={
    {type="vocab",tag="L06 词汇 · 动词",front="読みます",sub="假名: よみます",back="读",detail="読みます (よみます)",speak="読みます"},
    {type="vocab",tag="L06 词汇 · 动词",front="買います",sub="假名: かいます",back="买",detail="買います (かいます)",speak="買います"},
    {type="vocab",tag="L06 词汇 · 副词",front="一緒に",sub="假名: いっしょに",back="一起",detail="一緒に (いっしょに)",speak="一緒に"}
  }
  local gram_cards={
    {type="grammar",tag="L06 语法 · 动作对象",front="名詞を動詞",sub="わたしは本を読みます。",back="用助词「を」表示他动词的直接宾语。",detail="わたしは本を読みます。",speak="わたしは本を読みます。"}
  }
  local sent_cards={
    {type="sentence",tag="L06 例句 · 动作对象",front="わたしは本を読みます。",sub="名詞を動詞",back="我读书。",detail="句型: 名詞を動詞",speak="わたしは本を読みます。"}
  }
  local default_quizzes={
    {
      mode="fill_blank",
      mode_label="填空练习",
      lesson="006",
      prompt="わたしは本（　）読みます。",
      hint="我读书。",
      options={"を","へ","に"},
      correct=1,
      target_label="动作对象",
      explanation="他动词宾语用助词「を」表示。",
      speak="わたしは本を読みます。"
    },
    {
      mode="correction",
      mode_label="纠错重写",
      lesson="006",
      prompt="改错: 京都を行きます。",
      hint="原意: 去京都。",
      options={"京都へ行きます。","京都を行きます。","京都でます。"},
      correct=1,
      target_label="移动方向",
      explanation="移动方向终点使用助词「へ」而非「を」。",
      speak="京都へ行きます。"
    }
  }
  return {
    lesson=6,
    flash_cat="vocab",
    quiz_mode="fill_blank",
    cards_by_cat={vocab=vocab_cards,grammar=gram_cards,sentence=sent_cards},
    quizzes_by_mode={fill_blank=default_quizzes,correction=default_quizzes,translate_zh_to_ja=default_quizzes,review=default_quizzes},
    cards=vocab_cards,
    quizzes=default_quizzes,
    tracks={
      {index=52,title="052 第六课单词",section="单词",duration=224},
      {index=53,title="053 第六课文型",section="文型",duration=26},
      {index=54,title="054 第六课例文",section="例文",duration=51},
      {index=55,title="055 第六课会话",section="会话",duration=39},
      {index=56,title="056 第六课练习A",section="练习A",duration=96}
    },
    summary={
      total_interactions=33,
      accuracy=100,
      mastered_labels=3,
      pending_reviews=0,
      gnn_focus="判断句",
      gnn_action="先复习核心句型，再做3道替换练习"
    }
  }
end

function M.new()
  return {
    page=1,
    prev_page=1,
    lesson=6,
    flash_cat="vocab",
    card_index=1,
    card_flipped=false,
    quiz_mode="fill_blank",
    quiz_index=1,
    quiz_selected=0,
    quiz_feedback_ms=-10000,
    quiz_last_correct=true,
    track_slot=1,
    playing=false,
    play_started_ms=0,
    play_base_sec=0,
    play_clip_end=21,
    waiting_next_chunk=false,
    ab_state="off",
    ab_a=0,
    ab_b=0,
    ab_loops=0,
    single_loop=true,
    clip_seq=0,
    active_clip_seq=0,
    volume=80,
    audio_target="esp32",
    bt_name="BV370",
    bt_connected=false,
    bt_scanning=false,
    bt_modal=false,
    bt_page=1,
    bt_devices={
      {name="BV370",address="f4-4e-fd-0e-11-fd",connected=false,paired=true},
      {name="AirPods Pro",address="2c-76-00-c5-6b-a0",connected=false,paired=true}
    },
    brightness=75,
    leds_muted=false,
    haptic_enabled=true,
    slow_tts=false,
    data_seq=1,
    last_feed=-100000,
    last_touch=0,
    gesture=nil,
    key_l_down=false,
    key_r_down=false,
    both_handled=false,
    data=M.default_data()
  }
end

function M.linked(s,now)
  return now-s.last_feed<18000
end

function M.active_cards(s)
  if type(s.data.cards_by_cat)=="table" then
    local list=s.data.cards_by_cat[s.flash_cat]
    if type(list)=="table" and #list>0 then return list end
  end
  return s.data.cards or {}
end

function M.active_quizzes(s)
  if type(s.data.quizzes_by_mode)=="table" then
    local list=s.data.quizzes_by_mode[s.quiz_mode]
    if type(list)=="table" and #list>0 then return list end
  end
  return s.data.quizzes or {}
end

function M.current_card(s)
  local cards=M.active_cards(s)
  if #cards==0 then
    return {tag="暂无卡片",front="请等待同步",sub="",back="",detail="",speak=""},1,1
  end
  local idx=clamp(s.card_index,1,#cards)
  return cards[idx],idx,#cards
end

function M.current_quiz(s)
  local quizzes=M.active_quizzes(s)
  if #quizzes==0 then
    return {mode_label="练习",prompt="正在加载题目...",hint="",options={"A","B","C"},correct=1,explanation="",speak=""},1,1
  end
  local idx=clamp(s.quiz_index,1,#quizzes)
  return quizzes[idx],idx,#quizzes
end

function M.current_track(s)
  local tracks=s.data.tracks or {}
  if #tracks==0 then
    return {index=1,title="暂无音轨",section="单词",duration=30},1,1
  end
  local idx=clamp(s.track_slot,1,#tracks)
  return tracks[idx],idx,#tracks
end

function M.current_play_sec(s,now)
  local tr=M.current_track(s)
  local dur=math.max(1,tonumber(tr.duration) or 30)
  if not s.playing then
    return clamp(math.floor(s.play_base_sec+0.5),0,dur),dur
  end
  local elapsed=math.max(0,(now-s.play_started_ms)/1000.0)
  if s.ab_state=="loop" and s.ab_b>s.ab_a then
    local span=math.max(1,s.ab_b-s.ab_a)
    local pos=s.ab_a+(elapsed%span)
    return clamp(math.floor(pos+0.5),s.ab_a,s.ab_b),dur
  end
  local pos=s.play_base_sec+elapsed
  if pos>=dur then pos=dur end
  return clamp(math.floor(pos+0.5),0,dur),dur
end

function M.fmt_sec(sec)
  sec=math.max(0,math.floor((tonumber(sec) or 0)+0.5))
  return string.format("%02d:%02d",sec//60,sec%60)
end

function M.validate(d)
  return type(d)=="table" and (
    d.heartbeat==true
    or d.reload_snapshot==true
    or type(d.cards)=="table"
    or type(d.cards_by_cat)=="table"
    or type(d.quizzes)=="table"
    or type(d.tracks)=="table"
    or type(d.audio_cmd)=="table"
    or type(d.bt_devices)=="table"
  )
end

function M.apply_lesson_bundle(s,b)
  if type(b)~="table" then return false end
  if type(b.lesson)=="number" and b.lesson>=1 and b.lesson<=25 then
    s.lesson=math.floor(b.lesson)
  end
  if type(b.cards_by_cat)=="table" then s.data.cards_by_cat=b.cards_by_cat end
  if type(b.quizzes_by_mode)=="table" then s.data.quizzes_by_mode=b.quizzes_by_mode end
  if type(b.cards)=="table" and #b.cards>0 then s.data.cards=b.cards end
  if type(b.quizzes)=="table" and #b.quizzes>0 then s.data.quizzes=b.quizzes end
  if type(b.tracks)=="table" and #b.tracks>0 then s.data.tracks=b.tracks end
  s.card_index=1;s.card_flipped=false
  s.quiz_index=1;s.quiz_selected=0
  s.track_slot=1;s.playing=false;s.ab_state="off";s.ab_a=0;s.ab_b=0;s.ab_loops=0
  s.play_base_sec=0;s.waiting_next_chunk=false
  s.data_seq=(s.data_seq or 0)+1
  return true
end

function M.feed(s,d,now)
  if not M.validate(d) then return false end
  s.last_feed=now
  local changed=false
  if type(d.lesson)=="number" and d.lesson>=1 and d.lesson<=25 and not d.heartbeat then
    local new_l=math.floor(d.lesson)
    if s.lesson~=new_l then
      s.lesson=new_l
      s.card_index=1;s.card_flipped=false
      s.quiz_index=1;s.quiz_selected=0
      s.track_slot=1
      changed=true
    end
  end
  if type(d.flash_cat)=="string" and d.flash_cat~=s.flash_cat then
    s.flash_cat=d.flash_cat;changed=true
  end
  if type(d.quiz_mode)=="string" and d.quiz_mode~=s.quiz_mode then
    s.quiz_mode=d.quiz_mode;changed=true
  end
  if type(d.cards_by_cat)=="table" then
    s.data.cards_by_cat=d.cards_by_cat;changed=true
  end
  if type(d.quizzes_by_mode)=="table" then
    s.data.quizzes_by_mode=d.quizzes_by_mode;changed=true
  end
  if type(d.cards)=="table" and #d.cards>0 then
    s.data.cards=d.cards
    local active=M.active_cards(s)
    s.card_index=clamp(s.card_index,1,math.max(1,#active))
    changed=true
  end
  if type(d.quizzes)=="table" and #d.quizzes>0 then
    s.data.quizzes=d.quizzes
    local active=M.active_quizzes(s)
    s.quiz_index=clamp(s.quiz_index,1,math.max(1,#active))
    changed=true
  end
  if type(d.tracks)=="table" and #d.tracks>0 then
    s.data.tracks=d.tracks
    s.track_slot=clamp(s.track_slot,1,#d.tracks)
    changed=true
  end
  if type(d.summary)=="table" then
    s.data.summary=d.summary;changed=true
  end
  if type(d.bt_name)=="string" and #d.bt_name>0 then
    if d.bt_name~=s.bt_name then s.bt_name=d.bt_name;changed=true end
  end
  if type(d.bt_connected)=="boolean" then
    if d.bt_connected~=s.bt_connected then s.bt_connected=d.bt_connected;changed=true end
  end
  if type(d.bt_scanning)=="boolean" then
    if d.bt_scanning~=s.bt_scanning then s.bt_scanning=d.bt_scanning;changed=true end
  end
  if type(d.bt_devices)=="table" then
    s.bt_devices=d.bt_devices
    local max_p=math.max(1,math.ceil(#s.bt_devices/4))
    s.bt_page=clamp(s.bt_page or 1,1,max_p)
    changed=true
  end
  if type(d.audio_target)=="string" and (d.audio_target=="esp32" or d.audio_target=="bt") then
    if d.audio_target~=s.audio_target then s.audio_target=d.audio_target;changed=true end
  end
  if type(d.audio_cmd)=="table" then
    local cmd=d.audio_cmd
    local seq=tonumber(cmd.seq) or 0
    if seq>s.clip_seq then
      s.clip_seq=seq
      s.waiting_next_chunk=false
      if type(cmd.target)=="string" and (cmd.target=="esp32" or cmd.target=="bt") then
        s.audio_target=cmd.target
      end
      if cmd.action=="play" then
        s.playing=true
        s.play_started_ms=now
        s.play_base_sec=tonumber(cmd.start_sec) or s.play_base_sec
        s.play_clip_end=tonumber(cmd.end_sec) or 30
        if type(cmd.ab_state)=="string" then s.ab_state=cmd.ab_state end
        if cmd.ab_a then s.ab_a=math.floor(tonumber(cmd.ab_a) or 0) end
        if cmd.ab_b then s.ab_b=math.floor(tonumber(cmd.ab_b) or 0) end
        if type(cmd.single_loop)=="boolean" then s.single_loop=cmd.single_loop end
      elseif cmd.action=="tts" then
        -- Word/grammar/sentence read-aloud: one-shot clip, must NOT continue the lesson track.
        s.playing=false
        s.ab_state="off";s.ab_a=0;s.ab_b=0;s.ab_loops=0
        s.play_base_sec=0
        s.waiting_next_chunk=false
        s.tts_pending=(cmd.target~="bt")
      elseif cmd.action=="stop" then
        s.playing=false
      end
      changed=true
    end
  end
  if changed then s.data_seq=(s.data_seq or 0)+1 end
  return true
end

local function make_audio_event(s,now)
  local tr=M.current_track(s)
  local start_s=s.ab_state=="loop" and s.ab_a or s.play_base_sec
  local end_s=s.ab_state=="loop" and s.ab_b or tr.duration
  s.waiting_next_chunk=false
  return {
    type="audio_play",
    lesson=s.lesson,
    track_index=tr.index,
    start_sec=math.floor(start_s),
    end_sec=math.floor(end_s),
    ab_state=s.ab_state,
    single_loop=s.single_loop~=false,
    volume=s.volume,
    audio_target=s.audio_target
  }
end

local function make_speak_event(s,text)
  s.playing=false;s.waiting_next_chunk=false
  return {
    type="speak",
    text=text,
    lesson=s.lesson,
    volume=s.volume,
    slow_tts=s.slow_tts,
    audio_target=s.audio_target
  }
end

function M.trigger_ab_button(s,now)
  local pos,dur=M.current_play_sec(s,now)
  if s.ab_state=="off" then
    s.ab_a=clamp(pos,0,math.max(0,dur-2))
    s.ab_b=0
    s.ab_state="a_set"
    if not s.playing then
      s.playing=true;s.play_started_ms=now;s.play_base_sec=s.ab_a
      return make_audio_event(s,now)
    end
    return nil
  elseif s.ab_state=="a_set" then
    local b=pos
    if b<=s.ab_a+1 then b=math.min(dur,s.ab_a+math.min(6,math.max(2,dur-s.ab_a))) end
    s.ab_b=b
    s.ab_state="loop"
    s.ab_loops=0
    s.playing=true
    s.play_base_sec=s.ab_a
    s.play_started_ms=now
    return make_audio_event(s,now)
  else
    s.ab_state="off"
    s.ab_a=0;s.ab_b=0;s.ab_loops=0
    s.play_base_sec=pos
    s.play_started_ms=now
    if s.playing then return make_audio_event(s,now) end
    return {type="audio_ab_clear"}
  end
end

function M.handle_tap(s,x,y,now)
  -- Top bar (y=0..50)
  if y<=50 then
    if x>=430 then return "exit"
    elseif x>=344 and x<428 then
      if s.page==1 then
        local c=M.current_card(s)
        return "speak",make_speak_event(s,c.speak or c.front)
      elseif s.page==2 then
        local q=M.current_quiz(s)
        return "speak",make_speak_event(s,q.speak or q.prompt)
      else
        s.playing=true;s.play_started_ms=now
        return "audio",make_audio_event(s,now)
      end
    elseif x>=10 and x<=54 then
      s.lesson=s.lesson>1 and (s.lesson-1) or 25
      s.card_index=1;s.card_flipped=false;s.quiz_index=1;s.quiz_selected=0;s.track_slot=1;s.playing=false;s.ab_state="off";s.play_base_sec=0
      s.data_seq=(s.data_seq or 0)+1
      return "lesson",{type="select_lesson",lesson=s.lesson,flash_cat=s.flash_cat,quiz_mode=s.quiz_mode}
    elseif x>=55 and x<=150 then
      s.page=4;return "tab"
    elseif x>=151 and x<=196 then
      s.lesson=s.lesson<25 and (s.lesson+1) or 1
      s.card_index=1;s.card_flipped=false;s.quiz_index=1;s.quiz_selected=0;s.track_slot=1;s.playing=false;s.ab_state="off";s.play_base_sec=0
      s.data_seq=(s.data_seq or 0)+1
      return "lesson",{type="select_lesson",lesson=s.lesson,flash_cat=s.flash_cat,quiz_mode=s.quiz_mode}
    end
    return nil
  end

  -- Bottom navigation tabs (5 tabs: 1=词句, 2=练习, 3=复读, 4=选课, 5=设定)
  if y>=428 then
    local tab=clamp(math.floor((x-8)/93)+1,1,5)
    s.bt_modal=false
    s.page=tab
    return "tab"
  end

  -- Page 1: Flashcards (词句)
  if s.page==1 then
    if y>=54 and y<=94 then
      local cat=x<160 and "vocab" or (x<305 and "grammar" or "sentence")
      if cat~=s.flash_cat then
        s.flash_cat=cat;s.card_index=1;s.card_flipped=false
        s.data_seq=(s.data_seq or 0)+1
        return "cat",{type="select_flash_cat",lesson=s.lesson,flash_cat=cat}
      end
      return "tap"
    elseif y>=100 and y<=350 then
      s.card_flipped=not s.card_flipped
      return "flip"
    elseif y>=356 and y<=422 then
      local _,idx,total=M.current_card(s)
      if x<152 then
        s.card_index=idx>1 and (idx-1) or total
        s.card_flipped=false
        return "prev"
      elseif x<=328 then
        s.card_flipped=true
        local c=M.current_card(s)
        return "speak",make_speak_event(s,c.speak or c.front)
      else
        s.card_index=idx<total and (idx+1) or 1
        s.card_flipped=false
        return "next"
      end
    end
  end

  -- Page 2: Interactive Quiz (练习)
  if s.page==2 then
    if y>=54 and y<=94 then
      local modes={"fill_blank","correction","translate_zh_to_ja","review"}
      local m=modes[clamp(math.floor((x-16)/112)+1,1,4)]
      if m~=s.quiz_mode then
        s.quiz_mode=m;s.quiz_index=1;s.quiz_selected=0
        s.data_seq=(s.data_seq or 0)+1
        return "mode",{type="select_quiz_mode",lesson=s.lesson,quiz_mode=m}
      end
      return "tap"
    elseif y>=202 and y<=356 then
      local opt=clamp(math.floor((y-202)/52)+1,1,3)
      if s.quiz_selected==0 then
        s.quiz_selected=opt
        local q,idx=M.current_quiz(s)
        s.quiz_last_correct=(opt==(tonumber(q.correct) or 1))
        s.quiz_feedback_ms=now
        return "answer",{type="quiz_answer",lesson=s.lesson,quiz_mode=s.quiz_mode,quiz_index=idx,chosen=opt,speak=q.speak,volume=s.volume,slow_tts=s.slow_tts,audio_target=s.audio_target}
      end
      return "tap"
    elseif y>=360 and y<=422 then
      if x>=325 then
        local _,idx,total=M.current_quiz(s)
        s.quiz_index=idx<total and (idx+1) or 1
        s.quiz_selected=0
        return "next"
      else
        local q=M.current_quiz(s)
        return "speak",make_speak_event(s,q.speak or q.prompt)
      end
    end
  end

  -- Page 3: Ximalaya A-B Repeater (复读)
  if s.page==3 then
    local tr,idx,total=M.current_track(s)
    local dur=math.max(1,tonumber(tr.duration) or 30)
    if y>=54 and y<=94 then
      if x<84 then
        s.track_slot=idx>1 and (idx-1) or total
      else
        s.track_slot=idx<total and (idx+1) or 1
      end
      s.play_base_sec=0;s.play_started_ms=now;s.ab_state="off";s.ab_a=0;s.ab_b=0;s.ab_loops=0
      if s.playing then return "track",make_audio_event(s,now) end
      return "track"
    elseif y>=100 and y<=170 and x>=276 and x<=460 then
      s.single_loop=not (s.single_loop~=false)
      s.data_seq=(s.data_seq or 0)+1
      return "prefs",{
        type="toggle_single_loop",
        single_loop=s.single_loop,
        playing=s.playing,
        volume=s.volume,
        audio_target=s.audio_target
      }
    elseif y>=172 and y<=226 and x>=30 and x<=450 then
      local ratio=clamp((x-36)/408.0,0.0,1.0)
      s.play_base_sec=math.floor(ratio*dur)
      s.play_started_ms=now
      if s.ab_state=="loop" and (s.play_base_sec<s.ab_a or s.play_base_sec>s.ab_b) then
        s.ab_state="off"
      end
      s.playing=true
      return "seek",make_audio_event(s,now)
    elseif y>=234 and y<=292 then
      local pos=M.current_play_sec(s,now)
      if x<166 then
        s.ab_a=clamp(pos,0,math.max(0,dur-2))
        if s.ab_b<=s.ab_a then s.ab_state="a_set" else s.ab_state="loop" end
        if s.ab_state=="loop" then
          s.playing=true;s.play_base_sec=s.ab_a;s.play_started_ms=now
          return "ab",make_audio_event(s,now)
        end
        return "ab"
      elseif x<316 then
        local b=pos
        if b<=s.ab_a+1 then b=math.min(dur,s.ab_a+math.min(6,math.max(2,dur-s.ab_a))) end
        s.ab_b=b;s.ab_state="loop";s.ab_loops=0;s.playing=true;s.play_base_sec=s.ab_a;s.play_started_ms=now
        return "ab",make_audio_event(s,now)
      else
        local cur=M.current_play_sec(s,now)
        s.ab_state="off";s.ab_a=0;s.ab_b=0;s.ab_loops=0
        s.play_base_sec=cur;s.play_started_ms=now
        if s.playing then return "ab",make_audio_event(s,now) end
        return "ab",{type="audio_ab_clear"}
      end
    elseif y>=296 and y<=348 then
      if x<126 then s.ab_a=clamp(s.ab_a-1,0,math.max(0,(s.ab_b>0 and s.ab_b-2 or dur-2)))
      elseif x<238 then s.ab_a=clamp(s.ab_a+1,0,math.max(0,(s.ab_b>0 and s.ab_b-1 or dur-2)))
      elseif x<354 then
        if s.ab_b==0 then s.ab_b=math.min(dur,s.ab_a+5) end
        s.ab_b=clamp(s.ab_b-1,s.ab_a+1,dur);s.ab_state="loop"
      else
        if s.ab_b==0 then s.ab_b=math.min(dur,s.ab_a+5) end
        s.ab_b=clamp(s.ab_b+1,s.ab_a+1,dur);s.ab_state="loop"
      end
      if s.ab_state=="loop" then
        s.playing=true;s.play_base_sec=s.ab_a;s.play_started_ms=now
        return "ab_nudge",make_audio_event(s,now)
      end
      return "ab_nudge"
    elseif y>=354 and y<=422 then
      local cur=M.current_play_sec(s,now)
      if x<116 then
        s.play_base_sec=clamp(cur-3,0,dur);s.play_started_ms=now;s.playing=true
        return "seek",make_audio_event(s,now)
      elseif x<=362 then
        if s.playing then
          s.play_base_sec=cur;s.playing=false
          return "pause",{type="audio_stop"}
        else
          s.playing=true;s.play_started_ms=now
          return "play",make_audio_event(s,now)
        end
      else
        s.play_base_sec=clamp(cur+3,0,math.max(0,dur-1));s.play_started_ms=now;s.playing=true
        return "seek",make_audio_event(s,now)
      end
    end
  end

  -- Page 4: 5x5 Lesson Grid (选课 L01-L25)
  if s.page==4 then
    if y>=58 and y<=298 and x>=18 and x<=462 then
      local col=clamp(math.floor((x-20)/88),0,4)
      local row=clamp(math.floor((y-58)/48),0,4)
      local chosen=row*5+col+1
      if chosen>=1 and chosen<=25 then
        s.lesson=chosen
        s.card_index=1;s.card_flipped=false;s.quiz_index=1;s.quiz_selected=0;s.track_slot=1;s.playing=false;s.ab_state="off";s.play_base_sec=0
        s.page=1
        s.data_seq=(s.data_seq or 0)+1
        return "lesson",{type="select_lesson",lesson=s.lesson,flash_cat=s.flash_cat,quiz_mode=s.quiz_mode}
      end
    elseif y>=304 and y<=420 then
      s.page=2;s.quiz_mode="review";s.quiz_index=1;s.quiz_selected=0
      s.data_seq=(s.data_seq or 0)+1
      return "review",{type="select_quiz_mode",lesson=s.lesson,quiz_mode="review"}
    end
  end

  -- Page 5: System Settings Tab (设定) + Bluetooth Audio Scanner/Pairing Modal
  if s.page==5 then
    if s.bt_modal then
      -- Top control bar of Bluetooth modal (y=54..96)
      if y>=54 and y<=98 then
        if x>=18 and x<=176 then
          s.bt_scanning=true
          s.data_seq=(s.data_seq or 0)+1
          return "bt_scan",{type="bt_control",action="scan"}
        elseif x>=180 and x<=354 then
          s.audio_target=(s.audio_target=="bt") and "esp32" or "bt"
          s.data_seq=(s.data_seq or 0)+1
          return "bt_toggle",{type="bt_control",action="set_target",audio_target=s.audio_target,volume=s.volume,playing=s.playing}
        elseif x>=358 and x<=464 then
          s.bt_modal=false
          s.data_seq=(s.data_seq or 0)+1
          return "tap"
        end
      -- Device list rows (y=102..354, 4 rows of height 62)
      elseif y>=102 and y<=354 then
        local row=clamp(math.floor((y-102)/62)+1,1,4)
        local devs=s.bt_devices or {}
        local idx=((s.bt_page or 1)-1)*4+row
        local dev=devs[idx]
        if dev then
          local target=dev.address or dev.name or ""
          if dev.connected and x>=324 then
            dev.connected=false
            s.data_seq=(s.data_seq or 0)+1
            return "bt_disconnect",{type="bt_control",action="disconnect",target=target}
          else
            s.bt_name=dev.name or target
            s.audio_target="bt"
            s.data_seq=(s.data_seq or 0)+1
            local act_type=dev.paired and "connect" or "pair"
            return "bt_connect",{type="bt_control",action=act_type,target=target,audio_target="bt",volume=s.volume,playing=s.playing}
          end
        end
        return "tap"
      -- Bottom bar of Bluetooth modal (y=358..422)
      elseif y>=358 and y<=422 then
        local devs=s.bt_devices or {}
        local max_p=math.max(1,math.ceil(#devs/4))
        if x<138 then
          s.bt_page=math.max(1,(s.bt_page or 1)-1)
          s.data_seq=(s.data_seq or 0)+1
          return "tap"
        elseif x<=342 then
          if s.volume==0 then s.volume=60 end
          s.audio_target="bt"
          s.data_seq=(s.data_seq or 0)+1
          return "speak",make_speak_event(s,"こんにちは。Bluetoothオーディオのテストです。")
        else
          s.bt_page=math.min(max_p,(s.bt_page or 1)+1)
          s.data_seq=(s.data_seq or 0)+1
          return "tap"
        end
      end
      return nil
    end

    -- Normal Settings Page 5:
    -- Row 1: Volume controls (y=56..136, buttons y=86..132)
    if y>=84 and y<=136 then
      if x>=28 and x<=124 then
        s.volume=(s.volume>0) and 0 or 80
        return "volume",{type="volume_change",volume=s.volume,slow_tts=s.slow_tts,playing=s.playing,audio_target=s.audio_target}
      elseif x>=132 and x<=236 then
        s.volume=clamp(s.volume-10,0,100)
        return "volume",{type="volume_change",volume=s.volume,slow_tts=s.slow_tts,playing=s.playing,audio_target=s.audio_target}
      elseif x>=244 and x<=348 then
        s.volume=clamp(s.volume+10,0,100)
        return "volume",{type="volume_change",volume=s.volume,slow_tts=s.slow_tts,playing=s.playing,audio_target=s.audio_target}
      elseif x>=356 and x<=454 then
        if s.volume==0 then s.volume=60 end
        return "speak",make_speak_event(s,"こんにちは。音量テストです。")
      end
    -- Row 2: Bluetooth Audio & Output controls (y=142..228)
    elseif y>=142 and y<=228 then
      if y>=168 and x>=28 and x<=238 then
        s.audio_target=(s.audio_target=="bt") and "esp32" or "bt"
        s.data_seq=(s.data_seq or 0)+1
        return "bt_toggle",{type="bt_control",action="set_target",audio_target=s.audio_target,volume=s.volume,playing=s.playing}
      else
        s.bt_modal=true
        s.bt_page=1
        s.data_seq=(s.data_seq or 0)+1
        return "bt_open",{type="bt_control",action="status",audio_target=s.audio_target}
      end
    -- Row 3: Screen Brightness controls (y=234..308, buttons y=244..304)
    elseif y>=242 and y<=308 then
      if x>=244 and x<=344 then
        s.brightness=clamp(s.brightness-10,10,100)
        return "brightness"
      elseif x>=352 and x<=452 then
        s.brightness=clamp(s.brightness+10,10,100)
        return "brightness"
      end
    -- Row 4: Peripheral toggles (y=314..364)
    elseif y>=314 and y<=364 then
      if x<240 then
        s.leds_muted=not s.leds_muted
        return "prefs"
      else
        s.haptic_enabled=not s.haptic_enabled
        return "prefs"
      end
    -- Row 5: TTS speed & Exit (y=370..422)
    elseif y>=370 and y<=422 then
      if x<240 then
        s.slow_tts=not s.slow_tts
        return "prefs",{type="volume_change",volume=s.volume,slow_tts=s.slow_tts,audio_target=s.audio_target}
      else
        return "exit"
      end
    end
  end

  return nil
end

function M.touch(s,point,now)
  if point then
    s.last_touch=now
    if not s.gesture then
      s.gesture={x0=point.x,y0=point.y,x=point.x,y=point.y,t0=now,hold_fired=false}
    else
      s.gesture.x=point.x;s.gesture.y=point.y
      local dx=math.abs(point.x-s.gesture.x0)
      local dy=math.abs(point.y-s.gesture.y0)
      if not s.gesture.hold_fired and s.page<=4 and dx<18 and dy<18 and point.y>60 and point.y<420 and now-s.gesture.t0>=950 then
        s.gesture.hold_fired=true
        s.prev_page=s.page
        s.page=5
        return "settings"
      end
    end
    return nil
  end
  if not s.gesture then return nil end
  local g=s.gesture;s.gesture=nil
  if g.hold_fired then return nil end
  local dx=g.x-g.x0;local dy=g.y-g.y0
  if math.abs(dx)>=64 and math.abs(dx)>math.abs(dy)*1.4 and g.y0>55 and g.y0<425 then
    if dx<0 then s.page=s.page<5 and (s.page+1) or 1
    else s.page=s.page>1 and (s.page-1) or 5 end
    return "swipe"
  end
  return M.handle_tap(s,g.x,g.y,now)
end

-- Physical Buttons:
-- KEY_R (GPIO10, Button 1 with white LED):
--   Page 1 (词句): Flip card & speak Japanese
--   Page 2 (练习): Reveal / speak Japanese sentence
--   Page 3 (复读): Mark A -> Mark B (start AB loop) -> Clear AB
--   Page 4 (选课): Jump to Page 3 (AB Repeater)
--   Page 5 (设定): Volume +10%
-- KEY_L (GPIO11, Button 2):
--   Page 1 (词句): Next flashcard
--   Page 2 (练习): Next quiz question
--   Page 3 (复读): Play / Pause toggle
--   Page 4 (选课): Next lesson
--   Page 5 (设定): Volume -10%
function M.buttons(s,left_level,right_level,now)
  local l_pressed=(left_level==0)
  local r_pressed=(right_level==0)
  if l_pressed and r_pressed then
    if not s.both_handled then
      s.both_handled=true
      s.leds_muted=not s.leds_muted
      return "leds_toggle"
    end
    return nil
  end
  if not l_pressed and not r_pressed then
    s.both_handled=false
  end

  local action,event=nil,nil
  if r_pressed and not s.key_r_down and not s.both_handled then
    if s.page==1 then
      s.card_flipped=not s.card_flipped
      local c=M.current_card(s)
      action="key_r_flash"
      event=make_speak_event(s,c.speak or c.front)
    elseif s.page==2 then
      local q=M.current_quiz(s)
      if s.quiz_selected==0 then s.quiz_selected=tonumber(q.correct) or 1;s.quiz_last_correct=true;s.quiz_feedback_ms=now end
      action="key_r_quiz"
      event=make_speak_event(s,q.speak or q.prompt)
    elseif s.page==3 then
      action="key_r_ab"
      event=M.trigger_ab_button(s,now)
    elseif s.page==5 then
      s.volume=clamp(s.volume+10,0,100)
      action="volume"
      event={type="volume_change",volume=s.volume,slow_tts=s.slow_tts,playing=s.playing,audio_target=s.audio_target}
    else
      s.page=3;action="key_r_tab"
    end
  elseif l_pressed and not s.key_l_down and not s.both_handled then
    if s.page==1 then
      local _,idx,total=M.current_card(s)
      s.card_index=idx<total and (idx+1) or 1
      s.card_flipped=false
      action="key_l_next_card"
    elseif s.page==2 then
      local _,idx,total=M.current_quiz(s)
      s.quiz_index=idx<total and (idx+1) or 1
      s.quiz_selected=0
      action="key_l_next_quiz"
    elseif s.page==3 then
      local cur=M.current_play_sec(s,now)
      if s.playing then
        s.play_base_sec=cur;s.playing=false
        action="key_l_pause";event={type="audio_stop"}
      else
        s.playing=true;s.play_started_ms=now
        action="key_l_play";event=make_audio_event(s,now)
      end
    elseif s.page==5 then
      s.volume=clamp(s.volume-10,0,100)
      action="volume"
      event={type="volume_change",volume=s.volume,slow_tts=s.slow_tts,playing=s.playing,audio_target=s.audio_target}
    else
      s.lesson=s.lesson<25 and (s.lesson+1) or 1
      s.card_index=1;s.card_flipped=false;s.quiz_index=1;s.quiz_selected=0;s.track_slot=1;s.playing=false;s.ab_state="off";s.play_base_sec=0
      s.data_seq=(s.data_seq or 0)+1
      action="lesson";event={type="select_lesson",lesson=s.lesson,flash_cat=s.flash_cat,quiz_mode=s.quiz_mode}
    end
  end
  s.key_l_down=l_pressed
  s.key_r_down=r_pressed
  return action,event
end

function M.pixels(s,now,brightness)
  local out={{0,0,0},{0,0,0},{0,0,0},{0,0,0},{0,0,0},{0,0,0}}
  if s.leds_muted then return out end
  local scale=clamp(tonumber(brightness) or 18,5,100)/100.0
  local function rgb(r,g,b)
    return {math.floor(r*scale+0.5),math.floor(g*scale+0.5),math.floor(b*scale+0.5)}
  end

  if now-s.quiz_feedback_ms<1500 then
    local c=s.quiz_last_correct and rgb(40,235,160) or rgb(255,70,80)
    for i=1,4 do out[i]=c end
    out[5]=rgb(210,225,245)
    return out
  end

  if s.page==3 then
    local pos,dur=M.current_play_sec(s,now)
    if s.ab_state=="loop" then
      local pulse=((now//300)%2)==0
      out[1]=rgb(60,230,190)
      out[2]=pulse and rgb(255,165,90) or rgb(60,230,190)
      out[3]=pulse and rgb(60,230,190) or rgb(255,165,90)
      out[4]=rgb(255,165,90)
    elseif s.ab_state=="a_set" then
      out[1]=rgb(60,230,190)
      out[2]=((now//250)%2)==0 and rgb(255,165,90) or {0,0,0}
    elseif s.playing then
      local lit=clamp(math.ceil((pos/math.max(1,dur))*4),1,4)
      for i=1,lit do out[i]=rgb(80,175,255) end
    else
      out[1]=rgb(35,65,100)
    end
    out[5]=s.ab_state=="loop" and rgb(255,180,100) or rgb(200,220,245)
  else
    out[1]=s.page==1 and rgb(60,230,190) or rgb(25,55,75)
    out[2]=s.page==2 and rgb(110,180,255) or rgb(25,55,75)
    out[3]=s.page==3 and rgb(255,165,90) or rgb(25,55,75)
    out[4]=s.page==4 and rgb(180,140,255) or rgb(25,55,75)
    out[5]=s.page==5 and rgb(255,215,110) or rgb(200,220,245)
  end
  return out
end

return M
