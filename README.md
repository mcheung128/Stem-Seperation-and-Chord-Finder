# Chord Expo

This project is now a Python webapp with two saved-library workflows:

- stem separation for full mixes
- key, tempo, and chord progression analysis for isolated piano files

## What it does

The app supports local accounts and one shared saved library. You can upload audio once in either tab, keep the generated outputs or analysis, and reopen that saved result later without rerunning the job.

For stem-separation tracks, the app returns:

- `raw_piano_stem`: the direct model output
- `harmonic_keys`: harmonic-only reconstruction from the piano stem
- `isolated_keys`: a refined keys stem rebuilt with a soft spectral mask over the original mix
- `residual_stem`: the complementary non-piano stem

For isolated-piano analysis tracks, the app returns:

- global key estimate
- tempo estimate
- chord progression
- measure-by-measure chord sheet
- beat-level chord detail
- optional tempo-constrained and key-constrained analysis when you already know those values

## Accuracy strategy

The pipeline is intentionally more complex than a plain separator call:

1. Run Demucs with a piano-capable model.
2. Split out the piano stem and residual accompaniment.
3. Apply HPSS to the piano stem to emphasize sustained harmonic content.
4. Build a soft spectral mask from piano-vs-residual energy.
5. Reconstruct the final keys stem from the original mixture using that mask.
6. In the analysis tab, estimate key, tempo, and a beat-synchronous chord progression directly from an isolated piano file.

That gives cleaner piano isolation than returning the raw model output alone, especially when the keyboard part shares frequency range with guitars, pads, or room ambience.

The backend now uses the Demucs Python API with direct waveform loading instead of shelling out to the Demucs CLI. That avoids the recent `torchaudio` and `TorchCodec` loader breakage path.

## Run

Use Python 3.11 or 3.12 for this project. The current code path depends on `torch` and `demucs`, and those are the first things to break on unsupported Python builds.

```powershell
cd C:\Users\micha\SynologyDrive\2025-2026\Summer_Projects\Chord_Expo
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip uninstall -y torch torchaudio
python -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

## Notes

- Accounts and saved tracks are stored locally in `app.db`.
- Generated audio files remain in the project `jobs` folder and are linked from the saved library.
- The saved library now contains both separation jobs and piano-analysis jobs.
- The default model is `htdemucs_6s`, because it exposes a dedicated piano source.
- Processing is offline, not real-time.
- Large files and higher `shifts` values increase quality but also increase runtime.
- The separation tab no longer includes the old chord-progression panel.
- The analysis tab includes key, tempo, progression, measure sheet, and beat detail.
- In the analysis tab, `Exact BPM` is the strongest beat-tracking constraint. `Min BPM` and `Max BPM` narrow tempo search when you only know a range. `Known key` skips key estimation and biases chord scoring toward that harmony.
- The progression analyzer includes more voicings such as `6`, `m6`, `7`, `maj7`, `m7`, `mMaj7`, `add9`, `9`, `maj9`, `m9`, `sus2`, `sus4`, `dim`, `dim7`, `m7b5`, and slash-chord inversions when the bass note supports them.
- If the job fails immediately, check that the server is not running on Python 3.13 and that `torch` and `torchaudio` are the same version in the venv.
