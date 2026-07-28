Redesign and extend the existing Siena_v2 desktop application UI based on the attached reference screens.

Important project context:
- The active product is Siena_v2.
- Siena_v2 is written in Python.
- ONRYO is deprecated and must not be used as the product name or architecture reference.
- The UI should remain implementation-friendly and suitable for a future Electron frontend, while the backend remains Python.
- Use the attached current screens as the main visual and structural reference.

Design goal:
Create a polished, modern, dark desktop AI assistant UI for Siena_v2.
The application is a local AI companion and developer-oriented assistant with chat, model delegation, memory, logs, tools, voice, and debug capabilities.

Very important visual direction:
- Do NOT use the old purple-heavy style as the primary design direction.
- Purple may appear only as a minor secondary accent if needed.
- The main style should use:
  - deep graphite / charcoal / near-black surfaces
  - warm burnt sienna / terracotta accents
  - soft neutral / cream text highlights
  - subtle glow and soft gradients
- The app should feel calm, premium, technical, and elegant.
- Avoid cyberpunk neon.
- Avoid generic chatbot UI.
- Avoid playful mascot styles.

Base this redesign on the existing references, but improve the information architecture and settings coverage.

Core product areas to design:

1. Splash / startup loading screen
- Keep and improve the startup warm-up screen.
- It should feel similar in spirit to Discord startup loading.
- Show Siena logo, app name, and loading progress.
- Include statuses like:
  - Initializing Siena
  - Loading local models
  - Warming up inference engine
  - Preparing memory
  - Starting tools
  - Initializing voice services
- The startup screen must clearly support model warm-up before the main window opens.

2. Main chat screen
- Keep the current desktop chat structure.
- Include:
  - left sidebar with app navigation and sessions
  - central chat area
  - right-side optional trace / inspector / live execution panel
  - bottom input area
- Show assistant responses clearly.
- Keep support for code blocks with actions.
- Show active model state somewhere visible.
- Show tool activity and delegated model usage cleanly.
- The chat UI should support a local assistant that can delegate tasks to specialist models.

3. Code rendering improvements
- Preserve code-focused design.
- Code blocks must support syntax highlighting by language.
- Highlight:
  - keywords
  - strings
  - comments
  - numbers
  - function names
  - types
  - inline code
- Show language badges for code blocks.
- Include actions:
  - copy
  - collapse / expand
  - apply patch
  - save snippet
- Make this feel closer to a premium AI coding assistant.

4. Navigation and information architecture
Use a clean left sidebar navigation.

Required navigation items:
- Chat
- Tool Trace
- Short Memory
- Long Memory
- Logs
- Models
- Runtime
- Debug
- Settings

Important:
- Add a dedicated Debug section.
- Debug must appear ABOVE Settings in the sidebar.
- The user must be able to switch to the Debug screen/window directly from the sidebar.
- Make the navigation feel like real desktop app sections, not just placeholders.

5. Debug screen
Create a dedicated Debug page / screen.
This page should be clearly visible and first-class.

The Debug page should include:
- runtime health summary
- active model state
- tool call debug info
- delegation events
- request / response timing
- memory extraction debug
- prompt/debug pipeline overview
- warning/error states
- optional raw payload viewer
- filters / tabs inside debug if needed

It should feel like an actual advanced debugging workspace for Siena_v2, not a generic logs page.

6. Settings redesign
Preserve the current settings structure but improve completeness and realism.

Required settings sections:
- Appearance
- Model settings
- Startup & preload
- Tool permissions
- Code rendering
- Voice
- Language
- Developer
- About (optional inside developer or standalone)

7. Language settings
Add a dedicated Language settings screen or section.

It must include:
- UI language selection
- preferred conversation language
- preferred input language
- preferred output language
- STT recognition language
- TTS speech language
- default language presets

Initial supported language emphasis:
- Russian
- English

The UI should clearly show that Siena_v2 supports multilingual interaction.

8. Voice settings
Add a dedicated Voice settings section.

Voice stack:
- STT provider: Whisper
- Main TTS provider: faster_qwen3-tts
- Fallback TTS provider: Silero

The Voice settings UI should include:

STT:
- enable / disable speech-to-text
- provider display: Whisper
- input device selector
- transcription language
- auto-send after speech option
- push-to-talk or continuous listening option
- VAD / sensitivity option if useful

TTS:
- enable / disable text-to-speech
- primary provider display: faster_qwen3-tts
- fallback provider display: Silero
- output device selector
- voice / speaker selection
- playback speed
- volume
- auto-play assistant messages
- fallback behavior explanation

Voice runtime indicators:
- listening
- transcribing
- speaking
- fallback active
- error states

This should feel production-ready and very clear.

9. Runtime and models
Keep the current “Models” and “Runtime” concepts, but make them cleaner and more informative.
The UI should reflect that:
- Siena remains the main intelligence
- specialist models assist on delegation
- the Python runtime executes tools
- Ollama/local models are connected

Show:
- active model
- standby models
- model roles
- warm-up / loaded / idle state
- runtime version
- backend info
- connected tools count
- memory state
- local service connection state

10. Tool Trace / Logs / Memory screens
Preserve and refine the existing ideas:
- Tool Trace
- Short Memory
- Long Memory
- Logs

They should look cohesive with the new design.
Prioritize readability, filtering, and developer usefulness.

11. Developer screen
Preserve and improve the Developer section.
Include:
- local API / runtime information
- IPC or integration options
- debug logging
- backend transport / bridge state
- environment details
- version/build info

12. Window switching and layout behavior
The UI should clearly support switching between major app sections/screens.
Design a desktop experience where the user can easily switch between:
- chat
- debug
- memory
- logs
- models
- settings

Use consistent transitions and active-state indicators.

13. Design system and components
Create reusable components for:
- sidebar navigation item
- section headers
- chips / status badges
- cards / panels
- code blocks
- toggles
- dropdowns
- segmented controls
- sliders
- search bars
- table/list rows
- debug event cards
- memory table rows
- model cards
- voice controls

14. Handoff / implementation awareness
The result should be realistic for implementation in Electron later, while staying aligned with a Python backend.
Prefer practical layouts and reusable desktop patterns.
Do not invent impossible interactions.

Also provide a markdown-style handoff / implementation note that explains:
- which new screens were added
- where Debug was introduced and why
- that Debug must appear above Settings in the sidebar
- that Language settings were added
- that Voice settings were added
- STT = Whisper
- TTS primary = faster_qwen3-tts
- TTS fallback = Silero
- what current placeholder / stub values should later be connected to real backend data
- what UI sections are mock data vs production integrations
- what needs backend integration to function

What to preserve from the current reference:
- overall desktop app feel
- chat-centric main workflow
- model delegation concept
- memory screens
- logs and trace concepts
- settings structure
- startup warm-up concept

What to improve:
- remove purple as the dominant style
- strengthen warm sienna / terracotta identity
- add Debug section
- place Debug above Settings
- add Language settings
- add Voice settings
- reflect Whisper / faster_qwen3-tts / Silero
- improve navigation clarity
- improve implementation realism

Deliverables:
- updated splash screen
- updated main chat
- updated settings suite
- dedicated Debug screen
- Language settings screen/section
- Voice settings screen/section
- updated sidebar/navigation
- improved models/runtime screens
- design system/components
- markdown handoff notes