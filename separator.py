from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any
import shutil
import sys
import traceback
import uuid
import importlib.util

import librosa
import numpy as np
import soundfile as sf
import torch

from analyzer import PianoChordAnalyzer


@dataclass
class JobRecord:
    job_id: str
    filename: str
    track_id: int | None = None
    kind: str = "separation"
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    result_files: dict[str, str] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


class KeysIsolationService:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.jobs_dir = base_dir / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.cache_dir = base_dir / ".cache" / "torch"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = Lock()
        self._on_change = None
        self.piano_analyzer = PianoChordAnalyzer()

    def set_on_change(self, callback) -> None:
        self._on_change = callback

    def create_job(self, source_name: str, settings: dict[str, Any], track_id: int | None = None, kind: str = "separation") -> JobRecord:
        job_id = uuid.uuid4().hex[:12]
        job = JobRecord(job_id=job_id, filename=source_name, track_id=track_id, kind=kind, settings=settings)
        with self._lock:
            self._jobs[job_id] = job
        self._job_dir(job_id).mkdir(parents=True, exist_ok=True)
        return job

    def save_upload(self, job_id: str, source_name: str, data: bytes) -> Path:
        path = self._job_dir(job_id) / f"input{Path(source_name).suffix.lower() or '.wav'}"
        path.write_bytes(data)
        return path

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return JobRecord(**asdict(job))

    def process_job(self, job_id: str, input_path: Path) -> None:
        try:
            if self._jobs[job_id].kind == "analysis":
                self._process_analysis_job(job_id, input_path)
            else:
                self._process_separation_job(job_id, input_path)
        except Exception as error:
            formatted_traceback = "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()
            details = "".join(traceback.format_exception_only(type(error), error)).strip()
            self._append_log(job_id, formatted_traceback)
            self._update(job_id, status="error", stage="failed", error=details)

    def _process_separation_job(self, job_id: str, input_path: Path) -> None:
        self._update(job_id, status="running", stage="preparing", progress=0.05)
        output_root = self._job_dir(job_id) / "outputs"
        output_root.mkdir(exist_ok=True)

        self._append_log(job_id, "Starting Demucs separation.")
        self._update(job_id, stage="separating", progress=0.15)
        piano_path, residual_path = self._run_demucs(job_id, input_path, output_root)

        self._append_log(job_id, "Refining piano stem with harmonic masking.")
        self._update(job_id, stage="refining", progress=0.65)
        refined_path, harmonic_path = self._refine_keys_stem(
            input_path=input_path,
            piano_path=piano_path,
            residual_path=residual_path,
            output_root=output_root,
            aggressive=bool(self._jobs[job_id].settings.get("aggressive_refine", True)),
        )

        self._append_log(job_id, "Packaging outputs.")
        self._update(job_id, stage="finalizing", progress=0.92)
        result_files = {
            "isolated_keys": refined_path.name,
            "harmonic_keys": harmonic_path.name,
            "raw_piano_stem": piano_path.name,
            "residual_stem": residual_path.name,
        }
        self._update(job_id, status="done", stage="done", progress=1.0, result_files=result_files, analysis={})

    def _process_analysis_job(self, job_id: str, input_path: Path) -> None:
        self._update(job_id, status="running", stage="preparing", progress=0.08)
        output_root = self._job_dir(job_id) / "outputs"
        output_root.mkdir(exist_ok=True)
        source_copy = output_root / f"analysis_source{input_path.suffix.lower() or '.wav'}"
        source_copy.write_bytes(input_path.read_bytes())

        self._append_log(job_id, "Loading audio file for harmonic analysis.")
        self._update(job_id, stage="analyzing", progress=0.45)
        analysis = self.piano_analyzer.analyze_file(input_path)
        if "error" in analysis:
            raise RuntimeError(analysis["error"])

        self._append_log(job_id, "Formatting chord sheet.")
        self._update(job_id, stage="finalizing", progress=0.88)
        result_files = {"analysis_source": source_copy.name}
        self._update(job_id, status="done", stage="done", progress=1.0, result_files=result_files, analysis=analysis)

    def cleanup_job(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def resolve_download(self, job_id: str, filename: str) -> Path:
        path = (self._job_dir(job_id) / "outputs" / filename).resolve()
        outputs_dir = (self._job_dir(job_id) / "outputs").resolve()
        if outputs_dir not in path.parents:
            raise FileNotFoundError(filename)
        if not path.exists():
            raise FileNotFoundError(filename)
        return path

    def _run_demucs(self, job_id: str, input_path: Path, output_root: Path) -> tuple[Path, Path]:
        self._check_runtime_dependencies()
        settings = self._jobs[job_id].settings
        model = str(settings.get("model", "htdemucs_6s"))
        shifts = int(settings.get("shifts", 4))
        overlap = float(settings.get("overlap", 0.5))
        segment = int(round(float(settings.get("segment", 7))))
        jobs = int(settings.get("jobs", 0))
        self._append_log(job_id, f"Loading Demucs model '{model}'.")

        from demucs import apply, pretrained

        torch.hub.set_dir(str(self.cache_dir))
        demucs_model = pretrained.get_model(model)
        demucs_model.to("cuda" if torch.cuda.is_available() else "cpu")
        demucs_model.eval()

        audio, sample_rate = sf.read(input_path, always_2d=True, dtype="float32")
        waveform = torch.from_numpy(audio.T.copy())
        if sample_rate != demucs_model.samplerate:
            waveform = torch.from_numpy(
                librosa.resample(
                    waveform.numpy(),
                    orig_sr=sample_rate,
                    target_sr=demucs_model.samplerate,
                    axis=1,
                ).copy()
            )
            sample_rate = demucs_model.samplerate

        separated = apply.apply_model(
            demucs_model,
            waveform[None],
            shifts=shifts,
            split=True,
            overlap=overlap,
            progress=False,
            device="cuda" if torch.cuda.is_available() else "cpu",
            num_workers=jobs,
            segment=float(segment),
        )[0]

        source_names = list(demucs_model.sources)
        separated_map = {
            source_name: separated[index]
            for index, source_name in enumerate(source_names)
        }

        if "piano" not in separated_map:
            available = ", ".join(sorted(separated_map.keys()))
            raise RuntimeError(f"Model '{model}' did not return a piano stem. Available stems: {available}")

        piano = separated_map["piano"].detach().cpu().numpy()
        residual = np.zeros_like(piano)
        for stem_name, stem_audio in separated_map.items():
            if stem_name == "piano":
                continue
            residual += stem_audio.detach().cpu().numpy()

        piano_path = output_root / "piano.wav"
        residual_path = output_root / "no_piano.wav"
        sf.write(piano_path, piano.T, sample_rate, subtype="PCM_24")
        sf.write(residual_path, residual.T, sample_rate, subtype="PCM_24")
        return piano_path, residual_path

    def _check_runtime_dependencies(self) -> None:
        missing = [
            package
            for package in ["demucs", "torch"]
            if importlib.util.find_spec(package) is None
        ]
        if missing:
            version = sys.version.split()[0]
            raise RuntimeError(
                "Missing runtime dependencies for separation: "
                f"{', '.join(missing)}. Current Python: {version}. "
                "Create a Python 3.11 or 3.12 environment, then install requirements again."
            )
        if importlib.util.find_spec("torchaudio") is not None:
            import torchaudio

            torch_major_minor = ".".join(torch.__version__.split("+")[0].split(".")[:2])
            torchaudio_major_minor = ".".join(torchaudio.__version__.split("+")[0].split(".")[:2])
            if torch_major_minor != torchaudio_major_minor:
                raise RuntimeError(
                    "PyTorch package mismatch detected: "
                    f"torch={torch.__version__}, torchaudio={torchaudio.__version__}. "
                    "Install matching versions in the same venv, then restart uvicorn."
                )

    def _demucs_callback(self, job_id: str):
        def callback(update: dict[str, Any]) -> None:
            offset = float(update.get("segment_offset", 0.0))
            length = max(1.0, float(update.get("audio_length", 1.0)))
            progress = min(0.64, 0.15 + 0.5 * (offset / length))
            if update.get("state") == "end":
                progress = min(0.64, progress + 0.02)
            self._update(job_id, stage="separating", progress=progress)

        return callback

    def _refine_keys_stem(
        self,
        input_path: Path,
        piano_path: Path,
        residual_path: Path,
        output_root: Path,
        aggressive: bool,
    ) -> tuple[Path, Path]:
        mix, sample_rate = librosa.load(input_path, sr=None, mono=False)
        piano, _ = librosa.load(piano_path, sr=sample_rate, mono=False)
        residual, _ = librosa.load(residual_path, sr=sample_rate, mono=False)

        mix = self._ensure_stereo(mix)
        piano = self._ensure_stereo(piano)
        residual = self._ensure_stereo(residual)
        mix, piano, residual = self._align_lengths(mix, piano, residual)

        refined_channels = []
        harmonic_channels = []
        power = 2.4 if aggressive else 1.8
        harmonic_mix = 0.78 if aggressive else 0.62
        residual_penalty = 1.25 if aggressive else 1.0

        for channel_index in range(mix.shape[0]):
            mix_channel = mix[channel_index]
            piano_channel = piano[channel_index]
            residual_channel = residual[channel_index]

            piano_stft = librosa.stft(piano_channel, n_fft=4096, hop_length=1024)
            residual_stft = librosa.stft(residual_channel, n_fft=4096, hop_length=1024)
            mix_stft = librosa.stft(mix_channel, n_fft=4096, hop_length=1024)

            harmonic_stft, percussive_stft = librosa.decompose.hpss(piano_stft)
            piano_mag = np.abs(harmonic_stft + percussive_stft * (1.0 - harmonic_mix))
            residual_mag = np.abs(residual_stft) * residual_penalty

            soft_mask = (piano_mag**power) / ((piano_mag**power) + (residual_mag**power) + 1e-9)
            refined_stft = mix_stft * soft_mask
            harmonic_only = harmonic_stft

            refined_channel = librosa.istft(refined_stft, hop_length=1024, length=mix_channel.shape[-1])
            harmonic_channel = librosa.istft(harmonic_only, hop_length=1024, length=mix_channel.shape[-1])

            refined_channels.append(refined_channel)
            harmonic_channels.append(harmonic_channel)

        refined_audio = np.vstack(refined_channels)
        harmonic_audio = np.vstack(harmonic_channels)

        refined_audio = self._peak_normalize(refined_audio)
        harmonic_audio = self._peak_normalize(harmonic_audio)

        refined_path = output_root / "keys_isolated_refined.wav"
        harmonic_path = output_root / "keys_isolated_harmonic.wav"
        sf.write(refined_path, refined_audio.T, sample_rate, subtype="PCM_24")
        sf.write(harmonic_path, harmonic_audio.T, sample_rate, subtype="PCM_24")
        return refined_path, harmonic_path

    def _ensure_stereo(self, audio: np.ndarray) -> np.ndarray:
        if audio.ndim == 1:
            return np.stack([audio, audio], axis=0)
        return audio

    def _align_lengths(self, *arrays: np.ndarray) -> tuple[np.ndarray, ...]:
        length = min(array.shape[-1] for array in arrays)
        return tuple(array[..., :length] for array in arrays)

    def _peak_normalize(self, audio: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak <= 1e-8:
            return audio.astype(np.float32, copy=False)
        return (audio / peak * 0.96).astype(np.float32, copy=False)

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(message)
        self._emit_change(job_id)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
        self._emit_change(job_id)

    def _emit_change(self, job_id: str) -> None:
        if self._on_change is not None:
            self._on_change(job_id)
