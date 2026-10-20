from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from conftest import FIXTURES, MakeAudio

from hillaudio.analysis.peaks import BASE
from hillaudio.ingest import peaks_cache, verify
from hillaudio.ingest.hashing import sha256_file
from hillaudio.ingest.importer import IngestError, import_file
from hillaudio.ingest.probe import UnsupportedAudio
from hillaudio.store.project import Project


def test_copy_import_records_everything(project: Project, make_audio: MakeAudio) -> None:
    src = make_audio("Chapter 1.wav", seconds=1.5, sample_rate=48_000, channels=2)
    res = import_file(project, src)
    s = res.source
    assert res.created and res.warnings == []
    assert (s.mode, s.format, s.codec, s.sample_rate, s.channels, s.bit_depth, s.lossless) == (
        "copy",
        "wav",
        "pcm_s24le",
        48_000,
        2,
        24,
        True,
    )
    assert s.num_frames == 72_000 and s.duration == 1.5
    assert s.label == "Chapter 1" and s.original_name == "Chapter 1.wav"
    assert s.sha256 == sha256_file(src)
    stored = project.resolve(s)
    assert stored.parent == project.sources_dir
    assert sha256_file(stored) == s.sha256
    assert not stored.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert not list(project.sources_dir.glob("*.partial"))
    assert [o["op_type"] for o in project.operations()] == ["create_project", "import_source"]


def test_reference_import_leaves_file_in_place(project: Project, make_audio: MakeAudio) -> None:
    src = make_audio("ref.wav")
    s = import_file(project, src, mode="reference").source
    assert s.mode == "reference" and project.resolve(s) == src.resolve()
    assert list(project.sources_dir.iterdir()) == []


def test_mp3_exact_duration_from_decode(project: Project, make_audio: MakeAudio) -> None:
    s = import_file(project, make_audio("c.mp3", seconds=2.0, sample_rate=44_100)).source
    assert (s.format, s.lossless, s.bit_depth) == ("mp3", False, None)
    # ffmpeg trims encoder delay/padding (LAME gapless info), so length is exact.
    assert s.num_frames == 88_200


def test_peaks_cached_by_hash(project: Project) -> None:
    s = import_file(project, FIXTURES / "ch01_take2.wav").source
    peaks = peaks_cache.load(project.cache_dir, s.sha256)
    assert peaks is not None and peaks.num_frames == s.num_frames
    assert len(peaks.levels[0].max) == -(-s.num_frames // BASE)
    assert peaks.levels[0].max.max() > 10_000  # speech peaks near -6 dBFS


def test_duplicate_import_is_not_reimported(project: Project) -> None:
    a = import_file(project, FIXTURES / "ch01_take2.wav")
    b = import_file(project, FIXTURES / "ch01_take2.wav", mode="reference")
    assert not b.created and b.source.id == a.source.id
    assert "identical" in b.warnings[0]
    assert len(project.sources()) == 1


def test_order_and_mixed_sample_rate_warning(project: Project, make_audio: MakeAudio) -> None:
    import_file(project, FIXTURES / "ch01_take2.wav")
    res = import_file(project, make_audio("hi.wav", sample_rate=44_100))
    assert any("mixed sample rates (16000 Hz, 44100 Hz)" in w for w in res.warnings)
    assert [s.order_index for s in project.sources()] == [0, 1]


def test_rejects_unsupported_and_missing(
    project: Project, make_audio: MakeAudio, tmp_path: Path
) -> None:
    flac = make_audio("x.wav")
    renamed = flac.with_suffix(".flac")
    flac.rename(renamed)
    with pytest.raises(UnsupportedAudio):
        import_file(project, renamed)
    with pytest.raises(IngestError, match="not found"):
        import_file(project, tmp_path / "missing.wav")
    assert project.sources() == []


def test_rejects_importing_from_own_sources(project: Project) -> None:
    s = import_file(project, FIXTURES / "ch01_take2.wav").source
    with pytest.raises(IngestError, match="already inside"):
        import_file(project, project.resolve(s))


def test_cancel_rolls_back_cleanly(project: Project, make_audio: MakeAudio) -> None:
    src = make_audio("long.wav", seconds=5.0)
    before = sha256_file(src)
    calls = 0

    class Stop(Exception):
        pass

    def cancel_during_peaks() -> None:
        nonlocal calls
        calls += 1
        if calls > 5:  # after hashing/copying, inside peak decoding
            raise Stop

    with pytest.raises(Stop):
        import_file(project, src, check_cancel=cancel_during_peaks)
    assert project.sources() == []
    assert list(project.sources_dir.iterdir()) == []  # our partial copy removed
    assert sha256_file(src) == before  # the user's file is untouched


# ---- verify and relink --------------------------------------------------------------


def test_verify_detects_missing_and_modified(
    project: Project, fixture_copy: Callable[[str], Path]
) -> None:
    a = import_file(project, fixture_copy("ch01_take2.wav"), mode="reference").source
    b = import_file(project, fixture_copy("ch02_take1.wav"), mode="reference").source
    c = import_file(project, FIXTURES / "ch01_take1.wav").source  # copy mode
    assert [x.status for x in verify.verify_all(project)] == ["ok", "ok", "ok"]

    Path(a.path).unlink()  # user deleted their file
    with Path(b.path).open("r+b") as f:  # user edited their file in another app
        f.seek(1000)
        f.write(b"\x00\x01\x02")
    status = {x.source_id: x.status for x in verify.verify_all(project)}
    assert status == {a.id: "missing", b.id: "modified", c.id: "ok"}


def test_relink_reference_requires_same_content(
    project: Project, fixture_copy: Callable[[str], Path], tmp_path: Path
) -> None:
    orig = fixture_copy("ch01_take2.wav")
    s = import_file(project, orig, mode="reference").source
    moved = tmp_path / "moved.wav"
    orig.rename(moved)
    assert verify.check(project, project.source(s.id)).status == "missing"

    with pytest.raises(IngestError, match="not the same audio"):
        verify.relink(project, s.id, FIXTURES / "ch02_take1.wav")
    relinked = verify.relink(project, s.id, moved)
    assert relinked.path == str(moved) and verify.check(project, relinked).status == "ok"
    assert project.operations()[-1]["op_type"] == "relink_source"


def test_relink_copy_sets_damaged_copy_aside(project: Project) -> None:
    s = import_file(project, FIXTURES / "ch01_take2.wav").source
    stored = project.resolve(s)
    os.chmod(stored, 0o644)
    stored.write_bytes(b"corrupted")
    assert verify.check(project, s).status == "modified"

    verify.relink(project, s.id, FIXTURES / "ch01_take2.wav")
    assert verify.check(project, project.source(s.id)).status == "ok"
    aside = list(project.sources_dir.glob("*.damaged-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"corrupted"  # kept, not deleted
    assert np.isclose(project.source(s.id).duration, s.duration)
