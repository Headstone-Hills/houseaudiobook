"""Finalize and export (M8): the master-bus chain, presets, and writing files.

SPEC §6.8-6.9. The chain is project settings applied at render time; sources
are never touched and nothing is baked in (D-050). Each output file (the whole
book, or one per chapter/marker section) goes through three passes over the
timeline render, block by block so long files never sit in memory:

  0. render the section from the sources (with clip-level gain riding);
  1. room tone head/tail + noise reduction + gate + compressor -> a temp file,
     measuring its RMS on the way;
  2. normalization to the target RMS + look-ahead limiter -> the encoder.

Then the written file is decoded again and measured: the report describes the
file that was actually written (D-052).
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import soundfile as sf

from hillaudio import pipeline
from hillaudio.analysis.boundaries import speech_mask
from hillaudio.edl import timeline_store
from hillaudio.edl.model import NARRATION, Clip
from hillaudio.edl.render import Input, render
from hillaudio.ingest.decode import iter_blocks
from hillaudio.progress import CheckCancel, Progress, never_cancel, no_progress
from hillaudio.render import dsp
from hillaudio.store.project import Project

SETTINGS_KEY = "finalize_settings"
PRESETS_KEY = "finalize_presets"
BLOCK_S = 10.0
RIDE_MAX_DB = 6.0


class ExportError(ValueError):
    """A user-facing reason an export can't be made."""


# -- presets ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    rms_min_db: float
    rms_max_db: float
    peak_max_db: float
    noise_floor_max_db: float
    mp3_min_kbps: int = 192
    mp3_sample_rate: int = 44100
    head_s: float = 1.0  # room tone at the start of each file
    tail_s: float = 3.0  # ...and at the end
    head_range: tuple[float, float] = (1.0, 5.0)
    tail_range: tuple[float, float] = (1.0, 5.0)
    max_minutes: float | None = 120.0
    note: str = ""

    def validate(self) -> Preset:
        if not self.rms_min_db < self.rms_max_db <= 0 or self.peak_max_db > 0:
            raise ValueError("a preset needs rms_min < rms_max <= 0 dB and peak <= 0 dB")
        return self


# Numbers from ACX's "Audio Submission Requirements" page as summarized in search
# results on 2026-10-21; the page itself couldn't be fetched from this
# environment. Verify on acx.com before relying on them (SPEC §6.8 note).
ACX = Preset(
    "acx",
    "ACX / Audible",
    rms_min_db=-23.0,
    rms_max_db=-18.0,
    peak_max_db=-3.0,
    noise_floor_max_db=-60.0,
    mp3_min_kbps=192,
    mp3_sample_rate=44100,
    head_s=1.0,
    tail_s=3.0,
    max_minutes=120.0,
    note="ACX requirements as of 2026-10 (RMS -23 to -18 dB, peak -3 dB, noise floor "
    "-60 dB RMS, 192 kbps+ CBR MP3 at 44.1 kHz, 1-5 s room tone each end, same "
    "channels in every file). Verify on acx.com: they can change.",
)
BUILTIN = {ACX.key: ACX}


def presets(project: Project) -> dict[str, Preset]:
    out = dict(BUILTIN)
    try:
        raw = json.loads(project.meta(PRESETS_KEY))
    except KeyError:
        raw = {}
    for k, v in raw.items():
        v = dict(v)
        for rk in ("head_range", "tail_range"):
            if rk in v:
                v[rk] = tuple(v[rk])
        out[k] = Preset(**v)
    return out


def save_preset(project: Project, p: Preset) -> Preset:
    if p.key in BUILTIN:
        raise ValueError("built-in presets can't be changed; save it under a new name")
    p.validate()
    try:
        raw = json.loads(project.meta(PRESETS_KEY))
    except KeyError:
        raw = {}
    raw[p.key] = asdict(p)
    _put_meta(project, PRESETS_KEY, raw, "finalize_preset")
    return p


