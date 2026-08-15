#!/usr/bin/env python3
"""/youtube [url] [--visual] - extract transcript, metadata, and top comments from a YouTube video.

Transcript: free, no API key needed (youtube-transcript-api).
Metadata + comments: free, requires YOUTUBE_API_KEY (Data API v3).
Without YOUTUBE_API_KEY: transcript-only mode.

No LLM API call for the summary. This script only does the mechanical part
(fetch transcript/metadata/comments, save a skeleton AI-first note) and prints
the transcript in a TRANSCRIPT-FOR-CLAUDE block; Claude (the agent running this
command) reads it and writes the ## Summary section itself, per commands/youtube.md,
then edits it into the note this script already saved. Same shape as the --visual
frame-reading flow below, just applied to every run now instead of only visual ones.

--visual: also download the video and extract one frame per scene change (ffmpeg
scene detection, via lib/video_frames.py). Hero frames are saved into the vault
and the full keyframe set is printed for Claude to read with its own vision and
write the Visual notes section. Requires yt-dlp + ffmpeg on PATH. Ported idea from
claude-watch (MIT, github.com/taoufik123-collab/claude-watch).

Default behavior: print to chat AND save an AI-first note (skeleton) to
Research/YouTube/; Claude fills in ## Summary (and ## Visual notes, if --visual)
via Edit right after this script returns.
"""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .lib import vault, video_frames, youtube

# Frames the visual layer reads by default. Kept modest: each frame is an image
# Claude reads, so cost scales with count. Override with --max-frames.
DEFAULT_VISUAL_MAX_FRAMES = 24

SUMMARY_PLACEHOLDER = (
    "<!-- CLAUDE: read the transcript (and comments, if present) in the "
    "TRANSCRIPT-FOR-CLAUDE block this script printed (stderr), then replace this "
    "comment with the summary. Structure and the Brazilian Portuguese language "
    "rule are in commands/youtube.md step 4 - follow them exactly. -->"
)


