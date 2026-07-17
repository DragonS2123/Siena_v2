local SubtitleOverlay={}
SubtitleOverlay.__index=SubtitleOverlay
local function sanitize(text,max_chars)
  text=tostring(text or ""):gsub("[%c<>`*_#%[%]{}]",""):gsub("%s+"," ")
  return text:sub(1,max_chars)
end
function SubtitleOverlay.new(config)
  return setmetatable({config=config,visible=false,text="",until_ms=0},SubtitleOverlay)
end
function SubtitleOverlay:show(text,duration_ms,now_ms)
  if not self.config.in_game_subtitles_enabled then return end
  self.text=sanitize(text,self.config.subtitle_max_chars); self.visible=self.text~=""; self.until_ms=now_ms+math.min(math.max(duration_ms or 1000,250),30000)
end
function SubtitleOverlay:clear() self.visible=false; self.text=""; self.until_ms=0 end
function SubtitleOverlay:update(now_ms) if self.visible and now_ms>=self.until_ms then self:clear() end end
function SubtitleOverlay:draw()
  if not self.visible or ImGui==nil then return end
  pcall(function()
    local width,height=GetDisplayResolution(); ImGui.SetNextWindowPos(width*0.5-230,height*0.76,ImGuiCond.Always); ImGui.SetNextWindowSize(460,0)
    ImGui.SetNextWindowBgAlpha(0.72); ImGui.Begin("SienaSubtitle",true,ImGuiWindowFlags.NoTitleBar+ImGuiWindowFlags.NoResize+ImGuiWindowFlags.NoMove+ImGuiWindowFlags.NoInputs)
    ImGui.TextColored(0.85,0.35,0.95,1.0,"SIENA"); ImGui.TextWrapped(self.text); ImGui.End()
  end)
end
return SubtitleOverlay
