#!/usr/bin/env python3
"""Build the current engine, editor and game sources in this workspace."""
import argparse
import contextlib
import fcntl
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True

COMPONENTS = {'jsd.engine': '3d-engine', 'jsd.editor': 'jsd-engine-editor'}
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
    for directory in (*COMPONENTS.values(), 'red-wasteland'):
        require(safe_path(root, directory).is_dir(), f'Missing workspace folder: {directory}')
    # Build current sources. The archive's release manifest is not a build input.
    sys.path.insert(0, str(root / '3d-engine/tools'))
    guard = importlib.import_module('project_guard')
    build = importlib.import_module('build_project')
    metas = {pid: guard.source(root / folder, pid) for pid, folder in COMPONENTS.items()}
    guard.compatible(metas['jsd.engine'], metas['jsd.editor'])
    for pid, folder in COMPONENTS.items():
        project = root / folder
        require(safe_path(project, 'build.sh').is_file(), f'Missing build script: {folder}/build.sh')
        env = build_environment(root, args, pid)
        options = [args.mode]
        if pid == 'jsd.editor':
            options += ['-DJSD_EDITOR_USE_INSTALLED_ENGINE=OFF', '-DJSD_ENGINE_SOURCE_DIR=' + str(root / '3d-engine')]
        build.preflight(project, pid, metas[pid]['version'], options, env)
    game = root / 'red-wasteland'
    require(not (game / '.jsd-update-journal.json').exists(), 'Incomplete update in red-wasteland; restore its update backup first')
    script = safe_path(game, 'build.sh')
    cmake_file = safe_path(game, 'CMakeLists.txt')
    require(script.is_file() or cmake_file.is_file(),
            'red-wasteland has no build.sh or CMakeLists.txt. Add your game sources before building all three projects.')
    game_build = safe_path(game, 'build/' + args.mode)
    guard.output_path(game, game_build)
    cache = guard.cache_values(game_build)
    if cache:
        require(Path(cache.get('CMAKE_HOME_DIRECTORY', '')).resolve() == game.resolve(),
                f'Game build cache belongs to another source tree: {game_build}')
    elif game_build.exists() and not script.is_file():
        require(not any(game_build.iterdir()), f'Nonempty game build directory has no CMake cache: {game_build}')
    game_meta = safe_path(game, 'JSD_PROJECT.json')
    if game_meta.exists():
        meta = json.loads(game_meta.read_text())
        require(meta.get('project_id') not in COMPONENTS, 'red-wasteland contains an engine/editor project')
        if cmake_file.exists() and meta.get('version'):
            _, ver = guard.cmake_identity(game)
            require(ver == meta['version'], 'Mixed game project versions')
            guard.version(ver)
    if cmake_file.exists():
        declaration = re.search(r'project\(\s*([\w-]+)', cmake_file.read_text(), re.IGNORECASE)
        require(declaration and declaration[1] not in ('JSDGameEngine', 'JSDStudio'),
                'red-wasteland must declare its own CMake project')
        if cache:
            require(cache.get('CMAKE_PROJECT_NAME') == declaration[1], 'Game build cache belongs to another project')
    return metas, game_build, script.is_file()

