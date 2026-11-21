# Full 3D workspace installer

This is a complete source installer. No earlier release or update ZIP is needed.
It includes all engine/editor source code, bundled third-party source, licenses,
showcase assets, the complete showcase project, and editor example projects.

| Folder | Contents |
| --- | --- |
| `3d-engine` | Engine 0.5.3, graphics lab, project player and SUNDOG showcase |
| `jsd-engine-editor` | Independent JSD Studio editor 1.1.1 |
| `3d-game` | Empty, separate folder reserved for your game |

## Install on desktop Linux

Extract the ZIP into a **new directory**, preserving file permissions. Do not
merge it over an existing installation. Enter the extracted installer directory:

```bash
./install.sh --check
./install.sh --install-deps
```

The installer checks the complete source/asset payload, project identities,
versions, engine/editor compatibility and both projects' build paths before
starting. OS package setup supports apt, dnf and pacman and may ask for sudo;
it is only run with `--install-deps`. If dependencies are already installed:

```bash
./install.sh
```

Installation compiles the bundled SDL2, Jolt, engine and editor locally and
installs each project's runtime into its own directory. It also enables the
optional showcase. The game folder receives no files. No global engine/editor
installation or network source download is required. System compilers, GPU
runtime/driver and OS development libraries remain prerequisites.

Python 3, CMake 3.21+, a C++20 compiler, make/ninja, pkg-config and desktop
OpenGL/EGL/window/audio development headers are needed. Desktop runtime requires
OpenGL 3.3+. Set `CMAKE=/path/to/cmake` or `JSD_BUILD_JOBS=8` as needed. This
package targets Linux; Windows installation is not provided.

For source inspection without compiling, use `./install.sh --source-only`.
`--mode debug` selects a Debug build; Release is the default. `--test` runs
both projects' CTest suites after installation. `--headless` enables an EGL
validation build for machines without desktop development headers; it is not
the normal interactive desktop mode.

## Run

From this workspace directory:

```bash
./3d-engine/run-showcase.sh
./3d-engine/run.sh
./3d-engine/run-project.sh
./jsd-engine-editor/run.sh
```

The launch scripts incrementally build and validate the selected project before
launching. Installed executables are also in each project's `bin/` directory.
The complete editable showcase is
`3d-engine/projects/sundog/sundog.jsdproject`; open it from the editor's File
menu or the dedicated showcase action. `C` opens the showcase graphics overlay.

## Independent projects and later updates

Each project retains its own build/run/clean scripts and version guards. Use
its `./build.sh release` after editing sources or projects. The root installer
validates the exact initial release and refuses modified payload files; it is
not the everyday build command for edited sources. Moving a configured tree
requires a fresh build cache, because caches from another source path are
rejected. Documentation mentioning `jsd-editor` or `jsd-game-engine` uses older
folder names; here they are `jsd-engine-editor` and `3d-engine` respectively.

This full package is a one-time distribution. Future changes can continue as
version-checked update packages. The engine accepts no editor dependency; the
editor uses sibling `../3d-engine` and requires engine API 1, >= 0.5.3 and < 0.6.0.
Keep `3d-game` separate. Existing game contents are preserved when rerunning the
installer; no game or default game project is generated.

All executable/library code is built from source. No compiled binaries,
prebuilds, private build caches or machine-specific SDK files are shipped.
The included `FULL_INSTALL.json`, `SHA256SUMS` and `VALIDATION.json` record the
release payload and checks. Project licenses and third-party credits remain
inside their respective folders.