def _fmt_ts(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _extract_visual(video_id: str, title: str, max_frames: int) -> dict | None:
    """Download the video, extract scene-change frames, copy hero frames into the vault.

    Returns {frame_dir, frames, hero_rel_paths, count} or None if the visual layer
    could not run (missing binaries, download failure). Never raises - the visual
    layer is additive; the note still saves on its own.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    work_dir = Path(tempfile.mkdtemp(prefix=f"youtube-visual-{video_id}-"))
    try:
        print("[/youtube] Downloading video for scene extraction...", file=sys.stderr)
        dl = video_frames.download_video(url, work_dir)
        print("[/youtube] Extracting scene-change frames...", file=sys.stderr)
        frames = video_frames.extract_scene_change(
            dl["video_path"], work_dir / "frames", max_frames=max_frames
        )
        if not frames:
            return None
        hero = video_frames.select_hero_frames(frames)

        # Persist hero frames into the vault so the embeds survive; the full set
        # stays in work_dir for Claude to read this session.
        date = datetime.now().strftime("%Y-%m-%d")
        slug = vault.slugify(title) or "untitled"
        att_dir = vault.VAULT_PATH / vault.SUBFOLDERS["youtube"] / "attachments" / f"{date}-{slug}"
        att_dir.mkdir(parents=True, exist_ok=True)
        hero_rel: list[tuple[str, float]] = []
        for i, f in enumerate(hero):
            dest = att_dir / f"hero_{i:02d}.jpg"
            shutil.copy2(f["path"], dest)
            rel = dest.relative_to(vault.VAULT_PATH).as_posix()
            hero_rel.append((rel, f["timestamp_seconds"]))

        return {
            "frame_dir": str(work_dir / "frames"),
            "frames": frames,
            "hero_rel": hero_rel,
            "count": len(frames),
            "source": frames[0].get("source", "scene-change"),
        }
    except video_frames.FrameError as e:
        print(f"[/youtube] Visual layer skipped: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001 - visual layer must never break the note
        print(f"[/youtube] Visual layer error ({type(e).__name__}): {e}", file=sys.stderr)
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="/youtube", add_help=False)
    parser.add_argument("url", nargs="?", default="")
    parser.add_argument("--visual", action="store_true",
                        help="download video + extract scene-change frames for Claude to read")
    parser.add_argument("--max-frames", type=int, default=DEFAULT_VISUAL_MAX_FRAMES,
                        help=f"cap on frames the visual layer reads (default {DEFAULT_VISUAL_MAX_FRAMES})")
    args = parser.parse_args(argv[1:])

    if not args.url.strip():
        print("Usage: /youtube <video-url-or-id> [--visual] [--max-frames N]", file=sys.stderr)
        return 2

    try:
        video_id = youtube.parse_video_id(args.url.strip())
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(f"[/youtube] Extracting video {video_id}...", file=sys.stderr)

    transcript = youtube.get_transcript(video_id)
    metadata = youtube.get_video_metadata(video_id)
    comments = youtube.get_top_comments(video_id, max_results=15)

    if not transcript and not metadata:
        print("❌ Could not fetch transcript or metadata. Is the video public and does it have captions?", file=sys.stderr)
        return 1

    title = (metadata or {}).get("title") or f"Video {video_id}"
    channel = (metadata or {}).get("channel") or "(unknown channel)"
    published = (metadata or {}).get("published_at") or "(unknown date)"

    # Visual layer (opt-in). Run before the save so failures surface early.
    visual = _extract_visual(video_id, title, args.max_frames) if args.visual else None

    if transcript:
        TX_LIMIT = 24000  # plenty for Claude to read directly, keeps the block manageable
        tx_truncated = transcript[:TX_LIMIT]
        tx_note = "" if len(transcript) <= TX_LIMIT else f"\n\n[Transcript truncated at {TX_LIMIT} chars from total {len(transcript)} chars]"
    else:
        tx_truncated = "(transcript not available)"
        tx_note = ""

    comments_section = ""
    if comments:
        comments_section = "TOP COMMENTS (relevance order):\n"
        for c in comments[:15]:
            comments_section += f"- {c['author']} ({c['like_count']} 👍): {c['text'][:200]}\n"

    print(f"# {title}")
    print(f"**Channel:** {channel} · **Published:** {published}")
    print(f"**URL:** https://www.youtube.com/watch?v={video_id}\n")

    # AI-first save (skeleton - Claude fills in ## Summary next, per commands/youtube.md)
    now = datetime.now()
    preamble = (
        f"For future Claude: This note is a transcript-grounded summary of YouTube video \"{title}\" "
        f"by {channel} (published {published}), processed on {now.strftime('%Y-%m-%d %H:%M')}. "
        f"Transcript was extracted via youtube-transcript-api. The Summary below was written "
        f"directly by Claude reading the transcript in-session - no external LLM API call. "
        f"Quotes are sourced from the transcript verbatim where attributed. Use Worth Following Up On bullets to spawn deeper research."
    )
    if visual:
        preamble += (
            f" Visual layer: {visual['count']} keyframes were extracted via ffmpeg "
            f"{visual['source']} detection; the Visual notes section is Claude's own reading of those frames."
        )
    fm = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "type": "youtube",
        "video-id": video_id,
        "video-url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "channel": channel,
        "channel-id": (metadata or {}).get("channel_id", ""),
        "published": published,
        "view-count": (metadata or {}).get("view_count"),
        "like-count": (metadata or {}).get("like_count"),
        "comment-count": (metadata or {}).get("comment_count"),
        "duration": (metadata or {}).get("duration"),
        "visual": bool(visual),
        "frame-count": visual["count"] if visual else None,
        "tags": ["research", "youtube"] + ((metadata or {}).get("tags") or [])[:5],
        "cost-usd": 0.0,
        "ai-first": True,
    }
    note_body = (
        f"## For future Claude\n\n{preamble}\n\n"
        f"## Video\n\n"
        f"- **Title:** {title}\n"
        f"- **Channel:** {channel}\n"
        f"- **Published:** {published}\n"
        f"- **URL:** https://www.youtube.com/watch?v={video_id}\n\n"
        f"## Summary\n\n{SUMMARY_PLACEHOLDER}\n"
    )
    if visual:
        note_body += "\n" + _visual_note_section(visual)

    path = vault.write_note("youtube", title, fm, note_body)
    vault.print_save_links(path)
    vault.append_to_log(f"youtube on \"{title}\" - saved to {path.name}")

    _print_transcript_for_claude(path, tx_truncated + tx_note, comments_section)

    if visual:
        _print_frames_for_claude(path, visual)

    print(
        f"---\n[transcript: {len(transcript) if transcript else 0} chars"
        f" · comments: {len(comments)}"
        + (f" · frames: {visual['count']}" if visual else "")
        + "]",
        file=sys.stderr,
    )
    return 0


def _visual_note_section(visual: dict) -> str:
    """Hero frame embeds + a pending marker for Claude to fill the visual reading."""
    lines = [
        "## Visual timeline",
        "",
        f"Key frames from ffmpeg {visual['source']} detection "
        f"({visual['count']} extracted). Representative frames embedded below:",
        "",
    ]
    for rel, ts in visual["hero_rel"]:
        lines.append(f"![[{rel}]]")
        lines.append(f"*{_fmt_ts(ts)}*")
        lines.append("")
    lines += [
        "## Visual notes",
        "",
        "<!-- CLAUDE: read the keyframes listed in the FRAMES-FOR-CLAUDE block the "
        "script printed, then replace this comment with visual analysis keyed by "
        "timestamp - on-screen text, code, diagrams, UI, b-roll, scene transitions. "
        "Cross-reference the transcript and Summary above; do not restate the audio. -->",
        "",
    ]
    return "\n".join(lines)


def _print_transcript_for_claude(note_path: Path, transcript_text: str, comments_section: str) -> None:
    """Emit a machine-readable block with the transcript for Claude to read and
    summarize itself, then telling it which note to edit with the result."""
    payload = {
        "note_path": str(note_path),
        "instruction": (
            "Read the transcript (and comments, if present) below, write the "
            "## Summary content per commands/youtube.md step 4 (structure + the "
            "Brazilian Portuguese language rule), then Edit the note at note_path: "
            "replace the SUMMARY_PLACEHOLDER comment under '## Summary' with the "
            "finished summary."
        ),
        "transcript": transcript_text,
        "comments": comments_section or None,
    }
    print("\n=== TRANSCRIPT-FOR-CLAUDE ===", file=sys.stderr)
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
    print("=== END TRANSCRIPT-FOR-CLAUDE ===", file=sys.stderr)


def _print_frames_for_claude(note_path: Path, visual: dict) -> None:
    """Emit a machine-readable block telling Claude which frames to read and which
    note to update. Claude reads the frames with its own vision, then edits the
    note's Visual notes section."""
    payload = {
        "note_path": str(note_path),
        "instruction": (
            "Read each frame below (they are local JPGs), then Edit the note at "
            "note_path: replace the <!-- CLAUDE: ... --> comment in the '## Visual "
            "notes' section with a concise, timestamp-keyed visual reading. Focus on "
            "what the frames add over the transcript: on-screen text/code, diagrams, "
            "UI, demos, b-roll, scene transitions."
        ),
        "frames": [
            {"timestamp": _fmt_ts(f["timestamp_seconds"]), "path": f["path"]}
            for f in visual["frames"]
        ],
    }
    print("\n=== FRAMES-FOR-CLAUDE ===", file=sys.stderr)
    print(json.dumps(payload, indent=2), file=sys.stderr)
    print("=== END FRAMES-FOR-CLAUDE ===", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