def _put_meta(project: Project, key: str, value: Any, op: str) -> None:
    with project.db() as conn:
        conn.execute(
            "INSERT INTO project_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        project.log(conn, op, {"key": key, "value": value}, "user")


# -- settings -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizeSettings:
    preset: str = "acx"
    noise_reduction: bool = True
    nr_strength: float = 0.5  # 0..1
    nr_reduction_db: float = 12.0
    # The room tone to learn the noise from, and to pad files with: a stretch of a
    # recording (seconds). None: the quietest second found automatically.
    noise_region: dict[str, float] | None = None
    gate: bool = False
    gate_threshold_db: float = -55.0
    gate_range_db: float = 10.0
    auto_level: bool = True  # clip-level riding + gentle compression
    compressor_threshold_db: float = -24.0
    compressor_ratio: float = 2.0
    normalize: bool = True
    target_rms_db: float = -20.0
    limiter: bool = True
    ceiling_db: float = -3.5  # half a dB under the preset's peak limit

    def validate(self) -> FinalizeSettings:
        if not 0 <= self.nr_strength <= 1:
            raise ValueError("noise reduction strength must be 0-1")
        if not 0 < self.nr_reduction_db <= 40 or not 0 < self.gate_range_db <= 40:
            raise ValueError("reductions must be between 0 and 40 dB")
        if not -40 <= self.target_rms_db <= -6 or not -20 <= self.ceiling_db <= 0:
            raise ValueError("target RMS must be -40..-6 dB and the ceiling -20..0 dB")
        if self.compressor_ratio < 1:
            raise ValueError("compressor ratio must be at least 1")
        r = self.noise_region
        if r is not None and not (r.get("end", 0) > r.get("start", 0) >= 0 and "source_id" in r):
            raise ValueError("the room tone region needs a source and start < end")
        return self

    def key(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def settings(project: Project) -> FinalizeSettings:
    try:
        raw = json.loads(project.meta(SETTINGS_KEY))
    except KeyError:
        return FinalizeSettings()
    known = {f.name for f in fields(FinalizeSettings)}
    return FinalizeSettings(**{k: v for k, v in raw.items() if k in known})


def save_settings(project: Project, s: FinalizeSettings) -> FinalizeSettings:
    s.validate()
    if s.preset not in presets(project):
        raise ValueError(f"unknown preset {s.preset!r}")
    _put_meta(project, SETTINGS_KEY, asdict(s), "finalize_settings")
    return s


# -- what to write --------------------------------------------------------------------------


Split = Literal["single", "chapters", "markers"]


@dataclass(frozen=True)
class OutputPlan:
    index: int
    title: str
    t0: int  # timeline samples
    t1: int

    def seconds(self, rate: int) -> float:
        return (self.t1 - self.t0) / rate


def _safe(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_" else " " for ch in name)
    return "_".join(keep.split())[:60] or "part"


def plan(project: Project, split: Split = "single") -> list[OutputPlan]:
    """Output files: the whole timeline, or one per chapter (where each chapter's
    first line plays), or one per marker section."""
    st = timeline_store.load(project)
    end = max((c.timeline_end for c in st.clips), default=0)
    if end == 0:
        raise ExportError("the timeline is empty")
    starts: list[tuple[int, str]] = []
    if split == "chapters":
        starts = _chapter_starts(project, st)
        if not starts:
            raise ExportError(
                "no chapters found on the timeline (import a manuscript and run Compare to Text)"
            )
    elif split == "markers":
        loc = timeline_store.Locator(st)
        for m in st.markers:
            t = (
                m["sample"] / st.rate
                if m["source_file_id"] is None
                else loc.at_sample(m["source_file_id"], int(m["sample"]))
            )
            if t is not None:
                starts.append((round(t * st.rate), m["label"]))
        starts.sort()
        if not starts:
            raise ExportError("add markers where each file should start")
    if not starts:
        return [OutputPlan(1, project.name, 0, end)]
    if starts[0][0] > 0:
        starts[0] = (0, starts[0][1])  # anything before the first chapter joins it
    out: list[OutputPlan] = []
    for i, (t0, title) in enumerate(starts):
        t1 = starts[i + 1][0] if i + 1 < len(starts) else end
        if t1 > t0:
            out.append(OutputPlan(len(out) + 1, title, t0, t1))
    return out


def _chapter_starts(project: Project, st: timeline_store.State) -> list[tuple[int, str]]:
    run = pipeline.latest_run(project)
    if run is None:
        return []
    with project.db() as conn:
        chapters = conn.execute(
            "SELECT c.id, c.text, MIN(s.ordinal) AS first FROM manuscript_unit c"
            " JOIN manuscript_unit p ON p.parent_id = c.id"
            " JOIN manuscript_unit s ON s.parent_id = p.id"
            " WHERE c.kind = 'chapter' GROUP BY c.id ORDER BY c.ordinal"
        ).fetchall()
    from hillaudio import issue_store

    readings = issue_store.reading_infos(project, run)
    loc = timeline_store.Locator(st)
    out = []
    for ch in chapters:
        times = [
            t
            for r in readings
            if r.sentence_ordinal == ch["first"] and r.coverage > 0.6
            if (t := loc.at(r.source_id, r.start)) is not None
        ]
        if times:
            out.append((round(min(times) * st.rate), ch["text"]))
    out.sort()
    return out


# -- the chain -------------------------------------------------------------------------------


def _inputs(project: Project) -> dict[int, Input]:
    out = {}
    for s in project.sources():
        path = project.resolve(s)
        if not path.is_file():
            raise ExportError(f"{s.label} is missing; relink it first")
        out[s.id] = Input(path, s.sample_rate, s.channels, s.num_frames)
    return out


def riding(project: Project, st: timeline_store.State) -> list[Clip]:
    """Clip-level gain riding (auto-level, SPEC §6.8.3): each narration clip's
    speech level moves toward the narration's median, by at most 6 dB, so takes
    and chapters recorded at different levels sit together. Measured from the
    cached 10 ms envelopes, not by rendering."""
    levels: list[tuple[Clip, float, float]] = []
    envs: dict[int, Any] = {}
    for c in st.clips:
        if c.track_id != NARRATION:
            continue
        src = next(s for s in project.sources() if s.id == c.source_id)
        env = envs.get(src.id)
        if env is None:
            env = envs[src.id] = pipeline.envelope(project, src)
        a = int(c.source_in / src.sample_rate * env.fps)
        b = int(c.source_out / src.sample_rate * env.fps)
        db = env.db[a:b]
        mask = speech_mask(env)[a:b]
        speech = db[mask]
        if len(speech) >= 10:
            power = float(np.mean(10.0 ** (speech.astype(np.float64) / 10.0)))
            levels.append((c, 10.0 * math.log10(power), len(speech) / env.fps))
    if not levels:
        return st.clips
    # Duration-weighted median of the clip levels.
    order = sorted(levels, key=lambda x: x[1])
    half = sum(w for _, _, w in order) / 2
    acc = 0.0
    median = order[-1][1]
    for _, lv, w in order:
        acc += w
        if acc >= half:
            median = lv
            break
    adjust = {id(c): max(-RIDE_MAX_DB, min(RIDE_MAX_DB, median - lv)) for c, lv, _ in levels}
    return [replace(c, gain_db=c.gain_db + adjust.get(id(c), 0.0)) for c in st.clips]


@dataclass
class Job:
    """Everything one export or analysis run needs."""

    project: Project
    settings: FinalizeSettings
    preset: Preset
    st: timeline_store.State
    clips: list[Clip]
    inputs: dict[int, Input]
    channels: int
    rate: int
    room: np.ndarray  # room tone (frames, channels) at the timeline rate
    profile: dsp.NoiseProfile | None
    progress: Progress = no_progress
    check_cancel: CheckCancel = never_cancel
    tmp: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="hillaudio-")))

    def render(self, t0: int, t1: int) -> np.ndarray:
        return render(
            self.clips, self.inputs, self.rate, self.channels, t0, t1, tracks=self.st.tracks
        )


