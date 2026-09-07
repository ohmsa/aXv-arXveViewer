# Three-backend Windows builds

Run from a command prompt in this directory:

```bat
build_all.bat
```

The default `fast` mode matches the existing onedir/windowed build. Optional
`build_all.bat debug` uses onedir/console; `build_all.bat final` uses onefile/windowed.
Existing `build.bat` and its wrapper scripts are unchanged.

| Variant | Additional Python dependency | EXE |
| --- | --- | --- |
| original | None (local NCNN/Vulkan engines remain available) | `dist\aXv_original\aXv.exe` |
| openVINO | `requirements-openvino.txt` | `dist\aXv_openVINO\aXv.exe` |
| directML | `requirements-directml.txt` | `dist\aXv_directML\aXv.exe` |

Each successful build creates `aXv_original.lnk`, `aXv_openVINO.lnk`, or
`aXv_directML.lnk` beside `build_all.bat`. The shortcuts use absolute targets and
the EXE directory as their working directory. Rebuild after moving the project.

`build_all.ps1` uses independent embedded Python 3.11.9 installations under
`build\all\<variant>\python_embed`, with separate package directories, PyInstaller
work directories and specs. It reuses the original Python ZIP/bootstrap file when
available, but never copies installed packages from the existing `python_embed`.
Each environment installs `requirements-build.txt` and only its own optional
requirements. It checks for conflicting backend distributions and runs `pip check`.
If an environment has been manually contaminated, remove only that variant's
generated `build\all\<variant>\python_embed` directory and rerun.

Python and package downloads require internet access on the first build. Package
versions follow the existing requirement ranges, so this is not a locked build.
The script stops with a nonzero exit code on failure, without pausing for input.
Existing generated `_internal` libraries are replaced on an onedir rebuild;
settings and user assets are preserved. Do not run the output EXEs during rebuilds.

Local `ai_upscale` assets and optional `unrar.exe` / `tlg6_native.dll`, plus notices,
are copied as in the existing build. Engines and models are not downloaded. Models
for OpenVINO/DirectML belong in `ai_upscale\openvino_models` beside each EXE.

On Windows, 7z files are read with the signed `tar.exe` (bsdtar) included with
Windows 10/11. `py7zr` is retained for non-Windows source runs only. This avoids
the unsigned `_zstd`, `inflate64`, `pybcj`, and `pyppmd` native extensions that
Windows App Control may block. The build scripts remove stale copies of those
packages before packaging. AES ZIP support remains provided by `pyzipper`.

AI processing, difference-based acceleration, and skipping small images now default
to enabled. Existing saved choices are preserved. AI processing still follows the
existing scaling/noise rules and requires an available engine and model.

ONNX inputs now use the first session input's type: `tensor(float16)` maps to
NumPy float16 and `tensor(float)` to float32. Other input types raise a clear error.
`_qimage_to_nchw(image, dtype=np.float32)` also preserves its previous default.

Regression tests (no GPU/model required):

```bat
build\all\original\python_embed\python.exe -m unittest -v test_regressions
```
