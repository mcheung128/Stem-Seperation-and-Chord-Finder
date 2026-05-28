from __future__ import annotations

import bisect
import importlib.metadata
import tempfile
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import soundfile as sf
from lv_chordia.chord_recognition import chord_recognition

_original_distribution = importlib.metadata.distribution


def _patched_distribution(name: str):
    if name == "madmom":
        try:
            return _original_distribution(name)
        except importlib.metadata.PackageNotFoundError:
            return _original_distribution("madmom-prebuilt")
    return _original_distribution(name)


importlib.metadata.distribution = _patched_distribution

from madmom.features.beats import RNNBeatProcessor
from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
from madmom.features.tempo import TempoEstimationProcessor
from madmom.io.audio import LoadAudioFileError


PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float64)
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float64)


class PianoChordAnalyzer:
    def analyze_file(self, audio_path: Path) -> dict[str, Any]:
        chord_segments = chord_recognition(audio_path=str(audio_path), chord_dict_name="submission")
        if not chord_segments:
            return {"error": "No chords detected in the uploaded file."}

        rhythm = self._analyze_rhythm(audio_path, meter_candidates=[3, 4, 6], downbeat_tolerance=0.2)
        beats_per_bar = int(rhythm.get("estimated_meter") or 4)
        beat_sheet = self._build_beat_sheet(
            chord_segments,
            rhythm["beats"],
            beats_per_bar=beats_per_bar,
            snap_window=0.18,
        )
        if not beat_sheet:
            return {"error": "No usable chord segments were produced."}

        key_label, key_confidence = self._estimate_key(audio_path)
        progression = self._build_progression_from_beats(beat_sheet)
        aligned_segments = self._merge_beat_sheet_to_segments(beat_sheet)
        measure_sheet = self._build_measure_sheet(beat_sheet, beats_per_bar)

        return {
            "key": {"label": key_label, "confidence": round(key_confidence, 3)},
            "tempo_bpm": round(float(rhythm["tempo_bpm"]), 2) if rhythm["tempo_bpm"] is not None else None,
            "progression_text": " -> ".join(progression) if progression else "Unknown",
            "beats_per_bar": beats_per_bar,
            "measure_sheet": measure_sheet,
            "beat_sheet": beat_sheet,
            "segments": aligned_segments,
            "analysis_context": {
                "tempo_source": "estimated",
                "key_source": "estimated",
                "duration_seconds": round(float(aligned_segments[-1]["end_time"]), 3),
                "meter_candidates": [3, 4, 6],
                "detected_meter": beats_per_bar,
            },
        }

    def _run_madmom_processors(self, audio_input: str, meter_candidates: list[int]) -> dict[str, Any]:
        beat_activations = RNNBeatProcessor()(audio_input)
        tempo_candidates = TempoEstimationProcessor(fps=100)(beat_activations)
        downbeat_activations = RNNDownBeatProcessor()(audio_input)
        beat_positions = DBNDownBeatTrackingProcessor(
            beats_per_bar=meter_candidates,
            fps=100,
        )(downbeat_activations)
        return {
            "tempo_candidates": tempo_candidates,
            "beat_positions": beat_positions,
        }

    def _analyze_rhythm(self, audio_path: Path, meter_candidates: list[int], downbeat_tolerance: float) -> dict[str, Any]:
        try:
            madmom_output = self._run_madmom_processors(str(audio_path), meter_candidates)
        except LoadAudioFileError:
            audio, sample_rate = librosa.load(str(audio_path), sr=44100, mono=True)
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_wav_path = Path(temp_dir) / "madmom_fallback.wav"
                sf.write(str(temp_wav_path), audio, sample_rate)
                madmom_output = self._run_madmom_processors(str(temp_wav_path), meter_candidates)

        tempo_candidates = madmom_output["tempo_candidates"]
        beat_positions = madmom_output["beat_positions"]

        beats: list[dict[str, Any]] = []
        current_measure = 0
        for raw_time, raw_beat in beat_positions:
            beat_in_measure = int(raw_beat)
            if beat_in_measure == 1:
                current_measure += 1
            elif current_measure == 0:
                current_measure = 1

            beats.append(
                {
                    "time": float(raw_time),
                    "beat_in_measure": beat_in_measure,
                    "measure": current_measure,
                    "starts_on_downbeat_window": downbeat_tolerance,
                }
            )

        downbeat_indices = [index for index, beat in enumerate(beats) if beat["beat_in_measure"] == 1]
        beats_per_bar: list[int] = []
        for start_index, end_index in zip(downbeat_indices, downbeat_indices[1:], strict=False):
            beats_per_bar.append(end_index - start_index)

        estimated_meter = None
        if beats_per_bar:
            estimated_meter = max(set(beats_per_bar), key=beats_per_bar.count)
        elif beats:
            estimated_meter = max(beat["beat_in_measure"] for beat in beats)

        tempo_bpm = None
        if len(tempo_candidates):
            tempo_bpm = float(tempo_candidates[0][0])

        return {
            "tempo_bpm": tempo_bpm,
            "tempo_candidates": [
                {"bpm": float(candidate[0]), "strength": float(candidate[1])}
                for candidate in tempo_candidates
            ],
            "estimated_meter": estimated_meter,
            "beats": beats,
        }

    def _estimate_key(self, audio_path: Path) -> tuple[str, float]:
        audio, sample_rate = librosa.load(str(audio_path), sr=22050, mono=True)
        harmonic, _ = librosa.effects.hpss(audio)
        chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sample_rate, hop_length=512, bins_per_octave=36)
        chroma = self._smooth_chroma(chroma)
        profile = self._normalize(np.mean(chroma, axis=1))

        scores = []
        for root in range(12):
            scores.append((f"{PITCH_CLASSES[root]} major", self._pearson(profile, np.roll(MAJOR_PROFILE, root))))
            scores.append((f"{PITCH_CLASSES[root]} minor", self._pearson(profile, np.roll(MINOR_PROFILE, root))))
        scores.sort(key=lambda item: item[1], reverse=True)
        best, runner_up = scores[0], scores[1]
        return best[0], max(0.0, float(best[1] - runner_up[1]))

    def _smooth_chroma(self, chroma: np.ndarray) -> np.ndarray:
        if chroma.size == 0:
            return chroma
        smoothed = np.apply_along_axis(
            lambda row: np.convolve(row, np.array([0.2, 0.6, 0.2]), mode="same"),
            axis=1,
            arr=chroma,
        )
        return np.maximum(smoothed, 0.0)

    def _normalize(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        total = float(np.sum(values))
        return np.zeros_like(values) if total <= 1e-9 else values / total

    def _pearson(self, a: np.ndarray, b: np.ndarray) -> float:
        a_centered = a - np.mean(a)
        b_centered = b - np.mean(b)
        denom = np.linalg.norm(a_centered) * np.linalg.norm(b_centered)
        return 0.0 if denom <= 1e-9 else float(np.dot(a_centered, b_centered) / denom)

    def _build_progression_from_beats(self, beat_sheet: list[dict[str, Any]]) -> list[str]:
        progression: list[str] = []
        for beat in beat_sheet:
            chord = beat["chord"]
            if chord == "N":
                continue
            if not progression or progression[-1] != chord:
                progression.append(chord)
        return progression

    def _build_beat_sheet(
        self,
        segments: list[dict[str, Any]],
        beats: list[dict[str, Any]],
        beats_per_bar: int,
        snap_window: float,
    ) -> list[dict[str, Any]]:
        if not beats:
            return []

        beat_sheet: list[dict[str, Any]] = []
        for beat_index, beat in enumerate(beats):
            start = float(beat["time"])
            end = float(beats[beat_index + 1]["time"]) if beat_index + 1 < len(beats) else float(segments[-1]["end_time"])
            best_segment = self._select_segment_for_beat(
                segments,
                start,
                end,
                int(beat["beat_in_measure"]),
                beats_per_bar,
                snap_window,
            )
            beat_sheet.append(
                {
                    "beat": beat_index + 1,
                    "measure": beat["measure"],
                    "beat_in_measure": beat["beat_in_measure"],
                    "chord": best_segment["chord"],
                    "start": round(start, 2),
                    "end": round(max(start, end), 2),
                    "confidence": round(float(best_segment.get("_beat_score", 0.0)), 3),
                    "starts_on_downbeat": beat["beat_in_measure"] == 1,
                }
            )
        return beat_sheet

    def _select_segment_for_beat(
        self,
        segments: list[dict[str, Any]],
        beat_start: float,
        beat_end: float,
        beat_in_measure: int,
        beats_per_bar: int,
        snap_window: float,
    ) -> dict[str, Any]:
        best_segment = dict(segments[0])
        best_score = -1.0

        for segment in segments:
            segment_start = float(segment["start_time"])
            segment_end = float(segment["end_time"])
            overlap = max(0.0, min(segment_end, beat_end) - max(segment_start, beat_start))
            if overlap <= 0.0 and abs(segment_start - beat_start) > snap_window:
                continue

            score = overlap / max(beat_end - beat_start, 1e-6)

            # Favor harmonic changes that start near the beat boundary.
            if abs(segment_start - beat_start) <= snap_window:
                proximity = 1.0 - (abs(segment_start - beat_start) / max(snap_window, 1e-6))
                score += 0.35 * proximity
                score += self._beat_priority_bonus(beat_in_measure, beats_per_bar) * proximity

            # Slightly prefer segments already active before the beat over future changes.
            if segment_start <= beat_start <= segment_end:
                score += 0.05

            if score > best_score:
                best_score = score
                best_segment = dict(segment)

        best_segment["_beat_score"] = max(0.0, min(1.0, best_score))
        return best_segment

    def _beat_priority_bonus(self, beat_in_measure: int, beats_per_bar: int) -> float:
        if beat_in_measure == 1:
            return 0.22
        if beats_per_bar == 6 and beat_in_measure == 4:
            return 0.12
        if beats_per_bar >= 4 and beat_in_measure == 3:
            return 0.12
        return 0.03

    def _merge_beat_sheet_to_segments(self, beat_sheet: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not beat_sheet:
            return []

        merged: list[dict[str, Any]] = []
        for beat in beat_sheet:
            chord = beat["chord"]
            if merged and merged[-1]["chord"] == chord:
                merged[-1]["end_time"] = beat["end"]
                merged[-1]["end"] = beat["end"]
                merged[-1]["beats"] += 1
                merged[-1]["confidence"] = round(max(merged[-1]["confidence"], beat["confidence"]), 3)
                continue

            merged.append(
                {
                    "start_time": beat["start"],
                    "end_time": beat["end"],
                    "start": beat["start"],
                    "end": beat["end"],
                    "chord": chord,
                    "measure": beat["measure"],
                    "beat_in_measure": beat["beat_in_measure"],
                    "starts_on_downbeat": beat["beat_in_measure"] == 1,
                    "beats": 1,
                    "confidence": beat["confidence"],
                }
            )
        return merged

    def _build_measure_sheet(self, beat_sheet: list[dict[str, Any]], beats_per_bar: int) -> list[dict[str, Any]]:
        if not beat_sheet:
            return []

        measures: list[dict[str, Any]] = []
        by_measure: dict[int, list[dict[str, Any]]] = {}
        for beat in beat_sheet:
            by_measure.setdefault(int(beat["measure"]), []).append(beat)

        for measure_number in sorted(by_measure):
            group = by_measure[measure_number]
            slots = [""] * beats_per_bar
            for entry in group:
                beat_index = max(0, min(beats_per_bar - 1, int(entry["beat_in_measure"]) - 1))
                slots[beat_index] = entry["chord"]
            measures.append(
                {
                    "measure": measure_number,
                    "start": group[0]["start"],
                    "end": group[-1]["end"],
                    "slots": slots,
                    "display_slots": self._suppress_repeats(slots),
                }
            )
        return measures

    def _suppress_repeats(self, slots: list[str]) -> list[str]:
        rendered: list[str] = []
        previous = None
        for slot in slots:
            current = slot or ""
            rendered.append(current if current and current != previous else "")
            previous = current or previous
        return rendered
