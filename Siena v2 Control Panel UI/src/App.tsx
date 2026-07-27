import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { ChatAttachment, Conversation, Message, ModelsPayload } from "./api/types";

const views = ["Chat", "Models", "Memory", "Insights", "Voice", "Settings", "Logs", "Tool Trace", "Diagnostics"] as const;
type View = typeof views[number];

function JsonPanel({ title, load }: { title: string; load: () => Promise<unknown> }) {
  const [data, setData] = useState<unknown>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(() => {
    setError("");
    void load().then(setData).catch((reason: Error) => setError(reason.message));
  }, [load]);
  useEffect(refresh, [refresh]);
  return <section className="panel"><div className="toolbar"><h2>{title}</h2><button onClick={refresh}>Refresh</button></div>
    {error && <p className="error">{error}</p>}<pre className="card">{JSON.stringify(data, null, 2)}</pre></section>;
}

function Models() {
  const [state, setState] = useState<ModelsPayload | null>(null);
  const [error, setError] = useState("");
  const load = useCallback((refresh = false) => {
    setError("");
    return (refresh ? api.refreshModels() : api.models()).then(setState).catch((reason: Error) => setError(reason.message));
  }, []);
  useEffect(() => { void load(); }, [load]);
  const assign = async (role: string, model: string) => {
    try { await api.assignRole(role, model); await load(); } catch (reason) { setError((reason as Error).message); }
  };
  return <section className="panel">
    <div className="toolbar"><h2>Installed Ollama models</h2><button onClick={() => void load(true)}>Refresh catalog</button></div>
    {error && <p className="error">{error}</p>}
    {!state?.available && <p className="error">Ollama unavailable: {state?.error}</p>}
    <div className="cards">{state?.roles.map(role => <div className="card row" key={role.role}>
      <strong>{role.role}</strong>
      <select aria-label={`${role.role} model`} value={role.model} onChange={event => void assign(role.role, event.target.value)}>
        {role.missing && <option value={role.model}>{role.model} (missing)</option>}
        {state.models.map(model => <option key={model.name} value={model.name}>{model.name}</option>)}
      </select>
      {role.missing && <span className="missing">missing</span>}
    </div>)}</div>
    <div className="cards">{state?.models.map(model => <article className="card" key={model.name}>
      <div className="row"><strong>{model.name}</strong>{model.loaded && <span className="ok">loaded</span>}</div>
      <div className="muted">{model.family || "unknown family"} · {model.parameter_size || "unknown parameters"} · {model.quantization || "unknown quantization"} · {model.size ? `${(model.size / 1e9).toFixed(2)} GB` : "unknown size"}</div>
    </article>)}</div>
  </section>;
}

function Chat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [error, setError] = useState("");
  const loadList = useCallback(async () => {
    const result = await api.conversations();
    setConversations(result.conversations);
    if (!active && result.conversations[0]) setActive(result.conversations[0].id);
  }, [active]);
  useEffect(() => { void loadList().catch((e: Error) => setError(e.message)); }, [loadList]);
  useEffect(() => {
    if (active) void api.conversation(active).then(item => setMessages(item.messages || [])).catch((e: Error) => setError(e.message));
  }, [active]);
  const create = async () => { const result = await api.createConversation(); setActive(result.conversation_id); setMessages([]); await loadList(); };
  const send = async () => {
    if (!active || (!text.trim() && !attachments.length) || busy) return;
    const message = text.trim(); setText(""); setBusy(true); setError("");
    setMessages(items => [...items, { id: crypto.randomUUID(), role: "user", content: message }]);
    try { await api.chat(active, message || "Please process the attached files.", attachments); setAttachments([]); const current = await api.conversation(active); setMessages(current.messages || []); await loadList(); }
    catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  };
  const selectFiles = async (files: FileList | null) => {
    if (!files) return;
    try {
      const selected = await Promise.all(Array.from(files).slice(0, 5).map(async file => {
        if (file.type.startsWith("image/")) {
          const data_url = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = reject;
            reader.readAsDataURL(file);
          });
          return { name: file.name, type: "image", mime: file.type, data_url } satisfies ChatAttachment;
        }
        return { name: file.name, type: "text", mime: file.type || "text/plain", content: await file.text() } satisfies ChatAttachment;
      }));
      setAttachments(selected);
    } catch (reason) { setError((reason as Error).message); }
  };
  return <section className="panel"><div className="toolbar"><h2>Chat</h2><button onClick={() => void create()}>New conversation</button></div>
    {error && <p className="error">{error}</p>}<div className="chat">
      <div className="cards">{conversations.map(item => <button className={item.id === active ? "active" : ""} key={item.id} onClick={() => setActive(item.id)}>{item.title}</button>)}</div>
      <div><div className="messages card">{messages.map(item => <div className={`message ${item.role}`} key={item.id}>{item.content}
        {item.attachments?.map(file => <div className="muted" key={file.id}>📎 {file.original_name} · OCR {file.ocr_status || "n/a"} · Vision {file.vision_status || "n/a"}</div>)}
        <div className="muted">{item.model}</div></div>)}</div>
        {attachments.length > 0 && <div className="muted">Selected: {attachments.map(item => item.name).join(", ")}</div>}
        <div className="composer"><label className="file-button">Attach<input type="file" multiple accept="text/*,.md,.json,.log,image/*" onChange={event => void selectFiles(event.target.files)} /></label><textarea value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} /><button disabled={busy} onClick={() => void send()}>{busy ? "Thinking…" : "Send"}</button></div>
      </div></div></section>;
}