def prepare(
    project: Project, progress: Progress = no_progress, check_cancel: CheckCancel = never_cancel
) -> Job:
    s = settings(project)
    pr = presets(project).get(s.preset, ACX)
    st = timeline_store.load(project)
    inputs = _inputs(project)
    channels = timeline_store.mix_channels(st)
    clips = riding(project, st) if s.auto_level else st.clips
    job = Job(
        project,
        s,
        pr,
        st,
        clips,
        inputs,
        channels,
        st.rate,
        np.zeros((0, channels), np.float32),
        None,
        progress,
        check_cancel,
    )
    job.room = _room_tone(job)
    if s.noise_reduction and len(job.room):
        job.profile = dsp.NoiseProfile.from_audio(job.room, st.rate)
    return job


def _room_tone(job: Job) -> np.ndarray:
    """The room tone to pad files with and learn the noise from."""
    r = job.settings.noise_region
    if r is not None:
        src = job.inputs.get(int(r["source_id"]))
        if src is None:
            raise ExportError("the room tone region's recording is gone")
        from hillaudio.edl.render import read

        a = round(r["start"] * src.sample_rate)
        n = round((r["end"] - r["start"]) * job.rate)
        return read(src, a, n, job.rate, job.channels)
    # The quietest second of the first few minutes of the timeline.
    end = min(max((c.timeline_end for c in job.clips), default=0), round(300 * job.rate))
    if end <= 0:
        return np.zeros((0, job.channels), np.float32)
    head = job.render(0, end)
    a, b = dsp.quietest(head, job.rate, 1.0)
    return head[a:b]


