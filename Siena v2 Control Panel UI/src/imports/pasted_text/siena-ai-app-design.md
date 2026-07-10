Design a modern desktop AI assistant application called “Siena”.

Product concept:
Siena is a warm, intelligent, local AI companion. The application should feel premium, fluid, animated, and emotionally calm. It is not a generic chatbot, not a corporate SaaS dashboard, and not a cyberpunk interface. It should look like a real desktop product that could later be integrated into Electron.

Platform:
Desktop application UI, designed to be easy to implement in Electron later.
The design should be modular, component-based, and developer-friendly.

Core design goals:
- Premium desktop app feel
- Smooth and modern animations
- Clean, readable layout
- Warm, intelligent, calm visual identity
- Easy to adapt into an Electron-based app
- Suitable for a local AI assistant with model management and coding capabilities

Visual style:
- Minimal but rich interface
- Dark theme first, with optional light theme
- Smooth rounded corners
- Subtle gradients and soft glow
- Slightly animated feel, but not flashy
- Warm terracotta / burnt sienna accents
- Graphite / charcoal base colors
- Cream or soft neutral text highlights
- Elegant, slightly futuristic, but not neon or cyberpunk

Brand:
Use the “Siena” identity consistently across the app.
If logo placement is needed, use a refined abstract “S” or Siena wordmark.

Create the following screens and flows:

1. Splash / startup loading screen
- A startup loading screen similar in spirit to Discord loading
- Show Siena logo, app name, and a clean loading/progress animation
- Include status text such as:
  - “Initializing Siena”
  - “Loading local models”
  - “Warming up inference engine”
  - “Preparing memory”
  - “Starting voice and tools”
- The loading screen should feel polished and alive, with subtle animation and progress feedback
- This screen is important because models may need warm-up before the main app opens

2. Main chat screen
- Main conversation area
- Sidebar for chats / sessions
- Input area with large modern prompt box
- Message bubbles or message cards
- Support for normal conversation, tool outputs, system notes, and code responses
- Clear distinction between assistant, user, and system/tool messages
- Optional status area showing which local model is active

3. Code-aware response UI
- Responses containing code must display proper code blocks
- Syntax highlighting should visually adapt depending on the detected programming language
- Keywords, strings, comments, numbers, and function names should be visually differentiated
- Inline code should also be styled clearly
- Language label should be visible on code blocks (for example: Python, C#, JavaScript, JSON, Bash)
- Include copy button, collapse/expand button, and optional “apply” or “use patch” button for code blocks
- Important words and technical tokens should stand out clearly
- The UI should feel like a mix of a premium chat assistant and a lightweight developer tool

4. Animated interface behavior
- Add subtle but meaningful animations:
  - sidebar open/close
  - hover transitions
  - message appearance
  - loading indicators
  - typing/thinking state
  - tab switching
  - button feedback
- Motion should feel smooth and premium, not distracting
- The design should feel more alive than a static chat app

5. Model / status panel
- Add a panel or widget showing local model status
- Show active model, warm-up state, loading state, and readiness
- Show whether the assistant is idle, thinking, generating, or using tools
- Keep this visually elegant and compact

6. Settings / integration screen
- Create a settings screen that looks realistic for a desktop app
- Include sections such as:
  - Appearance
  - Model settings
  - Startup / preload behavior
  - Tool permissions
  - Developer options
  - Code rendering options
- Include a section that makes the app easy to imagine as an Electron app
- Use a layout that would be easy to wire to real settings later

7. Developer / integration friendliness
- The design should be component-based and easy to translate into Electron
- Prefer reusable desktop UI patterns
- Make sure the app structure can be implemented with HTML/CSS/JS or React inside Electron
- Avoid layout ideas that would be extremely hard to implement in Electron
- Use a clean information hierarchy and practical spacing

Deliverables:
- Splash / loading screen
- Main chat screen
- Code response example screen
- Settings screen
- Components / design system section
- Basic interaction notes
- Light and dark theme considerations if possible

Important UI requirements:
- Code syntax highlighting by language
- Clear styling for code keywords and important tokens
- Smooth animated behavior
- Startup loading/warmup screen
- Design should be realistic and implementation-friendly
- Easy future integration into Electron
- Desktop-first experience

Avoid:
- Generic chatbot templates
- Overly corporate SaaS look
- Cyberpunk neon overload
- Robot mascots
- Overcomplicated futuristic HUD visuals
- Unreadable tiny text
- Layouts that are difficult to implement in Electron