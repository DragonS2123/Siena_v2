import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Maximize2,
  Save,
  Terminal,
  X,
} from "lucide-react";

import { useUiPreferences } from "../hooks/useUiPreferences";

export interface MessageSegment {
  type: "text" | "code";
  content: string;
  lang?: string;
  filename?: string;
  filenameExplicit?: boolean;
}

const LANGUAGE_ALIASES: Record<string, string> = {
  "": "plaintext",
  text: "plaintext",
  txt: "plaintext",
  plain: "plaintext",
  plaintext: "plaintext",
  md: "markdown",
  markdown: "markdown",
  json: "json",
  jsonc: "json",
  yml: "yaml",
  yaml: "yaml",
  xml: "xml",
  html: "html",
  htm: "html",
  css: "css",
  js: "javascript",
  javascript: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  typescript: "typescript",
  jsx: "jsx",
  tsx: "tsx",
  py: "python",
  python: "python",
  ps1: "powershell",
  pwsh: "powershell",
  powershell: "powershell",
  bash: "bash",
  sh: "shell",
  shell: "shell",
  zsh: "shell",
  lua: "lua",
  c: "c",
  "c++": "cpp",
  cpp: "cpp",
  cc: "cpp",
  "c#": "csharp",
  cs: "csharp",
  csharp: "csharp",
  java: "java",
  sql: "sql",
  rs: "rust",
  rust: "rust",
  golang: "go",
  go: "go",
};

const LANGUAGE_LABELS: Record<string, string> = {
  plaintext: "TEXT",
  markdown: "MARKDOWN",
  json: "JSON",
  yaml: "YAML",
  xml: "XML",
  html: "HTML",
  css: "CSS",
  javascript: "JAVASCRIPT",
  typescript: "TYPESCRIPT",
  jsx: "JSX",
  tsx: "TSX",
  python: "PYTHON",
  powershell: "POWERSHELL",
  bash: "BASH",
  shell: "SHELL",
  lua: "LUA",
  c: "C",
  cpp: "C++",
  csharp: "C#",
  java: "JAVA",
  sql: "SQL",
  rust: "RUST",
  go: "GO",
};

const LANGUAGE_FILES: Record<string, string> = {
  plaintext: "code.txt",
  markdown: "README.md",
  json: "data.json",
  yaml: "config.yaml",
  xml: "code.xml",
  html: "code.html",
  css: "styles.css",
  javascript: "script.js",
  typescript: "script.ts",
  jsx: "component.jsx",
  tsx: "component.tsx",
  python: "script.py",
  powershell: "script.ps1",
  bash: "script.sh",
  shell: "script.sh",
  lua: "script.lua",
  c: "main.c",
  cpp: "main.cpp",
  csharp: "Program.cs",
  java: "Main.java",
  sql: "query.sql",
  rust: "main.rs",
  go: "main.go",
};

const LANGUAGE_EXTENSIONS: Record<string, string> = Object.fromEntries(
  Object.entries(LANGUAGE_FILES).map(([language, filename]) => [language, filename.split(".").pop() ?? "txt"]),
);

const SUPPORTED_LANGUAGES = new Set(Object.values(LANGUAGE_ALIASES));