def _loop(room: np.ndarray, n: int, rate: int) -> np.ndarray:
    """Room tone of length n: the clip looped with 50 ms crossfades."""
    if n <= 0 or len(room) == 0:
        return np.zeros((max(0, n), room.shape[1] if room.ndim == 2 else 1), np.float32)
    xf = min(len(room) // 4, round(0.05 * rate))
    out = room.copy()
    while len(out) < n:
        if xf:
            ramp = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
            mixed = out[-xf:] * (1 - ramp) + room[:xf] * ramp
            out = np.concatenate([out[:-xf], mixed, room[xf:]])
        else:
            out = np.concatenate([out, room])
    return out[:n]


def _speech_bounds(job: Job, plan_: OutputPlan) -> tuple[int, int]:
    """First and last speech in a section (so its own leading/trailing silence
    can be replaced by the preset's room tone)."""
    hop = round(dsp.FRAME_S * job.rate)
    thr = None
    first = last = None
    pos = plan_.t0
    step = _block(job.rate)
    while pos < plan_.t1:
        job.check_cancel()
        b = min(plan_.t1, pos + step)
        lv = dsp.frame_db(job.render(pos, b), hop)
        if thr is None:
            noise = dsp.measure(job.room, job.rate).rms_db if len(job.room) else -70.0
            thr = max(noise + 15.0, -55.0)
        loud = np.flatnonzero(lv > thr)
        if len(loud):
            if first is None:
                first = pos + int(loud[0]) * hop
            last = pos + int(loud[-1] + 1) * hop
        pos = b
    if first is None or last is None:
        return plan_.t0, plan_.t1
    # Keep a breath of the original around the speech.
    return max(plan_.t0, first - round(0.25 * job.rate)), min(
        plan_.t1, last + round(0.5 * job.rate)
    )


def _block(rate: int) -> int:
    q = dsp.block_quantum(rate)
    return max(q, round(BLOCK_S * rate / q) * q)


def _section(
    job: Job, plan_: OutputPlan, pad: bool = True
) -> tuple[Callable[[int, int], np.ndarray], int]:
    """The section as the encoder will see it, before processing: room tone
    head, the speech, room tone tail (pad=False: just the stretch as it is).
    Returns a reader of [a, b) and its length."""
    a, b = _speech_bounds(job, plan_) if pad else (plan_.t0, plan_.t1)
    head_n = round(job.preset.head_s * job.rate) if pad else 0
    tail_n = round(job.preset.tail_s * job.rate) if pad else 0
    head = _loop(job.room, head_n, job.rate)
    tail = _loop(job.room[::-1] if len(job.room) else job.room, tail_n, job.rate)
    n_head, n_body = len(head), b - a
    total = n_head + n_body + len(tail)

    def get(x: int, y: int) -> np.ndarray:
        x, y = max(0, x), min(total, y)
        parts = []
        if x < n_head:
            parts.append(head[x : min(y, n_head)])
        if y > n_head and x < n_head + n_body:
            parts.append(job.render(a + max(0, x - n_head), a + min(n_body, y - n_head)))
        if y > n_head + n_body:
            parts.append(tail[max(0, x - n_head - n_body) : y - n_head - n_body])
        out = np.concatenate(parts) if parts else np.zeros((0, job.channels), np.float32)
        return out.astype(np.float32)

    return get, total


def process(
    job: Job,
    plan_: OutputPlan,
    sink: Callable[[np.ndarray], None],
    progress: Callable[[float], None] = lambda f: None,
    pad: bool = True,
    gain_db: float | None = None,
) -> dict[str, Any]:
    """Run the chain over one output section, feeding the encoder `sink`.
    `gain_db`: a normalization gain to use instead of measuring (previews)."""
    s = job.settings
    get, total = _section(job, plan_, pad)
    step = _block(job.rate)
    nr = (
        dsp.NoiseReducer(job.profile, s.nr_strength, s.nr_reduction_db)
        if s.noise_reduction and job.profile
        else None
    )
    gate = dsp.Gate(s.gate_threshold_db, range_db=s.gate_range_db) if s.gate else None
    comp = dsp.Compressor(s.compressor_threshold_db, s.compressor_ratio) if s.auto_level else None
    tmp = job.tmp / f"pass1-{plan_.index}.f32"
    power = 0.0
    with tmp.open("wb") as f:
        for pos in range(0, total, step):
            job.check_cancel()
            end = min(total, pos + step)
            if nr is not None:
                ctx = nr.context
                block = nr.process(get(pos - ctx, end + ctx))
                lo = min(ctx, pos)
                block = block[lo : lo + (end - pos)]
            else:
                block = get(pos, end)
            if gate is not None:
                block = gate.process(block, job.rate)
            if comp is not None:
                block = comp.process(block, job.rate)
            power += float(np.sum(np.square(block, dtype=np.float64)))
            f.write(np.ascontiguousarray(block, np.float32).tobytes())
            progress(0.5 * end / max(1, total))
    mm = np.memmap(tmp, dtype=np.float32, mode="r").reshape(-1, job.channels)
    rms_db = 10 * math.log10(max(power / max(1, len(mm) * job.channels), 1e-24))
    if not s.normalize:
        gain = 1.0
    elif gain_db is not None:
        gain = float(dsp.undb(gain_db))
    else:
        gain = float(dsp.undb(s.target_rms_db - rms_db))
    state = 1.0
    ahead_n = round(0.01 * job.rate)
    for pos in range(0, len(mm), step):
        job.check_cancel()
        end = min(len(mm), pos + step)
        block = np.asarray(mm[pos:end]) * np.float32(gain)
        if s.limiter:
            ahead = np.asarray(mm[end : end + ahead_n]) * np.float32(gain)
            block, state = dsp.limit(block, job.rate, s.ceiling_db, ahead, state)
        sink(block.astype(np.float32))
        progress(0.5 + 0.5 * end / max(1, len(mm)))
    del mm
    tmp.unlink(missing_ok=True)
    return {"normalize_db": round(20 * math.log10(gain), 2), "pre_rms_db": round(rms_db, 2)}


# -- encoders ---------------------------------------------------------------------------------

Format = Literal["wav16", "wav24", "mp3"]


class Encoder:
    def __init__(
        self, path: Path, fmt: Format, rate: int, channels: int, kbps: int, mp3_rate: int
    ) -> None:
        self.path, self.fmt = path, fmt
        self.rng = np.random.default_rng(0)  # deterministic dither: repeatable exports
        if fmt == "mp3":
            self.proc: subprocess.Popen[bytes] | None = subprocess.Popen(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-f",
                    "f32le",
                    "-ar",
                    str(rate),
                    "-ac",
                    str(channels),
                    "-i",
                    "-",
                    "-ar",
                    str(mp3_rate),
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    f"{kbps}k",
                    str(path),
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.sf: sf.SoundFile | None = None
        else:
            self.proc = None
            self.sf = sf.SoundFile(
                str(path), "w", rate, channels, "PCM_16" if fmt == "wav16" else "PCM_24"
            )

    def write(self, block: np.ndarray) -> None:
        if self.proc is not None:
            assert self.proc.stdin is not None
            self.proc.stdin.write(np.ascontiguousarray(block, np.float32).tobytes())
        elif self.fmt == "wav16":
            assert self.sf is not None
            self.sf.write(dsp.dither_16(block, self.rng))
        else:
            assert self.sf is not None
            self.sf.write(np.clip(block, -1.0, 1.0))

    def close(self) -> None:
        if self.proc is not None:
            assert self.proc.stdin is not None and self.proc.stderr is not None
            self.proc.stdin.close()
            err = self.proc.stderr.read().decode(errors="replace")
            if self.proc.wait() != 0:
                raise ExportError(f"MP3 encoding failed: {err.strip()[:200]}")
        elif self.sf is not None:
            self.sf.close()


def probe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,bit_rate",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    d = json.loads(proc.stdout)
    stream = d["streams"][0]
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "bit_rate": int(stream.get("bit_rate") or d["format"].get("bit_rate") or 0),
        "duration": float(d["format"].get("duration", 0.0)),
    }


def check(levels: dsp.Levels, info: dict[str, Any], pr: Preset, fmt: str) -> list[dict[str, Any]]:
    """Pass/fail against the preset, one row per requirement (SPEC §6.8.5)."""
    rows = [
        {
            "name": "RMS",
            "value": round(levels.rms_db, 2),
            "limit": f"{pr.rms_min_db} to {pr.rms_max_db} dB",
            "pass": pr.rms_min_db <= levels.rms_db <= pr.rms_max_db,
        },
        {
            "name": "Peak",
            "value": round(levels.peak_db, 2),
            "limit": f"<= {pr.peak_max_db} dB",
            "pass": levels.peak_db <= pr.peak_max_db,
        },
        {
            "name": "Noise floor",
            "value": round(levels.noise_floor_db, 2),
            "limit": f"<= {pr.noise_floor_max_db} dB RMS",
            "pass": levels.noise_floor_db <= pr.noise_floor_max_db,
        },
        {
            "name": "No digital silence",
            "value": not levels.digital_silence,
            "limit": "room tone, not zeros",
            "pass": not levels.digital_silence,
        },
    ]
    if pr.max_minutes is not None:
        rows.append(
            {
                "name": "Length",
                "value": round(info["duration"] / 60, 2),
                "limit": f"<= {pr.max_minutes} min",
                "pass": info["duration"] <= pr.max_minutes * 60,
            }
        )
    if fmt == "mp3":
        kbps = round(info["bit_rate"] / 1000)
        rows.append(
            {
                "name": "MP3 bitrate",
                "value": kbps,
                "limit": f">= {pr.mp3_min_kbps} kbps CBR",
                "pass": kbps >= pr.mp3_min_kbps,
            }
        )
        rows.append(
            {
                "name": "Sample rate",
                "value": info["sample_rate"],
                "limit": f"{pr.mp3_sample_rate} Hz",
                "pass": info["sample_rate"] == pr.mp3_sample_rate,
            }
        )
    return rows


# -- export ------------------------------------------------------------------------------------


def preflight(project: Project, fmt: Format = "mp3", split: Split = "single") -> dict[str, Any]:
    """What the export dialog shows first (SPEC §6.9): unresolved issues, sources
    that would make it impossible or unwise, the files it would write."""
    from hillaudio import issue_store

    open_issues: dict[str, int] = {}
    for i in issue_store.list_issues(project):
        if i["status"] == "open":
            open_issues[i["kind"]] = open_issues.get(i["kind"], 0) + 1
    st = timeline_store.load(project)
    used = {c.source_id for c in st.clips}
    srcs = [s for s in project.sources() if s.id in used]
    lossy = [s.label for s in srcs if not s.lossless]
    missing = [s.label for s in srcs if not project.resolve(s).is_file()]
    problems = []
    if fmt == "mp3" and lossy:
        problems.append(
            f"MP3 export would re-encode lossy recordings ({', '.join(lossy)}): lossy to lossy "
            "is never done. Export WAV, or replace them with lossless originals."
        )
    if missing:
        problems.append(f"missing recordings: {', '.join(missing)}; relink them first")
    outputs: list[dict[str, Any]] = []
    try:
        outputs = [{"title": p.title, "seconds": p.seconds(st.rate)} for p in plan(project, split)]
    except ExportError as e:
        outputs = []
        problems.append(str(e))
    pr = presets(project).get(settings(project).preset, ACX)
    warnings = []
    if pr.max_minutes is not None:
        long = [o["title"] for o in outputs if o["seconds"] > pr.max_minutes * 60]
        if long:
            warnings.append(
                f"longer than {pr.max_minutes:.0f} min: {', '.join(long)};"
                " split by chapters or markers"
            )
    return {
        "open_issues": open_issues,
        "lossy_sources": lossy,
        "missing_sources": missing,
        "outputs": outputs,
        "problems": problems,
        "warnings": warnings,
        "preset": asdict(pr),
        "last_analysis": _last_analysis(project),
    }


def _last_analysis(project: Project) -> Any:
    try:
        return json.loads(project.meta("finalize_last_analysis"))
    except KeyError:
        return None


def export(
    project: Project,
    fmt: Format = "mp3",
    split: Split = "single",
    kbps: int | None = None,
    progress: Progress = no_progress,
    check_cancel: CheckCancel = never_cancel,
    analyze_only: bool = False,
) -> dict[str, Any]:
    """Write the finished files (or, analyze_only, just measure what they'd be)."""
    pf = preflight(project, fmt, split)
    blocking = [p for p in pf["problems"] if not (analyze_only and "MP3" in p)]
    if blocking:
        raise ExportError(blocking[0])
    job = prepare(project, progress, check_cancel)
    plans = plan(project, split)
    kbps = kbps or job.preset.mp3_min_kbps
    if fmt == "mp3" and kbps < 32:
        raise ExportError("choose an MP3 bitrate")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = project.root / "exports" / stamp
    if not analyze_only:
        out_dir.mkdir(parents=True, exist_ok=False)
    ext = "mp3" if fmt == "mp3" else "wav"
    files = []
    try:
        for k, p in enumerate(plans):

            def prog(f: float, k: int = k, p: OutputPlan = p) -> None:
                progress(
                    (k + f) / len(plans),
                    f"{p.title}: {'measuring' if analyze_only else 'rendering'}",
                )

            if analyze_only:
                chunks: list[np.ndarray] = []
                stats = process(job, p, chunks.append, prog)
                audio = (
                    np.concatenate(chunks) if chunks else np.zeros((0, job.channels), np.float32)
                )
                info: dict[str, Any] = {
                    "duration": len(audio) / job.rate,
                    "sample_rate": job.rate,
                    "bit_rate": 0,
                    "channels": job.channels,
                }
                levels = dsp.measure(audio, job.rate)
                path = None
            else:
                name = (
                    f"{p.index:02d}_{_safe(p.title)}.{ext}"
                    if len(plans) > 1
                    else f"{_safe(project.name)}.{ext}"
                )
                path = out_dir / name
                enc = Encoder(path, fmt, job.rate, job.channels, kbps, job.preset.mp3_sample_rate)
                try:
                    stats = process(job, p, enc.write, prog)
                finally:
                    enc.close()
                info = probe(path)
                # Measure the file as written: decode it back (D-052).
                decoded = np.concatenate(list(iter_blocks(path, info["channels"])))
                levels = dsp.measure(decoded, info["sample_rate"])
            files.append(
                {
                    "title": p.title,
                    "start": p.t0 / job.rate,
                    "end": p.t1 / job.rate,
                    "file": None if path is None else str(path.relative_to(project.root)),
                    "seconds": round(info["duration"], 3),
                    "levels": asdict(levels),
                    "checks": check(levels, info, job.preset, "wav" if analyze_only else fmt),
                    "pass": all(
                        r["pass"]
                        for r in check(levels, info, job.preset, "wav" if analyze_only else fmt)
                    ),
                    **stats,
                    **({} if path is None else {"format": info}),
                }
            )
    except BaseException:
        if not analyze_only:
            shutil.rmtree(out_dir, ignore_errors=True)  # only our own half-written folder
        raise
    finally:
        shutil.rmtree(job.tmp, ignore_errors=True)
    report = {
        "created": stamp,
        "format": fmt,
        "split": split,
        "kbps": kbps if fmt == "mp3" else None,
        "preset": asdict(job.preset),
        "settings": asdict(job.settings),
        "settings_key": job.settings.key(),
        "channels": job.channels,
        "files": files,
        "pass": all(f["pass"] for f in files),
    }
    if analyze_only:
        _put_meta(project, "finalize_last_analysis", report, "finalize_analyze")
    else:
        (out_dir / "report.json").write_text(json.dumps(report, indent=2))
        with project.db() as conn:
            project.log(
                conn,
                "export",
                {
                    "dir": str(out_dir.relative_to(project.root)),
                    "files": [f["file"] for f in files],
                    "pass": report["pass"],
                },
                "user",
            )
    return report


def exports(project: Project) -> list[dict[str, Any]]:
    base = project.root / "exports"
    out = []
    for d in sorted(base.glob("*/report.json"), reverse=True):
        try:
            out.append(json.loads(d.read_text()) | {"dir": str(d.parent.relative_to(project.root))})
        except (OSError, ValueError):
            continue
    return out


def preview(project: Project, start: float, end: float) -> tuple[np.ndarray, int]:
    """The chain on a stretch of the timeline, for A/B listening. Normalization
    uses the gain the last analysis found for the file this stretch is in (with
    the same settings), else the stretch's own level."""
    job = prepare(project)
    try:
        t0, t1 = round(start * job.rate), round(end * job.rate)
        gain = None
        last = _last_analysis(project)
        if last and last.get("settings_key") == job.settings.key():
            f = next(
                (f for f in last["files"] if f.get("start", 0) <= start < f.get("end", 0)), None
            )
            if f is not None:
                gain = float(f["normalize_db"])
        chunks: list[np.ndarray] = []
        process(job, OutputPlan(0, "preview", t0, t1), chunks.append, pad=False, gain_db=gain)
        audio = np.concatenate(chunks) if chunks else np.zeros((0, job.channels), np.float32)
        return audio, job.rate
    finally:
        shutil.rmtree(job.tmp, ignore_errors=True)
