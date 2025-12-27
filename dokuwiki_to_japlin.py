#!/usr/bin/env python3
"""
DokuWiki -> Markdown (Joplin-friendly) migrator

- Reads DokuWiki .txt pages from:  C:\migrate\pages
- Copies media from:              C:\migrate\media
- Writes output to:              C:\migrate\output\notes and \media
- Preserves full folder hierarchy
- Decodes URL-encoded Greek filenames in media (and updates references)
- Converts DokuWiki links/media syntax to Markdown
- Removes {.*} blocks (including {.align-center})
- Strips any query string after filenames (?800, ?600x400, etc.)
- Normalizes path separators to "/"
- Idempotent and safe (never modifies sources)
- Python >= 3.9, no external dependencies
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote
import posixpath


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
# Add more if you want treated as "downloadable link"
DOWNLOADABLE_EXTS = {".pdf", ".zip", ".docx", ".xlsx", ".pptx", ".txt", ".csv", ".rtf"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def to_posix(p: str) -> str:
    return p.replace("\\", "/")


def decode_path_segments(path_like: str) -> str:
    """
    Decode URL-encoded sequences for each segment independently.
    Keeps '/' separators (posix style) in the returned string.
    """
    parts = to_posix(path_like).split("/")
    decoded = [unquote(part) for part in parts if part != ""]
    # Preserve leading/trailing slashes? For our use, we don't want them.
    return "/".join(decoded)


def strip_query(filename_or_path: str) -> str:
    """
    Remove ANY query string after a filename/path.
    Preserves anchors (#...) if present, but removes ?... before it.
    Examples:
      "image.png?800" -> "image.png"
      "x.pdf?download#p=2" -> "x.pdf#p=2"
    """
    s = filename_or_path
    if "?" not in s:
        return s
    # If there's an anchor, keep it, but drop query.
    if "#" in s:
        before_hash, after_hash = s.split("#", 1)
        before_q = before_hash.split("?", 1)[0]
        return before_q + "#" + after_hash
    return s.split("?", 1)[0]


def relposix(from_dir: str, to_path: str) -> str:
    """
    POSIX relative path from from_dir to to_path.
    Both inputs are POSIX-style relative paths (no drive letters).
    """
    rel = posixpath.relpath(to_path, start=from_dir if from_dir else ".")
    return rel.replace("\\", "/")


def write_text_if_changed(dst: Path, text: str, encoding: str = "utf-8") -> None:
    """
    Idempotent write: only overwrite if content differs.
    """
    if dst.exists():
        try:
            old = dst.read_text(encoding=encoding, errors="replace")
            if old == text:
                return
        except Exception:
            # If read fails, just overwrite.
            pass
    safe_mkdir(dst.parent)
    dst.write_text(text, encoding=encoding, newline="\n")


def copy_file_if_changed(src: Path, dst: Path) -> None:
    """
    Idempotent copy: copies only if dst missing or differs by size/sha256.
    Uses copy2 to preserve timestamps where possible.
    """
    safe_mkdir(dst.parent)
    if not dst.exists():
        shutil.copy2(src, dst)
        return

    try:
        if src.stat().st_size == dst.stat().st_size:
            # Quick check: compare hashes only if sizes match
            if sha256_file(src) == sha256_file(dst):
                return
    except Exception:
        # If something fails, fall back to overwrite
        pass

    shutil.copy2(src, dst)


def unique_path_if_needed(dst: Path) -> Path:
    """
    If dst exists and is a different file, generate a unique name by adding suffixes.
    This helps when two different encoded filenames decode to the same Unicode filename.
    """
    if not dst.exists():
        return dst

    stem = dst.stem
    suffix = dst.suffix
    parent = dst.parent

    for i in range(1, 10_000):
        candidate = parent / f"{stem}__dup{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many filename collisions under: {parent}")


def build_page_index(pages_root: Path) -> dict[str, str]:
    """
    Map DokuWiki page IDs to output notes relative paths (POSIX with .md).
    - DokuWiki IDs use ":" as namespace separator.
    - We also store variants with leading ":".
    """
    idx: dict[str, str] = {}
    for src in pages_root.rglob("*.txt"):
        rel = src.relative_to(pages_root)
        rel_noext = rel.with_suffix("")  # e.g. foo/bar/page
        rel_posix = to_posix(str(rel_noext))
        dokuwiki_id = rel_posix.replace("/", ":")  # foo:bar:page
        out_md_rel = rel_posix + ".md"  # foo/bar/page.md

        idx[dokuwiki_id] = out_md_rel
        idx[":" + dokuwiki_id] = out_md_rel
    return idx


def build_media_index_and_copy(media_root: Path, out_media_root: Path) -> dict[str, str]:
    """
    Copy all media to output/media (decoded names) preserving hierarchy.
    Build a lookup index from multiple key forms -> decoded output relative path (POSIX).
    """
    idx: dict[str, str] = {}

    for src in media_root.rglob("*"):
        if not src.is_file():
            continue

        rel_src = src.relative_to(media_root)  # may contain URL-encoded parts on disk
        rel_src_posix = to_posix(str(rel_src))  # e.g. ns/%CE%B3.../file.png

        rel_decoded_posix = decode_path_segments(rel_src_posix)
        rel_decoded_posix = strip_query(rel_decoded_posix)  # just in case
        out_rel_posix = rel_decoded_posix  # output preserves decoded hierarchy

        dst = out_media_root / Path(out_rel_posix)
        # Handle collisions caused by decoding
        dst_unique = unique_path_if_needed(dst)
        copy_file_if_changed(src, dst_unique)

        # If we had to uniquify, adjust output rel path accordingly
        out_rel_posix_final = to_posix(str(dst_unique.relative_to(out_media_root)))

        # Index keys (strip query always)
        key_variants = set()

        # Raw as on disk
        key_variants.add(strip_query(rel_src_posix))
        key_variants.add(strip_query(":" + rel_src_posix))
        key_variants.add(strip_query(rel_src_posix.replace("/", ":")))
        key_variants.add(strip_query(":" + rel_src_posix.replace("/", ":")))

        # Decoded
        key_variants.add(strip_query(rel_decoded_posix))
        key_variants.add(strip_query(":" + rel_decoded_posix))
        key_variants.add(strip_query(rel_decoded_posix.replace("/", ":")))
        key_variants.add(strip_query(":" + rel_decoded_posix.replace("/", ":")))

        # Also add fully unquoted of raw (covers partial encoding oddities)
        key_variants.add(strip_query(unquote(rel_src_posix)))
        key_variants.add(strip_query(":" + unquote(rel_src_posix)))
        key_variants.add(strip_query(unquote(rel_src_posix).replace("/", ":")))
        key_variants.add(strip_query(":" + unquote(rel_src_posix).replace("/", ":")))

        for k in key_variants:
            idx[k] = out_rel_posix_final

    return idx


def current_page_id_from_rel(rel_md_posix: str) -> str:
    """
    Given a page output relative path like 'a/b/page.md', return DokuWiki-like id: 'a:b:page'
    """
    rel_noext = rel_md_posix[:-3] if rel_md_posix.lower().endswith(".md") else rel_md_posix
    return rel_noext.replace("/", ":")


def resolve_page_target(raw_target: str, current_id: str, page_index: dict[str, str]) -> str:
    """
    Resolve DokuWiki page link target to output .md relative path (POSIX).

    Rules:
    - If target starts with ":" => absolute from root.
    - If target contains ":" => treat as absolute-ish namespace path.
    - Else => relative to current namespace (folder of current_id).
    """
    t = raw_target.strip()
    t = unquote(t)
    t = strip_query(t)

    anchor = ""
    if "#" in t:
        t, anchor = t.split("#", 1)
        anchor = "#" + anchor

    if not t:
        return ""  # nothing to link to

    is_absolute = t.startswith(":")
    if is_absolute:
        t2 = t[1:]
    else:
        t2 = t

    if ":" in t2 or is_absolute:
        resolved_id = t2
    else:
        # relative: prepend current namespace (everything before last ":")
        ns = current_id.rsplit(":", 1)[0] if ":" in current_id else ""
        resolved_id = f"{ns}:{t2}" if ns else t2

    # Normalize repeated separators/spaces
    resolved_id = re.sub(r"\s+", " ", resolved_id).strip()

    out_md = page_index.get(resolved_id) or page_index.get(":" + resolved_id)
    if out_md is None:
        # Fallback: generate a path based on id
        out_md = resolved_id.replace(":", "/") + ".md"

    return out_md.replace("\\", "/") + anchor


def normalize_media_target(raw_target: str) -> str:
    """
    Normalize a DokuWiki media target:
    - strip leading ':'
    - decode URL encoding
    - remove query
    - convert ':' -> '/'
    - normalize separators to '/'
    """
    t = raw_target.strip()
    t = unquote(t)
    t = strip_query(t)

    anchor = ""
    if "#" in t:
        t, anchor = t.split("#", 1)
        anchor = "#" + anchor

    if t.startswith(":"):
        t = t[1:]
    t = to_posix(t)
    # Some refs use namespaces with ':'
    t = t.replace(":", "/")

    # Decode each segment too (covers mixed encodings)
    t = decode_path_segments(t)

    # Strip query again, just in case
    t = strip_query(t)

    return t + anchor


# Regex patterns
RE_CURLY_BLOCK = re.compile(r"\{[^}\n]*\}")  # remove {.*} on a single line
RE_DOKU_MEDIA = re.compile(r"\{\{([^}]+?)\}\}")  # {{...}}
RE_DOKU_LINK = re.compile(r"\[\[([^\]]+?)\]\]")  # [[...]]
RE_MULTISPACE = re.compile(r"[ \t]{2,}")


def convert_dokuwiki_to_markdown(
    text: str,
    current_note_rel_md_posix: str,
    page_index: dict[str, str],
    media_index: dict[str, str],
) -> str:
    """
    Convert key DokuWiki syntax patterns to Markdown suited for Joplin.
    """
    # Normalize newlines first
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove any {.*} blocks, including {.align-center}
    #text = RE_CURLY_BLOCK.sub("", text)

    # Current note context
    current_dir = posixpath.dirname(current_note_rel_md_posix)  # e.g. 'a/b'
    current_id = current_page_id_from_rel(current_note_rel_md_posix)

    def repl_media(m: re.Match) -> str:
        inner = m.group(1).strip()

        # Split off title/params after '|'
        left = inner.split("|", 1)[0].strip()

        # Ignore empty
        if not left:
            return ""

        # External media URLs: keep as-is (remove DokuWiki braces)
        if re.match(r"^(https?|ftp)://", left, flags=re.IGNORECASE):
            return left

        normalized_key = strip_query(unquote(left.strip()))
        normalized_key = normalized_key.strip()
        # Keep original leading ':' variants in lookups too
        candidates = []
        candidates.append(normalized_key)
        candidates.append(":" + normalized_key if not normalized_key.startswith(":") else normalized_key)
        # Also candidate after our normalizer (handles ':' -> '/')
        candidates.append(normalize_media_target(normalized_key))
        candidates.append(":" + normalize_media_target(normalized_key) if not normalize_media_target(normalized_key).startswith(":") else normalize_media_target(normalized_key))
        # Convert '/' form to ':' form as additional lookup
        for c in list(candidates):
            c0 = c
            c1 = c0.replace("/", ":")
            c2 = c0.replace(":", "/")
            candidates.extend([c1, ":" + c1 if not c1.startswith(":") else c1, c2, ":" + c2 if not c2.startswith(":") else c2])

        out_media_rel = None
        for c in candidates:
            c = strip_query(c)
            if c in media_index:
                out_media_rel = media_index[c]
                break

        if out_media_rel is None:
            # Fallback: derive output media rel path from normalized
            out_media_rel = normalize_media_target(left)
            # drop any anchor for filesystem path
            out_media_rel = out_media_rel.split("#", 1)[0]

        # Compute relative link from note to output/media/<out_media_rel>
        media_full_rel = posixpath.join("..", "media", out_media_rel)
        # current_dir is inside notes/..., so note_dir in output/notes/<current_dir>
        # link should be relative from that dir to output/media file:
        # which is ../media/... if note is at notes/<...>
        # But if note is nested: notes/a/b => need ../../media/...
        # Use a robust relpath between:
        from_dir = current_dir  # relative inside notes
        to_path = posixpath.normpath(posixpath.join("..", "media", out_media_rel))
        rel = relposix(from_dir, to_path)

        ext = Path(out_media_rel).suffix.lower()
        fname = posixpath.basename(out_media_rel)

        if ext in IMAGE_EXTS:
            return f"![]({rel})"
        else:
            # Keep attachments downloadable as a link
            return f"[{fname}]({rel})"

    def repl_link(m: re.Match) -> str:
        inner = m.group(1).strip()

        # Split "target|title"
        if "|" in inner:
            target, title = inner.split("|", 1)
            target = target.strip()
            title = title.strip()
        else:
            target = inner.strip()
            title = ""

        if not target:
            return title or ""

        # Interwiki / external links like [[http://...|...]]
        if re.match(r"^(https?|ftp)://", target, flags=re.IGNORECASE):
            label = title if title else target
            return f"[{label}]({target})"

        # DokuWiki sometimes uses media links in [[...]]; handle file extensions:
        # Decide whether this is a page link or an attachment link.
        # Heuristic: if it has a file extension, treat as media/attachment.
        t_norm = unquote(strip_query(target))
        t_norm = t_norm.strip()

        # Preserve anchor on pages
        anchor = ""
        if "#" in t_norm:
            base, anch = t_norm.split("#", 1)
            t_norm_base = base
            anchor = "#" + anch
        else:
            t_norm_base = t_norm

        ext = Path(t_norm_base).suffix.lower()

        if ext and ext != ".md":
            # Treat as media/attachment reference
            media_target = normalize_media_target(t_norm)
            # Lookup in media index (try a few forms)
            keys = [
                strip_query(t_norm),
                strip_query(":" + t_norm) if not t_norm.startswith(":") else strip_query(t_norm),
                strip_query(media_target),
                strip_query(":" + media_target) if not media_target.startswith(":") else strip_query(media_target),
                strip_query(t_norm.replace(":", "/")),
                strip_query(":" + t_norm.replace(":", "/")) if not t_norm.startswith(":") else strip_query(t_norm.replace(":", "/")),
                strip_query(t_norm.replace("/", ":")),
                strip_query(":" + t_norm.replace("/", ":")) if not t_norm.startswith(":") else strip_query(t_norm.replace("/", ":")),
            ]
            out_media_rel = None
            for k in keys:
                if k in media_index:
                    out_media_rel = media_index[k]
                    break
            if out_media_rel is None:
                out_media_rel = media_target.split("#", 1)[0]

            to_path = posixpath.normpath(posixpath.join("..", "media", out_media_rel))
            rel = relposix(current_dir, to_path)

            label = title if title else posixpath.basename(out_media_rel)
            # Images as images, others as downloadable links
            if ext in IMAGE_EXTS:
                return f"![]({rel})" if not title else f"![{title}]({rel})"
            return f"[{label}]({rel})"

        # Otherwise: page link
        target_md_rel = resolve_page_target(t_norm, current_id, page_index)
        if not target_md_rel:
            return title or ""

        # Compute rel path from current note to target note
        # Both are relative to output/notes
        target_md_rel_no_anchor = target_md_rel
        page_anchor = ""
        if "#" in target_md_rel_no_anchor:
            target_md_rel_no_anchor, page_anchor = target_md_rel_no_anchor.split("#", 1)
            page_anchor = "#" + page_anchor

        rel_link = relposix(current_dir, target_md_rel_no_anchor) + page_anchor

        label = title if title else Path(t_norm_base).name  # [[page]] -> [page](...)
        # In DokuWiki, "page" might include namespaces; label should be last segment
        if not title and ":" in t_norm_base:
            label = t_norm_base.split(":")[-1]
        return f"[{label}]({rel_link})"

    # Convert media embeds first ({{...}})
    text = RE_DOKU_MEDIA.sub(repl_media, text)

    # Convert internal links ([[...]])
    text = RE_DOKU_LINK.sub(repl_link, text)

    # Final cleanup: normalize multiple spaces from removals, avoid excessive blank spaces before newlines
    text = RE_MULTISPACE.sub(" ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    # Ensure POSIX separators everywhere (some conversions might introduce backslashes)
    text = text.replace("\\", "/")

    return text


def migrate(pages_root: Path, media_root: Path, out_root: Path) -> None:
    out_notes = out_root / "notes"
    out_media = out_root / "media"

    safe_mkdir(out_notes)
    safe_mkdir(out_media)

    # 1) Build page index
    page_index = build_page_index(pages_root)

    # 2) Copy media & build media index
    media_index = build_media_index_and_copy(media_root, out_media)

    # 3) Convert pages
    for src in pages_root.rglob("*.txt"):
        rel_src = src.relative_to(pages_root)  # e.g. a/b/page.txt
        rel_noext = rel_src.with_suffix("")    # a/b/page
        rel_md = rel_noext.with_suffix(".md")  # a/b/page.md

        rel_md_posix = to_posix(str(rel_md))
        dst = out_notes / rel_md

        # Read source
        raw = src.read_text(encoding="utf-8", errors="replace")

        # Convert
        converted = convert_dokuwiki_to_markdown(
            raw,
            current_note_rel_md_posix=rel_md_posix,
            page_index=page_index,
            media_index=media_index,
        )

        # Write
        write_text_if_changed(dst, converted, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate DokuWiki pages/media to Markdown for Joplin import (preserving hierarchy)."
    )
    parser.add_argument("--pages", default=r"C:\migrate\pages", help="Source DokuWiki pages root (contains .txt files).")
    parser.add_argument("--media", default=r"C:\migrate\media", help="Source DokuWiki media root.")
    parser.add_argument("--out", default=r"C:\migrate\output", help="Output root (will create notes/ and media/).")
    args = parser.parse_args()

    pages_root = Path(args.pages)
    media_root = Path(args.media)
    out_root = Path(args.out)

    if not pages_root.exists() or not pages_root.is_dir():
        raise SystemExit(f"Pages folder not found or not a directory: {pages_root}")
    if not media_root.exists() or not media_root.is_dir():
        raise SystemExit(f"Media folder not found or not a directory: {media_root}")

    migrate(pages_root, media_root, out_root)


if __name__ == "__main__":
    main()
