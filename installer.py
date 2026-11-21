#!/usr/bin/env python3
"""Complete source installer for the engine, independent editor and empty game workspace."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True

COMPONENTS = {'jsd.engine': ('3d-engine', '0.5.3'), 'jsd.editor': ('jsd-engine-editor', '1.1.1')}
class InstallError(RuntimeError):
    pass

def require(condition, message):
    if not condition:
        raise InstallError(message)

def safe_path(root, rel):
    require(isinstance(rel, str) and rel and not Path(rel).is_absolute() and '\\' not in rel and all(part not in ('', '.', '..') for part in rel.split('/')), f'Invalid installer path: {rel}')
    path = root / rel
    for item in (path, *path.parents):
        require(not item.is_symlink(), f'Symlink installation path is not allowed: {item}')
    require(root in path.resolve().parents, f'Installer path escapes workspace: {rel}')
    return path

def validate(root, args):
    require(not os.environ.get('DESTDIR'), 'DESTDIR is not supported; install into the extracted workspace')
    manifest_path = safe_path(root, 'FULL_INSTALL.json')
    data = json.loads(manifest_path.read_text())
    require(data.get('format') == 'jsd-full-installer' and data.get('schema') == 1, 'Unsupported installer manifest')
    require(data.get('components') == {pid: {'directory': folder, 'version': ver} for pid, (folder, ver) in COMPONENTS.items()}, 'Installer component versions/layout do not match this release')
    require(data.get('game_directory') == '3d-game', 'Wrong game directory in installer manifest')
    require(data.get('files'), 'Installer manifest has no payload files')
    # Validate every source, bundled dependency and asset before running project code.
    for rel, entry in data['files'].items():
        path = safe_path(root, rel)
        require(path.is_file(), f'Incomplete installer; missing {rel}')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], f'Changed/corrupt installer payload: {rel}. Use project build.sh for edited sources; do not reinstall over them.')
        require(path.stat().st_mode & 0o777 == entry['mode'], f'Incorrect executable/file permissions: {rel}; extract with a tool that preserves ZIP permissions')
    for directory in ('3d-engine', 'jsd-engine-editor', '3d-game'):
        require(safe_path(root, directory).is_dir(), f'Missing workspace folder: {directory}')
    # The projects keep their own guards and remain buildable without this installer.
    sys.path.insert(0, str(root / '3d-engine/tools'))
    guard = importlib.import_module('project_guard')
    build = importlib.import_module('build_project')
    metas = {pid: guard.source(root / folder, pid, ver) for pid, (folder, ver) in COMPONENTS.items()}
    guard.compatible(metas['jsd.engine'], metas['jsd.editor'])
    plans = {}
    for pid, (folder, _) in COMPONENTS.items():
        project = root / folder
        env = build_environment(root, args, pid)
        options = [args.mode]
        if pid == 'jsd.editor':
            options += ['-DJSD_EDITOR_USE_INSTALLED_ENGINE=OFF', '-DJSD_ENGINE_SOURCE_DIR=' + str(root / '3d-engine')]
        plans[pid] = build.preflight(project, pid, metas[pid]['version'], options, env)
    return data

def build_environment(root, args, pid):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', JSD_AUTO_INSTALL_DEPS='0')
    # Installer outputs always stay within the corresponding project.
    env['JSD_BUILD_DIR'] = str(root / '3d-engine/build' / args.mode)
    env['JSD_INSTALL_PREFIX'] = str(root / '3d-engine')
    env['JSD_EDITOR_BUILD_DIR'] = str(root / 'jsd-engine-editor/build')
    env['JSD_EDITOR_INSTALL_PREFIX'] = str(root / 'jsd-engine-editor')
    if args.headless:
        env['JSD_HEADLESS_ONLY'] = 'ON'
    return env

@contextlib.contextmanager
def installer_lock(root):
    path = safe_path(root, '.jsd-full-installer.lock')
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError('Another full installation is already running in this workspace') from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

def run(root, args):
    validate(root, args)
    if args.check:
        print('Installer verified: engine 0.5.3, editor 1.1.1, separate game folder; no files changed')
        return
    if args.source_only:
        print('Complete source workspace verified and ready; no compilation or dependency installation requested')
        return
    with installer_lock(root):
        validate(root, args)
        engine = root / '3d-engine'
        editor = root / 'jsd-engine-editor'
        engine_env = build_environment(root, args, 'jsd.engine')
        editor_env = build_environment(root, args, 'jsd.editor')
        if args.install_deps:
            subprocess.run([str(engine / 'install_deps.sh')], check=True, env=engine_env)
        cmake = engine_env.get('CMAKE', 'cmake')
        require(shutil.which(cmake), 'CMake 3.21+ is required. Run ./install.sh --install-deps or set CMAKE to the installed executable')
        probe = subprocess.run([cmake, '--version'], capture_output=True, text=True, check=True)
        match = re.search(r'cmake version (\d+)\.(\d+)\.(\d+)', probe.stdout)
        require(match and tuple(map(int, match.groups())) >= (3, 21, 0), 'CMake 3.21+ is required')
        print('Installing engine 0.5.3 from bundled source', flush=True)
        subprocess.run([str(engine / 'build.sh'), args.mode, '-DJSD_BUILD_SHOWCASE=ON', '-DJSD_BUILD_TESTS=ON', '-DJSD_BUILD_EXAMPLES=ON'], check=True, env=engine_env)
        # Recheck the fixed release before starting the other project.
        validate(root, args)
        print('Installing editor 1.1.1 from bundled source', flush=True)
        subprocess.run([str(editor / 'build.sh'), args.mode, '-DJSD_EDITOR_USE_INSTALLED_ENGINE=OFF', '-DJSD_ENGINE_SOURCE_DIR=' + str(engine), '-DJSD_EDITOR_TESTS=ON'], check=True, env=editor_env)
        subprocess.run([cmake, '--install', str(editor / 'build'), '--prefix', str(editor)], check=True, env=editor_env)
        if args.test:
            ctest = str(Path(shutil.which(cmake)).parent / 'ctest')
            if not Path(ctest).is_file():
                ctest = engine_env.get('CTEST', 'ctest')
            test_env = dict(engine_env)
            for key in ('JSD_BUILD_DIR', 'JSD_INSTALL_PREFIX', 'JSD_EDITOR_BUILD_DIR', 'JSD_EDITOR_INSTALL_PREFIX'):
                test_env.pop(key, None)
            for build_dir in (engine / 'build' / args.mode, editor / 'build'):
                subprocess.run([ctest, '--test-dir', str(build_dir), '--output-on-failure'], check=True, env=test_env)
        print('Installed engine and editor. Run 3d-engine/run-showcase.sh or jsd-engine-editor/run.sh. Game content stays in 3d-game.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify the complete package, versions and build paths without writing files')
    parser.add_argument('--source-only', action='store_true', help='Verify the complete extracted sources without compiling')
    parser.add_argument('--install-deps', action='store_true', help='Install OS/compiler/graphics development packages using the engine dependency script; may require sudo/network')
    parser.add_argument('--mode', choices=('release', 'debug'), default='release')
    parser.add_argument('--headless', action='store_true', help='EGL validation build; use normal desktop mode for an interactive editor/showcase')
    parser.add_argument('--test', action='store_true', help='Run engine and editor CTest suites after installation')
    args = parser.parse_args()
    require(not (args.source_only and (args.install_deps or args.test)), '--source-only cannot be combined with --install-deps or --test')
    run(Path(__file__).resolve().parent, args)

if __name__ == '__main__':
    try:
        main()
    except (InstallError, OSError, KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Installation stopped: {error}', file=sys.stderr)
        sys.exit(2)
