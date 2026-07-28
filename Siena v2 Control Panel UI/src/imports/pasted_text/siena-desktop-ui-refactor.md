Refine the current Siena_v2 desktop UI with improved chat composer, attachments, clipboard handling, and response feedback.

Use the current Siena_v2 design as the base. Preserve the dark warm Siena style, terracotta accents, Python backend context, local model workflow, Tool Trace, Memory, Logs, Models, Runtime, Debug, Settings, Voice, and Language sections.

Add the following UX improvements:

1. Expandable chat input / composer

Improve the bottom message input area.

Behavior:
- Pressing Shift + Enter should create a new line.
- When Shift + Enter creates a new line, the composer height should grow upward.
- When the user types long text and reaches the text boundary, the composer should automatically grow upward.
- The input should start as a compact one-line field.
- It should expand smoothly up to a reasonable maximum height.
- After reaching max height, the text area should become internally scrollable.
- The send button, microphone button, attachment button, and model/priority controls should remain aligned and usable while the composer grows.
- The expansion animation should be smooth and premium, similar to modern AI chat apps.

Visual requirements:
- The expanded composer should look like a large rounded message panel.
- It should support multi-line text comfortably.
- It should not cover the message history aggressively.
- When expanded, it should feel intentional, not broken.
- Keep the style consistent with Siena: dark graphite surface, warm terracotta accents, soft borders, calm premium animation.

2. File attachment button

Add a visible attachment button to the composer.

Requirements:
- Add a “+” or paperclip button on the left side of the input area.
- Clicking it should open an attachment menu or file picker.
- The menu should support:
  - attach image
  - attach document
  - attach text/code file
  - attach folder/project file, optional developer mode
- Attached files should appear as preview chips/cards above the text input.
- Each attached file preview should show:
  - file icon
  - file name
  - file type
  - file size if useful
  - remove button
- Image attachments should show a small thumbnail preview.
- The attachment area should be visually connected to the composer.

3. Clipboard paste behavior

Design paste handling for images and text from clipboard.

Behavior:
- If the user pastes an image from the clipboard, show it as an attachment preview above the input text area.
- If the user pastes a file from clipboard, show it as a file preview above the input.
- If the user pastes plain text, insert it into the composer normally.
- If the pasted text is large, show a compact “pasted text” preview/card above the composer and optionally insert a short placeholder into the input.
- If the pasted content is code, detect it and show a code/text attachment preview with language badge if possible.
- The user should be able to remove any pasted attachment before sending.
- The user should be able to add a message together with the pasted image/file/text.

Visual:
- Show pasted image/file/text previews above the composer, not floating randomly.
- The preview area should expand the composer upward.
- Use compact rounded cards.
- Include close/remove buttons.
- Keep the design clean and not cluttered.

4. Response feedback controls

Add feedback/actions under every assistant response.

Under each Siena assistant message, show a small action row with:
- Like button
- Dislike button
- Copy button
- Optional regenerate button
- Optional save to memory button
- Optional report/debug button in developer mode

Behavior:
- Buttons should be subtle and not distract from reading.
- They can appear on hover or always be lightly visible.
- Like/dislike should show selected state.
- Copy should give a small “Copied” confirmation.
- For code responses, keep code-block copy button separately, but still show the response-level copy button under the whole assistant answer.
- Feedback buttons should not appear under user messages, only assistant/model responses.

5. Attachment-aware message rendering

When the user sends attachments:
- Show attached images/files/text cards inside the user message bubble or directly above it.
- Images should appear as thumbnails with click-to-preview.
- Documents should appear as file cards.
- Pasted text/code should appear as collapsible preview cards.
- The assistant response should visually indicate when it is analyzing attachments.

Examples:
- User attaches screenshot + writes “что не так?”
- User pastes image from clipboard + adds text
- User attaches Python file + asks Siena to review it
- User pastes long error log + asks for debugging

6. Composer states

Design the composer in multiple states:
- default empty state
- typing one-line message
- expanded multiline message
- with image attachment
- with file attachment
- with pasted text/code attachment
- recording voice / STT active
- disabled while model is generating, optional
- error state if attachment type is unsupported

7. Integration notes / handoff

Update the markdown handoff notes to explain:
- how auto-growing composer should work
- Shift + Enter = newline
- Enter = send
- max composer height and internal scrolling
- how file attachment previews should be represented
- how clipboard paste should be handled
- how image paste should become attachment preview
- how large pasted text should become a text attachment/preview
- how assistant response feedback should be stored
- what backend/frontend stubs are needed

Mention required frontend events:
- composer:textChanged
- composer:submit
- composer:newLine
- attachment:add
- attachment:remove
- clipboard:imagePasted
- clipboard:textPasted
- feedback:like
- feedback:dislike
- feedback:copy
- response:copy
- codeBlock:copy

Mention backend integration placeholders:
- file upload / local file reference handling
- image preview generation
- attachment metadata extraction
- pasted text storage
- feedback persistence
- message ID tracking
- clipboard permission handling
- STT/TTS interaction with composer

Important:
- Keep everything realistic for Electron + React or Electron + HTML/CSS/JS.
- Do not redesign the whole app from scratch.
- Refine the existing Siena_v2 UI.
- Keep Debug above Settings in sidebar.
- Keep Voice and Language settings.
- Keep the warm dark Siena style.