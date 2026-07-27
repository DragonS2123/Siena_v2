# Voice

Push-to-talk uses whisper.cpp with WAV input. TTS uses the local qwentts.cpp
server and stored voice profiles. Status endpoints never start either provider.
Subprocesses start only for an explicit transcription/synthesis or lifecycle
action.
