---
description: Extract transcript, metadata, and top comments from a YouTube video - Claude reads the transcript and writes the summary directly (no LLM API call, no API key needed for this part), saved to vault. Add --visual to also read the video's frames (scene detection)
category: research
triggers_en: ["summarize youtube", "youtube transcript", "extract video", "youtube to vault", "watch this video", "what's on screen in this video"]
triggers_es: ["resume este vídeo de youtube", "transcripción de youtube", "extrae este vídeo", "youtube al vault", "mira este vídeo", "qué se ve en este vídeo"]
triggers_pt: ["resuma este vídeo do youtube", "transcrição do youtube", "extraia este vídeo", "youtube para o vault", "assista a este vídeo", "o que aparece neste vídeo"]
---

Use the obsidian-second-brain skill. Execute `/youtube [url] [--visual]`:

1. Resolve the YouTube URL or video ID from the user's argument. Accept any of: full URL (`https://www.youtube.com/watch?v=...`), `https://youtu.be/...`, `https://www.youtube.com/shorts/...`, or just the 11-character video ID. If no input given, ask: "Which YouTube video?"

2. Run the script from the skill root (its absolute path was given at session start as **Skill root**; substitute it for `SKILL_ROOT`):
   ```bash
   uv run --directory "SKILL_ROOT" -m scripts.research.youtube_extract "<url-or-id>"
   ```
   Add `--visual` when the user wants the video watched, not just transcribed (demos, whiteboards, slides, UI walkthroughs, b-roll, "what's on screen"). Optional `--max-frames N` caps how many frames are read (default 24):
   ```bash
   uv run --directory "SKILL_ROOT" -m scripts.research.youtube_extract "<url-or-id>" --visual
   ```

3. The script only does the mechanical part - there is no LLM API call in it, so no Grok/Gemini/OpenAI key is needed for the text summary:
   - Extracts the transcript via `youtube-transcript-api` (free, no API key; falls back through available caption languages if English isn't one of them).
   - If `YOUTUBE_API_KEY` is set, also fetches title, channel, view/like counts, top comments. Otherwise skips metadata silently.
   - Saves a skeleton AI-first note to the vault: frontmatter, `## Video`, and a `## Summary` section holding a `SUMMARY_PLACEHOLDER` comment.
   - Prints a `TRANSCRIPT-FOR-CLAUDE` JSON block (to stderr) with the transcript, top comments, and the saved note's path.
   - With `--visual`: downloads the video (yt-dlp, <=720p) and extracts one frame per scene change (ffmpeg scene detection, not a fixed timer), so visual substance is captured. Hero frames are copied into the vault and embedded in the note; the full keyframe set is left on disk for you to read. Requires `yt-dlp` and `ffmpeg` on PATH (`brew install yt-dlp ffmpeg`). If either is missing or download fails, the visual layer is skipped with a warning and the note still saves.

4. **Write the summary yourself** from the transcript (and comments) in the `TRANSCRIPT-FOR-CLAUDE` block - this is your own reading of the transcript, the same way you read the frames for `--visual` below; there is no external LLM call. Produce EXACTLY this structure:

   ```
   ## TL;DR
   [2-3 frases em português (pt-BR) capturando a tese central ou o valor do vídeo.]

   ## Key Points
   - [Afirmação, ideia ou método concreto do vídeo, em português (pt-BR)]
   - [...continue for 5-12 bullets covering the actual substance, not filler]

   ## Notable Quotes
   - "[Verbatim quote in the original language]" (tradução: "[pt-BR translation]") - [locate with timestamp/context if you can; max 5 quotes]

   ## Themes & Topics
   [2-3 frases em português (pt-BR) nomeando os temas/domínios mais amplos que o vídeo toca]

   ## Comment Sentiment
   [If comments were provided: 1-2 frases em português (pt-BR) resumindo a reação da audiência. Note dominant praise, criticism, or question patterns. If no comments provided, write "Nenhum comentário disponível."]

   ## Worth Following Up On
   - [Pontos específicos mencionados que valeriam uma chamada /research mais profunda depois, em português (pt-BR)]
   ```

   Rules:
   - **Language: Brazilian Portuguese (pt-BR) throughout the prose** - correct grammar, natural phrasing, standard orthography (post-1990 Acordo Ortográfico). Applies regardless of the transcript's source language (English, Spanish, etc.): translate/paraphrase into pt-BR, don't leave sections in the original language, don't write stiff/literal machine-translated Portuguese - write as a fluent native speaker would. Exception: verbatim quotes in Notable Quotes stay in the original language, with a pt-BR translation alongside. Section headers stay in English, matching the rest of the vault's note templates.
   - Be specific. "Fala sobre IA" is useless to future-Claude. "Argumenta que janelas de contexto de LLM acima de 1M tokens degradam a qualidade do raciocínio após 200k tokens" is useful.
   - Don't pad. If a section is genuinely thin, write one bullet and move on.
   - Don't add commentary outside this structure.
   - If the transcript was unavailable (script printed `"(transcript not available)"`), write the best summary you can from title/metadata/comments alone, and say plainly in the TL;DR that no transcript was available - don't fabricate content you can't source.

   Then **Edit the note** at the `note_path` given in the `TRANSCRIPT-FOR-CLAUDE` block: replace the `SUMMARY_PLACEHOLDER` HTML comment under `## Summary` with the text you just wrote.

5. Show the finished summary to the user in chat (the same text you just wrote into the note).

6. **Default save behavior: saves automatically**, as soon as the script runs (step 3), before you write the summary. AI-first note written to `Research/YouTube/YYYY-MM-DD - <video-title-slug>.md`. Frontmatter includes video ID, channel, view counts, `visual`, `frame-count`, etc. for future Dataview queries.

7. **If `--visual` was used:** the script also prints a `FRAMES-FOR-CLAUDE` JSON block (to stderr) listing each extracted keyframe with its timestamp and local path, plus the saved note path. Do this:
   - Read each frame image with the read-files tool (they are local JPGs). This is your own vision doing the watching - there is no extra vision-API call.
   - Then edit the saved note: replace the `<!-- CLAUDE: ... -->` comment in the `## Visual notes` section with a concise, timestamp-keyed reading of what the frames add over the transcript: on-screen text, code, diagrams, slides, UI, demos, b-roll, scene transitions. Cross-reference the Summary; do not restate the audio.
   - Keep the `## Visual timeline` hero-frame embeds intact.
   - If `--max-frames` is high and reading every frame would be excessive, read the hero frames plus an even sample across the timeline, and say in the note that you sampled.

8. Plain English triggers: "summarize this YouTube video", "what's in this video", "extract this YouTube link", "transcribe this video", "watch this video", "what's on screen", or just pasting a YouTube URL with a question about content. When the ask is about what is *shown* (not just said), use `--visual`.

9. If the video has no captions (transcript unavailable) AND no metadata (no API key), the script will fail with a clear message - surface it. Suggest the user picks a different video, adds a `YOUTUBE_API_KEY`, or tries `--visual` (which can read the video even without captions).

10. If the user asks to research something mentioned in the "Worth Following Up On" section, route that to `/research [topic]`.

---

**AI-first rule:** Every note created or updated by this command MUST follow `references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `references/ai-first-rules.md`.