function wavBlob(chunks: Float32Array[], sampleRate: number): Blob {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) =>
    [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  write(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); write(8, "WAVE"); write(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const sample of chunk) {
    view.setInt16(offset, Math.max(-1, Math.min(1, sample)) * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function Voice() {
  const [status, setStatus] = useState<unknown>(null);
  const [transcription, setTranscription] = useState("");
  const [speech, setSpeech] = useState("");
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState("");
  const chunks = useRef<Float32Array[]>([]);
  const context = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const processor = useRef<ScriptProcessorNode | null>(null);
  const refresh = useCallback(() => {
    setError("");
    void Promise.all([api.voiceStatus(), api.ocrStatus(), api.visionStatus()])
      .then(([voice, ocr, vision]) => setStatus({ voice, ocr, vision }))
      .catch((reason: Error) => setError(reason.message));
  }, []);
  useEffect(refresh, [refresh]);
  const start = async () => {
    try {
      setError(""); chunks.current = [];
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      context.current = new AudioContext({ sampleRate: 16000 });
      const source = context.current.createMediaStreamSource(stream.current);
      processor.current = context.current.createScriptProcessor(4096, 1, 1);
      processor.current.onaudioprocess = event => chunks.current.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      source.connect(processor.current); processor.current.connect(context.current.destination); setRecording(true);
    } catch (reason) { setError((reason as Error).message); }
  };
  const stop = async () => {
    const rate = context.current?.sampleRate || 16000;
    processor.current?.disconnect();
    stream.current?.getTracks().forEach(track => track.stop());
    await context.current?.close();
    setRecording(false);
    try { setTranscription((await api.transcribe(wavBlob(chunks.current, rate))).text); }
    catch (reason) { setError((reason as Error).message); }
  };
  const synthesize = async () => {
    try { const result = await api.synthesize(speech); await new Audio(api.audioUrl(result.audio_url)).play(); }
    catch (reason) { setError((reason as Error).message); }
  };
  return <section className="panel">
    <div className="toolbar"><h2>Voice, OCR and Vision</h2><button onClick={refresh}>Refresh status</button></div>
    {error && <p className="error">{error}</p>}<pre className="card">{JSON.stringify(status, null, 2)}</pre>
    <div className="card"><h3>Push to talk</h3><button onClick={() => void (recording ? stop() : start())}>{recording ? "Stop and transcribe" : "Start recording"}</button><textarea readOnly value={transcription} placeholder="Transcription" /></div>
    <div className="card"><h3>Speech synthesis</h3><textarea value={speech} onChange={event => setSpeech(event.target.value)} /><button disabled={!speech.trim()} onClick={() => void synthesize()}>Speak</button></div>
  </section>;
}

function Settings() {
  const [payload, setPayload] = useState<{ values: Record<string, unknown>; classification: Record<string, string> } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void api.settings().then(setPayload).catch((e: Error) => setError(e.message)); }, []);
  const save = async (key: string, value: unknown) => {
    try { await api.saveSettings({ [key]: value }); setPayload(current => current ? { ...current, values: { ...current.values, [key]: value } } : current); }
    catch (reason) { setError((reason as Error).message); }
  };
  return <section className="panel"><h2>Settings</h2>{error && <p className="error">{error}</p>}<div className="cards">
    {payload && ["interface_language", "appearance_theme", "ui_font_size", "ui_density", "startup_page", "log_level"].map(key =>
      <label className="card row" key={key}><strong>{key}</strong><input value={String(payload.values[key] ?? "")} onChange={event => void save(key, event.target.value)} /><span className="muted">{payload.classification[key]}</span></label>)}
  </div></section>;
}

export default function App() {
  const [view, setView] = useState<View>("Chat");
  return <div className="app"><nav><h1>Siena</h1>{views.map(item => <button className={view === item ? "active" : ""} key={item} onClick={() => setView(item)}>{item}</button>)}</nav><main>
    {view === "Chat" && <Chat />}
    {view === "Models" && <Models />}
    {view === "Memory" && <JsonPanel title="Memory" load={async () => ({ short: await api.shortMemory(), long: await api.longMemory() })} />}
    {view === "Insights" && <JsonPanel title="Insights" load={api.insights} />}
    {view === "Voice" && <Voice />}
    {view === "Settings" && <Settings />}
    {view === "Logs" && <JsonPanel title="Logs" load={api.logs} />}
    {view === "Tool Trace" && <JsonPanel title="Tool Trace" load={api.trace} />}
    {view === "Diagnostics" && <JsonPanel title="Diagnostics" load={api.diagnostics} />}
  </main></div>;
}