def build_environment(root, args, pid):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', JSD_AUTO_INSTALL_DEPS='0')
    # Installer outputs always stay within the corresponding project.
    env['JSD_BUILD_DIR'] = str(root / '3d-engine/build' / args.mode)
    env['JSD_INSTALL_PREFIX'] = str(root / '3d-engine')
    env['JSD_EDITOR_BUILD_DIR'] = str(root / 'jsd-engine-editor/build')
    env['JSD_EDITOR_INSTALL_PREFIX'] = str(root / 'jsd-engine-editor')
    if pid == 'jsd.game':
        env['JSD_BUILD_DIR'] = str(root / 'red-wasteland/build' / args.mode)
        env['JSD_GAME_BUILD_DIR'] = env['JSD_BUILD_DIR']
        env['JSD_INSTALL_PREFIX'] = str(root / 'red-wasteland')
        env['JSD_GAME_INSTALL_PREFIX'] = str(root / 'red-wasteland')
        env['JSD_ENGINE_SOURCE_DIR'] = str(root / '3d-engine')
        env['CMAKE_PREFIX_PATH'] = str(root / '3d-engine') + (';' + env['CMAKE_PREFIX_PATH'] if env.get('CMAKE_PREFIX_PATH') else '')
    env['JSD_HEADLESS_ONLY'] = 'ON' if args.headless else 'OFF'
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
    metas, game_build, game_script = validate(root, args)
    if args.check:
        print(f"Workspace verified: engine {metas['jsd.engine']['version']}, editor {metas['jsd.editor']['version']}, buildable game; no files changed")
        return
    if args.source_only:
        print('All three source projects verified; no compilation or dependency installation requested')
        return
    with installer_lock(root):
        metas, game_build, game_script = validate(root, args)
        engine = root / '3d-engine'
        editor = root / 'jsd-engine-editor'
        engine_env = build_environment(root, args, 'jsd.engine')
        editor_env = build_environment(root, args, 'jsd.editor')
        if args.install_deps:
            subprocess.run(['bash', str(engine / 'install_deps.sh')], check=True, env=engine_env)
        cmake = engine_env.get('CMAKE', 'cmake')
        require(shutil.which(cmake), 'CMake 3.21+ is required. Run ./install.sh --install-deps or set CMAKE to the installed executable')
        probe = subprocess.run([cmake, '--version'], capture_output=True, text=True, check=True)
        match = re.search(r'cmake version (\d+)\.(\d+)\.(\d+)', probe.stdout)
        require(match and tuple(map(int, match.groups())) >= (3, 21, 0), 'CMake 3.21+ is required')
        print(f"Building engine {metas['jsd.engine']['version']}", flush=True)
        subprocess.run(['bash', str(engine / 'build.sh'), args.mode, '-DJSD_BUILD_SHOWCASE=ON', '-DJSD_BUILD_TESTS=ON', '-DJSD_BUILD_EXAMPLES=ON'], check=True, env=engine_env)
        # Recheck source identities and compatibility before the next project.
        validate(root, args)
        print(f"Building editor {metas['jsd.editor']['version']}", flush=True)
        subprocess.run(['bash', str(editor / 'build.sh'), args.mode, '-DJSD_EDITOR_USE_INSTALLED_ENGINE=OFF', '-DJSD_ENGINE_SOURCE_DIR=' + str(engine), '-DJSD_EDITOR_TESTS=ON'], check=True, env=editor_env)
        subprocess.run([cmake, '--install', str(editor / 'build'), '--prefix', str(editor)], check=True, env=editor_env)
        validate(root, args)
        print('Building game in red-wasteland', flush=True)
        game = root / 'red-wasteland'
        game_env = build_environment(root, args, 'jsd.game')
        if game_script:
            subprocess.run(['bash', str(game / 'build.sh'), args.mode], cwd=game, check=True, env=game_env)
        else:
            subprocess.run([cmake, '-S', str(game), '-B', str(game_build),
                            '-DCMAKE_BUILD_TYPE=' + ('Debug' if args.mode == 'debug' else 'Release'),
                            '-DCMAKE_PREFIX_PATH=' + game_env['CMAKE_PREFIX_PATH'],
                            '-DCMAKE_INSTALL_PREFIX=' + str(game)], check=True, env=game_env)
            jobs = game_env.get('JSD_BUILD_JOBS', '4')
            require(jobs.isdigit() and int(jobs) > 0, 'JSD_BUILD_JOBS must be a positive integer')
            subprocess.run([cmake, '--build', str(game_build), '--parallel', jobs], check=True, env=game_env)
        if args.test:
            ctest = str(Path(shutil.which(cmake)).parent / 'ctest')
            if not Path(ctest).is_file():
                ctest = engine_env.get('CTEST', 'ctest')
            test_env = dict(engine_env)
            for key in ('JSD_BUILD_DIR', 'JSD_INSTALL_PREFIX', 'JSD_EDITOR_BUILD_DIR', 'JSD_EDITOR_INSTALL_PREFIX'):
                test_env.pop(key, None)
            test_dirs = [engine / 'build' / args.mode, editor / 'build']
            if (game_build / 'CMakeCache.txt').is_file():
                test_dirs.append(game_build)
            elif game_script:
                print('Game script did not create a CMake cache in its configured build directory; game CTest not run', flush=True)
            for build_dir in test_dirs:
                subprocess.run([ctest, '--test-dir', str(build_dir), '--output-on-failure'], check=True, env=test_env)
        print('Built all three projects: 3d-engine, jsd-engine-editor and red-wasteland.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify current project identities, compatibility and build paths without writing files')
    parser.add_argument('--source-only', action='store_true', help='Verify all three current source projects without compiling')
    parser.add_argument('--install-deps', action='store_true', help='Install OS/compiler/graphics development packages using the engine dependency script; may require sudo/network')
    parser.add_argument('--mode', choices=('release', 'debug'), default='release')
    parser.add_argument('--headless', action='store_true', help='EGL validation build; use normal desktop mode for an interactive editor/showcase')
    parser.add_argument('--test', action='store_true', help='Run CTest for engine, editor and the game when its configured CMake build is available')
    args = parser.parse_args()
    require(not (args.source_only and (args.install_deps or args.test)), '--source-only cannot be combined with --install-deps or --test')
    run(Path(__file__).resolve().parent, args)

if __name__ == '__main__':
    try:
        main()
    except (InstallError, OSError, KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Installation stopped: {error}', file=sys.stderr)
        sys.exit(2)
