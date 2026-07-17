local NpcClient = {}
NpcClient.__index = NpcClient
local allowed={spawn=true,despawn=true,follow=true,stay=true,come_here=true,look_at_player=true,clear_look_at=true,status=true,configure=true,suspend=true,resume=true,speech_start=true,speech_end=true}
local origins={player=true,system=true,speech=true}
local function endpoint(base,path) return base:gsub("/$","")..path end

function NpcClient.new(config,logger,executor,status_provider)
  return setmetatable({config=config,logger=logger,executor=executor,status_provider=status_provider,available=false,connected=false,in_flight=nil,pending_result=nil,current_command=nil,next_poll_at=0,backoff_ms=config.error_backoff_ms,now_ms=0,token=0},NpcClient)
end
function NpcClient:start()
  if AsyncHttpClient==nil or HttpCallback==nil or HttpHeader==nil or NewProxy==nil or json==nil then self.logger:trace("npc_backend_disconnected","reason=RedHttpClient_or_json_unavailable"); return false end
  self.available=true; return true
end
function NpcClient:_callback(token)
  local owner=self
  local listener=NewProxy({OnResponse={args={"handle:HttpResponse"},callback=function(response) owner:_on_response(token,response) end}})
  return listener,HttpCallback.Create(listener:Target(),listener:Function("OnResponse"))
end
function NpcClient:_failure(message)
  self.connected=false; self.logger:trace("npc_backend_disconnected",message); self.next_poll_at=self.now_ms+self.backoff_ms; self.backoff_ms=math.min(self.backoff_ms*2,self.config.max_backoff_ms)
end
function NpcClient:_success()
  self.connected=true; self.backoff_ms=self.config.error_backoff_ms; self.next_poll_at=self.now_ms+self.config.poll_interval_ms
end
function NpcClient:_get_next()
  self.token=self.token+1; local token=self.token; local listener,callback=self:_callback(token)
  self.in_flight={token=token,kind="claim",started_at=self.now_ms,listener=listener,callback=callback}
  local ok,error=pcall(function() AsyncHttpClient.Get(callback,endpoint(self.config.backend_url,"/api/v1/npc/commands/next")) end)
  if not ok then self.in_flight=nil; self:_failure("claim_dispatch_failed="..tostring(error)) end
end
function NpcClient:_post_result()
  local item=self.pending_result; if item==nil then return end
  local ok_json,body=pcall(function() return json.encode({result=item.result,error=item.error,status=self.status_provider(self.connected)}) end)
  if not ok_json then self.pending_result=nil; self.logger:trace("npc_command_rejected","reason=result_encode_failed"); return end
  self.token=self.token+1; local token=self.token; local listener,callback=self:_callback(token)
  self.in_flight={token=token,kind="result",started_at=self.now_ms,listener=listener,callback=callback}
  local headers={HttpHeader.Create("Content-Type","application/json")}
  local ok,error=pcall(function() AsyncHttpClient.Post(callback,endpoint(self.config.backend_url,"/api/v1/npc/commands/"..item.command_id.."/result"),body,headers) end)
  if not ok then self.in_flight=nil; self:_failure("result_dispatch_failed="..tostring(error)) end
end
function NpcClient:_queue_result(command_id,result,error)
  self.pending_result={command_id=command_id,result=result,error=error}; self:_post_result()
end
function NpcClient:complete_current(result,error)
  if self.current_command==nil then return end
  local id=self.current_command.command_id; self.current_command=nil; self:_queue_result(id,result,error)
end
function NpcClient:_on_response(token,response)
  local request=self.in_flight; if request==nil or request.token~=token then return end; self.in_flight=nil
  local status=0; if response then local ok,value=pcall(function() return response:GetStatusCode() end); if ok then status=value end end
  if request.kind=="result" then
    if status==200 then self.pending_result=nil; self:_success() else self:_failure("result_http="..tostring(status)) end
    return
  end
  if status==204 then self:_success(); return end
  if status~=200 or response==nil then self:_failure("claim_http="..tostring(status)); return end
  local text_ok,text=pcall(function() return response:GetText() end); local json_ok,value=false,nil
  if text_ok then json_ok,value=pcall(function() return json.decode(text) end) end
  if not json_ok or type(value)~="table" or type(value.command_id)~="string" or type(value.command)~="string" or type(value.expires_at_epoch_ms)~="number" then self:_success(); self.logger:trace("npc_command_rejected","reason=response_validation"); return end
  if not allowed[value.command] then self:_success(); self.logger:trace("npc_command_rejected","reason=unknown_command"); self:_queue_result(value.command_id,"rejected","unknown_command"); return end
  if value.origin~=nil and not origins[value.origin] then self:_success(); self:_queue_result(value.command_id,"rejected","invalid_origin"); return end
  if value.payload~=nil and type(value.payload)~="table" then self:_success(); self:_queue_result(value.command_id,"rejected","invalid_payload"); return end
  if value.expires_at_epoch_ms<=os.time()*1000 then self:_success(); self.logger:trace("npc_command_expired","command="..value.command); self:_queue_result(value.command_id,"expired","local_expiry"); return end
  self:_success(); self.current_command={command_id=value.command_id,command=value.command}
  local result,error=self.executor(value.command,value.payload,value.origin or "player",self.now_ms)
  if result~="pending" then self.current_command=nil; self:_queue_result(value.command_id,result,error) end
end
function NpcClient:update(now_ms,session_available)
  self.now_ms=now_ms
  if not self.available or not self.config.enabled then return end
  if self.in_flight and now_ms-self.in_flight.started_at>=self.config.request_timeout_ms then self.token=self.token+1; self.in_flight=nil; self:_failure("request_timeout") end
  if self.in_flight then return end
  if self.pending_result then if now_ms>=self.next_poll_at then self:_post_result() end; return end
  if self.current_command then return end
  if not session_available or now_ms<self.next_poll_at then return end
  self:_get_next()
end
function NpcClient:shutdown() self.token=self.token+1; self.in_flight=nil; self.pending_result=nil; self.current_command=nil; self.available=false; self.connected=false end
return NpcClient