export function normalizeLanguage(rawLanguage = ""): string {
  const key = rawLanguage.trim().toLowerCase();
  return LANGUAGE_ALIASES[key] ?? (key.replace(/[^a-z0-9#+-]/g, "") || "plaintext");
}

function safeFilename(candidate: string | undefined, language: string): string {
  const fallback = LANGUAGE_FILES[language] ?? "code.txt";
  if (!candidate) return fallback;
  const basename = candidate.trim().replace(/^["'`]|["'`]$/g, "").split(/[\\/]/).pop() ?? "";
  const sanitized = basename
    .replace(/^\.+/, "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .trim()
    .slice(0, 120);
  return sanitized && sanitized !== "." && sanitized !== ".." ? sanitized : fallback;
}

function parseFenceInfo(info: string): { lang: string; filename?: string } {
  const trimmed = info.trim();
  if (!trimmed) return { lang: "plaintext" };
  const languageToken = trimmed.match(/^([^\s,{]+)/)?.[1] ?? "";
  const lang = normalizeLanguage(languageToken);
  const filenameMatch =
    trimmed.match(/\b(?:filename|file|title)\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s,}]+))/i) ??
    trimmed.match(/^[^\s]+\s+([^\s,}]+\.[a-z0-9]{1,10})\b/i);
  return {
    lang,
    filename: filenameMatch ? (filenameMatch[1] ?? filenameMatch[2] ?? filenameMatch[3]) : undefined,
  };
}

function filenameFromFirstLine(code: string): string | undefined {
  const first = code.split("\n", 1)[0]?.trim() ?? "";
  const match =
    first.match(/^(?:\/\/|#|--|;)\s*(?:file(?:name)?\s*:\s*)?([^<>:"|?*\\/]+\.[a-z0-9]{1,10})\s*$/i) ??
    first.match(/^<!--\s*(?:file(?:name)?\s*:\s*)?([^<>:"|?*\\/]+\.[a-z0-9]{1,10})\s*-->$/i);
  return match?.[1];
}

/**
 * Splits CommonMark-style fenced blocks without evaluating Markdown as HTML.
 * An unclosed fence intentionally remains text while streaming; calling this
 * function again with the completed message produces a code segment.
 */
export function parseMessageSegments(content: string): MessageSegment[] {
  const source = content.replace(/\r\n?/g, "\n");
  const lines = source.split("\n");
  const segments: MessageSegment[] = [];
  let textStart = 0;
  let offset = 0;
  let lineIndex = 0;

  while (lineIndex < lines.length) {
    const line = lines[lineIndex];
    const opening = line.match(/^ {0,3}(`{3,}|~{3,})([^\n]*)$/);
    const lineLength = line.length + (lineIndex < lines.length - 1 ? 1 : 0);
    if (!opening || (opening[1][0] === "`" && opening[2].includes("`"))) {
      offset += lineLength;
      lineIndex += 1;
      continue;
    }

    const fenceChar = opening[1][0];
    const fenceLength = opening[1].length;
    let closingIndex = -1;
    for (let cursor = lineIndex + 1; cursor < lines.length; cursor += 1) {
      const closing = lines[cursor].match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/);
      if (closing && closing[1][0] === fenceChar && closing[1].length >= fenceLength) {
        closingIndex = cursor;
        break;
      }
    }

    // During streaming a fence can be temporarily incomplete. Preserve it
    // verbatim as text and let the next React render parse it once closed.
    if (closingIndex < 0) break;

    if (offset > textStart) {
      segments.push({ type: "text", content: source.slice(textStart, offset) });
    }

    const parsedInfo = parseFenceInfo(opening[2]);
    const code = lines.slice(lineIndex + 1, closingIndex).join("\n");
    const detectedFilename = parsedInfo.filename ?? filenameFromFirstLine(code);
    segments.push({
      type: "code",
      content: code,
      lang: parsedInfo.lang,
      filename: safeFilename(detectedFilename, parsedInfo.lang),
      filenameExplicit: Boolean(detectedFilename),
    });

    const consumedLines = lines.slice(lineIndex, closingIndex + 1);
    offset += consumedLines.reduce(
      (sum, consumedLine, consumedIndex) =>
        sum + consumedLine.length + (lineIndex + consumedIndex < lines.length - 1 ? 1 : 0),
      0,
    );
    lineIndex = closingIndex + 1;
    textStart = offset;
  }

  if (textStart < source.length) {
    segments.push({ type: "text", content: source.slice(textStart) });
  }
  return segments.length ? segments : [{ type: "text", content: source }];
}

const PYTHON_KEYWORDS = new Set([
  "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif",
  "else", "except", "False", "finally", "for", "from", "global", "if", "import", "in", "is",
  "lambda", "None", "nonlocal", "not", "or", "pass", "raise", "return", "True", "try", "while",
  "with", "yield", "self",
]);
const JAVASCRIPT_KEYWORDS = new Set([
  "as", "async", "await", "break", "case", "catch", "class", "const", "continue", "debugger",
  "default", "delete", "do", "else", "enum", "export", "extends", "false", "finally", "for",
  "from", "function", "if", "implements", "import", "in", "instanceof", "interface", "let", "new",
  "null", "of", "package", "private", "protected", "public", "readonly", "return", "static",
  "super", "switch", "this", "throw", "true", "try", "type", "typeof", "undefined", "var", "void",
  "while", "with", "yield",
]);
const C_FAMILY_KEYWORDS = new Set([
  "abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char", "class", "const",
  "continue", "decimal", "default", "delegate", "do", "double", "else", "enum", "event", "explicit",
  "extends", "extern", "false", "final", "finally", "float", "for", "foreach", "if", "implements",
  "implicit", "import", "in", "int", "interface", "internal", "is", "long", "namespace", "new",
  "null", "object", "operator", "out", "override", "package", "params", "private", "protected",
  "public", "readonly", "record", "ref", "return", "sealed", "short", "signed", "sizeof", "static",
  "string", "struct", "super", "switch", "this", "throw", "true", "try", "typeof", "uint", "ulong",
  "unchecked", "unsafe", "ushort", "using", "virtual", "void", "volatile", "while",
]);
const OTHER_KEYWORDS = new Set([
  "begin", "case", "class", "const", "do", "else", "end", "false", "for", "from", "func", "function",
  "go", "if", "in", "local", "nil", "not", "null", "or", "return", "select", "self", "then", "true",
  "where", "while",
]);

type TokenType = "kw" | "str" | "num" | "fn" | "type" | "comment" | "deco" | "op" | "plain";
const TOKEN_CLASSES: Record<TokenType, string> = {
  kw: "text-[#c084fc]",
  str: "text-[#86c98e]",
  num: "text-[#e6956a]",
  fn: "text-[#7dd3fc]",
  type: "text-[#fbbf24]",
  comment: "text-[#5a5550] italic",
  deco: "text-[#fb923c]",
  op: "text-[#6b7280]",
  plain: "text-[#d8d0c7]",
};

function keywordSet(language: string): Set<string> {
  if (language === "python") return PYTHON_KEYWORDS;
  if (["javascript", "typescript", "jsx", "tsx"].includes(language)) return JAVASCRIPT_KEYWORDS;
  if (["c", "cpp", "csharp", "java"].includes(language)) return C_FAMILY_KEYWORDS;
  return OTHER_KEYWORDS;
}

function commentMarker(language: string): string | undefined {
  if (["python", "powershell", "bash", "shell", "yaml"].includes(language)) return "#";
  if (["sql", "lua"].includes(language)) return "--";
  if (["javascript", "typescript", "jsx", "tsx", "c", "cpp", "csharp", "java", "rust", "go"].includes(language)) return "//";
  return undefined;
}

function tokenizeLine(line: string, language: string): Array<{ type: TokenType; value: string }> {
  const keywords = keywordSet(language);
  const marker = commentMarker(language);
  const output: Array<{ type: TokenType; value: string }> = [];
  let index = 0;
  while (index < line.length) {
    if (marker && line.startsWith(marker, index)) {
      output.push({ type: "comment", value: line.slice(index) });
      break;
    }
    if ("\"'`".includes(line[index])) {
      const quote = line[index];
      let cursor = index + 1;
      while (cursor < line.length && line[cursor] !== quote) {
        if (line[cursor] === "\\") cursor += 1;
        cursor += 1;
      }
      output.push({ type: "str", value: line.slice(index, Math.min(cursor + 1, line.length)) });
      index = Math.min(cursor + 1, line.length);
      continue;
    }
    if (/\d/.test(line[index]) && (index === 0 || !/\w/.test(line[index - 1]))) {
      let cursor = index;
      while (cursor < line.length && /[\d._xXa-fA-F]/.test(line[cursor])) cursor += 1;
      output.push({ type: "num", value: line.slice(index, cursor) });
      index = cursor;
      continue;
    }
    if (line[index] === "@") {
      let cursor = index + 1;
      while (cursor < line.length && /[\w.]/.test(line[cursor])) cursor += 1;
      output.push({ type: "deco", value: line.slice(index, cursor) });
      index = cursor;
      continue;
    }
    if (/[A-Za-z_$]/.test(line[index])) {
      let cursor = index;
      while (cursor < line.length && /[\w$]/.test(line[cursor])) cursor += 1;
      const word = line.slice(index, cursor);
      const type: TokenType = keywords.has(word)
        ? "kw"
        : cursor < line.length && line[cursor] === "("
          ? "fn"
          : /^[A-Z]/.test(word)
            ? "type"
            : "plain";
      output.push({ type, value: word });
      index = cursor;
      continue;
    }
    output.push({ type: "op", value: line[index] });
    index += 1;
  }
  return output;
}

const CODE_FONT_SIZE: Record<string, string> = {
  small: "text-[11px]",
  default: "text-[13px]",
  large: "text-[15px]",
};

function SyntaxHighlight({
  code,
  lang,
  fontSize = "default",
  wrap = false,
  highlight = true,
  showLineNumbers = true,
}: {
  code: string;
  lang: string;
  fontSize?: string;
  wrap?: boolean;
  highlight?: boolean;
  showLineNumbers?: boolean;
}) {
  return (
    <div
      className={`${CODE_FONT_SIZE[fontSize] ?? CODE_FONT_SIZE.default} leading-[1.65] font-mono min-w-max`}
      data-code-language={lang}
    >
      {code.split("\n").map((line, lineIndex) => (
        <div key={lineIndex} className="flex min-h-[1.65em]">
          {showLineNumbers && (
            <span
              aria-hidden="true"
              className="select-none w-8 text-right pr-4 text-[#3a342e] shrink-0 text-xs leading-[1.65]"
            >
              {lineIndex + 1}
            </span>
          )}
          <span className={wrap ? "flex-1 whitespace-pre-wrap break-words" : "flex-1 whitespace-pre"}>
            {highlight
              ? tokenizeLine(line, lang).map((token, tokenIndex) => (
                  <span key={tokenIndex} className={TOKEN_CLASSES[token.type]}>
                    {token.value}
                  </span>
                ))
              : <span className="text-[#c8c0b7]">{line}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

async function copyText(code: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(code);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = code;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("Clipboard is unavailable");
}

function browserSave(code: string, filename: string): void {
  const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

interface ViewerBodyProps {
  code: string;
  lang: string;
  expanded?: boolean;
}

function ViewerBody({ code, lang, expanded = false }: ViewerBodyProps) {
  const { prefs } = useUiPreferences();
  return (
    <div
      className={`px-3 py-4 overflow-auto [scrollbar-width:thin] ${
        expanded ? "h-full max-h-none" : "max-h-[26rem]"
      }`}
      data-testid={expanded ? "expanded-code-scroll" : "code-scroll"}
    >
      <SyntaxHighlight
        code={code}
        lang={lang}
        fontSize={prefs.codeFontSize}
        wrap={prefs.codeLineWrap}
        highlight={prefs.codeSyntaxHighlighting}
        showLineNumbers={prefs.codeShowLineNumbers}
      />
    </div>
  );
}

function ViewerIdentity({ filename, lang }: { filename: string; lang: string }) {
  const { prefs } = useUiPreferences();
  return (
    <div className="flex items-center gap-2 min-w-0">
      <Terminal size={11} className="text-[#c4644a] shrink-0" />
      <span className="text-[11px] font-mono text-[#b8aea4] truncate">{filename}</span>
      {prefs.codeShowLanguageBadge && (
        <span
          className="text-[9px] font-mono uppercase tracking-widest text-[#6b5f57] border border-white/[0.07] rounded px-1.5 py-0.5"
          data-testid="language-badge"
        >
          {LANGUAGE_LABELS[lang] ?? lang.toUpperCase()}
        </span>
      )}
    </div>
  );
}

export function CodeViewer({
  lang: rawLang,
  code,
  filename: rawFilename,
}: {
  lang: string;
  code: string;
  filename?: string;
}) {
  const { prefs, t } = useUiPreferences();
  const lang = normalizeLanguage(rawLang);
  const filename = useMemo(
    () => safeFilename(rawFilename ?? filenameFromFirstLine(code), lang),
    [code, lang, rawFilename],
  );
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const expandButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const copy = useCallback(async () => {
    await copyText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }, [code]);

  const saveToFile = useCallback(async () => {
    if (window.sienaDesktop?.saveCodeFile) {
      await window.sienaDesktop.saveCodeFile({
        content: code,
        suggestedName: filename,
        language: lang,
      });
      return;
    }
    browserSave(code, filename);
  }, [code, filename, lang]);

  const closeExpanded = useCallback(() => setExpanded(false), []);
  useEffect(() => {
    if (!expanded) return undefined;
    const previousActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeExpanded();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      window.setTimeout(() => (expandButtonRef.current ?? previousActive)?.focus(), 0);
    };
  }, [closeExpanded, expanded]);

  const collapseLabel = collapsed ? t("codeBlock.expand") : t("codeBlock.collapse");
  const expandedLabel = t("codeBlock.openExpanded");

  const headerActions = (
    <div className="flex items-center gap-0.5">
      {prefs.codeShowCollapseButton && (
        <button
          type="button"
          onClick={() => setCollapsed((current) => !current)}
          aria-label={collapseLabel}
          className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#c4644a] transition-colors"
        >
          {collapsed ? <ChevronDown size={11} /> : <ChevronUp size={11} />}
          {collapseLabel}
        </button>
      )}
      {prefs.codeShowCopyButton && (
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={copied ? t("codeBlock.copied") : t("codeBlock.copy")}
          className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#c4644a] transition-colors ${
            copied ? "text-green-400" : "text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05]"
          }`}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? t("codeBlock.copied") : t("codeBlock.copy")}
        </button>
      )}
      {prefs.codeShowSaveButton && (
        <button
          type="button"
          onClick={() => void saveToFile()}
          aria-label={t("codeBlock.save")}
          className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#c4644a] transition-colors"
        >
          <Save size={11} />
          {t("codeBlock.save")}
        </button>
      )}
      <button
        ref={expandButtonRef}
        type="button"
        onClick={() => setExpanded(true)}
        aria-label={expandedLabel}
        className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#c4644a] transition-colors"
      >
        <Maximize2 size={11} />
        {expandedLabel}
      </button>
    </div>
  );

  return (
    <>
      <div
        className="mt-3 rounded-xl overflow-hidden border border-white/[0.07] bg-[#0f0e0c]"
        data-testid="code-viewer"
        data-language={lang}
        data-filename={filename}
      >
        <div className="flex items-center justify-between gap-3 px-4 py-2 bg-[#181512] border-b border-white/[0.06]">
          <ViewerIdentity filename={filename} lang={lang} />
          {headerActions}
        </div>
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeInOut" }}
              className="overflow-hidden"
            >
              <ViewerBody code={code} lang={lang} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {expanded && createPortal(
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-6"
          role="dialog"
          aria-modal="true"
          aria-label={`${filename} ${expandedLabel}`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeExpanded();
          }}
        >
          <div className="w-full h-full max-w-[1500px] max-h-[92vh] rounded-xl overflow-hidden border border-white/[0.1] bg-[#0f0e0c] shadow-2xl flex flex-col">
            <div className="flex items-center justify-between gap-3 px-4 py-2 bg-[#181512] border-b border-white/[0.06] shrink-0">
              <ViewerIdentity filename={filename} lang={lang} />
              <div className="flex items-center gap-0.5">
                {prefs.codeShowCopyButton && (
                  <button
                    type="button"
                    onClick={() => void copy()}
                    className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05]"
                  >
                    <Copy size={11} />
                    {t("codeBlock.copy")}
                  </button>
                )}
                {prefs.codeShowSaveButton && (
                  <button
                    type="button"
                    onClick={() => void saveToFile()}
                    className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05]"
                  >
                    <Save size={11} />
                    {t("codeBlock.save")}
                  </button>
                )}
                <button
                  ref={closeButtonRef}
                  type="button"
                  onClick={closeExpanded}
                  aria-label={t("codeBlock.closeExpanded")}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#c4644a]"
                >
                  <X size={12} />
                  {t("codeBlock.closeExpanded")}
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1">
              <ViewerBody code={code} lang={lang} expanded />
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

function InlineMarkdown({ text }: { text: string }) {
  const parts: Array<{ code: boolean; value: string }> = [];
  const inlineCode = /(?<!`)(`+)(?!`)([^\n]*?\S[^\n]*?)\1(?!`)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = inlineCode.exec(text)) !== null) {
    if (match.index > cursor) parts.push({ code: false, value: text.slice(cursor, match.index) });
    parts.push({ code: true, value: match[2] });
    cursor = inlineCode.lastIndex;
  }
  if (cursor < text.length) parts.push({ code: false, value: text.slice(cursor) });
  return (
    <>
      {parts.map((part, index) =>
        part.code ? (
          <code
            key={index}
            className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[0.9em] text-[#e2b59f]"
          >
            {part.value}
          </code>
        ) : part.value,
      )}
    </>
  );
}

function TextContent({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n{2,}/).map((paragraph, index) =>
        paragraph.trim() ? (
          <p
            key={index}
            className="text-sm text-[#c8c0b7] leading-relaxed whitespace-pre-wrap [&+p]:mt-3"
          >
            <InlineMarkdown text={paragraph} />
          </p>
        ) : null,
      )}
    </>
  );
}

export function MessageCodeContent({ content, filename }: { content: string; filename?: string }) {
  const segments = useMemo(() => parseMessageSegments(content), [content]);
  const codeSegmentCount = segments.filter((segment) => segment.type === "code").length;
  return (
    <>
      {segments.map((segment, index) =>
        segment.type === "code" ? (
          <CodeViewer
            key={`${index}-${segment.filename}`}
            lang={segment.lang ?? "plaintext"}
            code={segment.content}
            filename={segment.filenameExplicit ? segment.filename : codeSegmentCount === 1 ? (filename ?? segment.filename) : segment.filename}
          />
        ) : (
          <TextContent key={index} text={segment.content} />
        ),
      )}
    </>
  );
}

export const CODE_VIEWER_SUPPORTED_LANGUAGES = [...SUPPORTED_LANGUAGES].sort();
export const CODE_VIEWER_LANGUAGE_EXTENSIONS = LANGUAGE_EXTENSIONS;
