# Chord Expo

This project is a Python webapp with two workflows:

- stem separation for full mixes
- key, tempo, and chord progression analysis for isolated piano files

## What it does

You can upload audio once in either tab, keep the generated outputs or analysis, and reopen that saved result later without rerunning the job.

For stem-separation tracks, the app returns:

- `raw_piano_stem`: the direct model output
- `harmonic_keys`: harmonic-only reconstruction from the piano stem
- `isolated_keys`: a refined keys stem rebuilt with a soft spectral mask over the original mix
- `residual_stem`: the complementary non-piano stem

For isolated-piano analysis tracks, the app returns:

- global key estimate
- tempo estimate
- measure-by-measure chord sheet

## Accuracy strategy

The pipeline is intentionally more complex than a plain separator call:

1. Run Demucs with a piano-capable model.
2. Split out the piano stem and residual accompaniment.
3. Apply HPSS to the piano stem to emphasize sustained harmonic content.
4. Build a soft spectral mask from piano-vs-residual energy.
5. Reconstruct the final keys stem from the original mixture using that mask.

That gives cleaner piano isolation than returning the raw model output alone, especially when the keyboard part shares frequency range with guitars, pads, or room ambience.

The backend now uses the Demucs Python API with direct waveform loading instead of shelling out to the Demucs CLI. That avoids the recent `torchaudio` and `TorchCodec` loader breakage path.

## Run

Use Python 3.11 or 3.12 for this project.

```powershell
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
- The saved library now contains both separation jobs and analysis jobs.
- The default model is `htdemucs_6s`, because it exposes a dedicated piano source.
- Processing is offline, not real-time.
- Large files and higher `shifts` values increase quality but also increase runtime.
- The analysis tab includes key, tempo, and progression.
