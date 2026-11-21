#!/usr/bin/env bash
# Place beside installer.py; run: bash cleanall.sh [--dry-run] [--artifact PATH ...]
set -euo pipefail
clean_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 /dev/fd/3 "$clean_root" "$@" 3<<'PY'
import argparse
import contextlib
import fcntl
import fnmatch
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def checked(project, path):
    require(project in path.absolute().parents, f'Cleanup path escapes project: {path}')
    for parent in path.parents:
        if parent == project:
            break
        require(not parent.is_symlink(), f'Symlink cleanup parent: {parent}')
    require(not any(part == '.git' for part in path.relative_to(project).parts), f'Git metadata in cleanup target: {path}')
    return path


def tracked_files(project):
    if not shutil.which('git'):
        require(not (project / '.git').exists(), 'Git is required to protect tracked files')
        return set()
    probe = subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if probe.returncode:
        require(not (project / '.git').exists(), f'Cannot inspect Git repository: {project}')
        return set()
    result = subprocess.run(['git', '-C', str(project), 'ls-files', '-z', '--cached', '--', '.'], capture_output=True, check=True)
    return {project / os.fsdecode(name) for name in result.stdout.split(b'\0') if name}


def generated_targets(scope, nested_projects=()):
    """Find generated data without following links or entering Git metadata."""
    cache_dirs = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                  '.cache', '.hypothesis', 'shader_cache', 'shader-cache',
                  '.shader_cache', '.shader-cache'}
    runtime_dirs = {'logs', 'log', 'cache', 'runtime', 'captures', 'profiles',
                    'crash-dumps', 'crash_dumps'}
    file_patterns = ('*.pyc', '*.pyo', '*.o', '*.obj', '*.gch', '*.pch',
                     '*.gcda', '*.gcno', '*.gcov', '*.profraw', '*.profdata',
                     '*.log', '*.log.*', 'core', 'core.[0-9]*', '*.dmp',
                     '.ninja_log', '.ninja_deps', 'imgui.ini')
    for current, dirs, files in os.walk(scope, followlinks=False):
        current = Path(current)
        if current != scope and (current / '.git').exists():
            dirs[:] = []
            continue
        for name in list(dirs):
            entry = current / name
            if name == '.git' or entry in nested_projects:
                dirs.remove(name)
            elif name in cache_dirs or (current == scope and name in runtime_dirs):
                yield scope, entry
                dirs.remove(name)
            elif entry.is_symlink():
                # Never traverse a directory symlink to look for artifacts.
                dirs.remove(name)
        for name in files:
            if name != '.git' and any(fnmatch.fnmatchcase(name, pat) for pat in file_patterns):
                yield scope, current / name


def main():
    root = Path(sys.argv[1])
    parser = argparse.ArgumentParser(description='Remove generated build trees and installed outputs from all three projects; preserve sources, assets and Git metadata.')
    parser.add_argument('--dry-run', action='store_true', help='List generated files without deleting them')
    parser.add_argument('--artifact', action='append', default=[], metavar='PATH',
                        help='Also clean a custom generated file/directory inside this workspace (repeatable)')
    args = parser.parse_args(sys.argv[2:])
    projects = [root / name for name in ('3d-engine', 'jsd-engine-editor', 'red-wasteland')]
    for p in projects:
        require(p.is_dir() and not p.is_symlink(), f'Missing or symlink project folder: {p}')
        require(not (p / '.jsd-update-journal.json').exists(), f'Incomplete update: {p}')
    sys.path.insert(0, str(projects[0] / 'tools'))
    guard = importlib.import_module('project_guard')
    driver = importlib.import_module('build_project')
    with contextlib.ExitStack() as locks:
        lock_path = root / '.jsd-full-installer.lock'
        require(not lock_path.is_symlink(), 'Installer lock cannot be a symlink')
        handle = locks.enter_context(lock_path.open('a'))
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for p in sorted(projects):
            locks.enter_context(guard.operation_lock(p))
        targets = list(generated_targets(root, projects))
        for p in projects:
            targets.extend(generated_targets(p))
        for value in args.artifact:
            path = Path(value)
            path = Path(os.path.abspath(path if path.is_absolute() else root / path))
            scope = next((p for p in projects if p in path.parents), root)
            checked(scope, path)
            require(path not in projects, f'Cannot clean a whole source project: {path}')
            targets.append((scope, path))
        # Use the same project identities, version and output ownership guards as builds.
        for p, pid in zip(projects[:2], ('jsd.engine', 'jsd.editor')):
            meta = guard.source(p, pid)
            env = dict(os.environ)
            for key in ('DESTDIR', 'JSD_BUILD_DIR', 'JSD_INSTALL_PREFIX', 'JSD_EDITOR_BUILD_DIR', 'JSD_EDITOR_INSTALL_PREFIX'):
                env.pop(key, None)
            plan = driver.preflight(p, pid, meta['version'], ['clean'], env)
            targets.extend((p, x) for group in plan[4] for x in group)
        for p in projects:
            for pattern in ('build', 'build-*', 'cmake-build-*', 'prebuild', 'prebuilds', 'prebuilt', 'prebuilts'):
                for build in p.glob(pattern):
                    checked(p, build)
                    require(not build.is_symlink(), f'Build directory is a symlink: {build}')
                    if not build.is_dir():
                        continue
                    for current, dirs, files in os.walk(build, followlinks=False):
                        require('.git' not in dirs and '.git' not in files and 'JSD_PROJECT.json' not in files,
                                f'Build directory contains a source repository/project: {current}')
                        if 'CMakeCache.txt' in files:
                            values = guard.cache_values(Path(current))
                            require(Path(values.get('CMAKE_HOME_DIRECTORY', '')).resolve() == p.resolve()
                                    or build.resolve() in Path(values.get('CMAKE_HOME_DIRECTORY', '')).resolve().parents,
                                    f'Foreign CMake build cache: {current}')
                    # Game CMake installs may create local runtime outputs outside build/.
                    if p == projects[2]:
                        for manifest in build.rglob('install_manifest*.txt'):
                            require(not manifest.is_symlink(), f'Symlink install manifest: {manifest}')
                            for line in manifest.read_text().splitlines():
                                output = Path(line)
                                require(output.is_absolute(), f'Invalid installed output: {line}')
                                checked(p, output)
                                require(output.relative_to(p).parts[0] in ('bin', 'lib', 'lib64', 'share'),
                                        f'Game install manifest names a non-runtime path: {output}')
                                targets.append((p, output))
                    targets.append((p, build))
        # Cover in-source CMake builds and nonstandard nested build directories.
        for p in projects:
            for current, dirs, files in os.walk(p, followlinks=False):
                current = Path(current)
                dirs[:] = [name for name in dirs if name != '.git'
                           and not (current / name).is_symlink()]
                if current != p and (current / '.git').exists():
                    dirs[:] = []
                    continue
                if 'CMakeCache.txt' not in files:
                    continue
                checked(p, current / 'CMakeCache.txt')
                require(not (current / 'CMakeCache.txt').is_symlink(),
                        f'Symlink CMake cache: {current}')
                values = guard.cache_values(current)
                home = Path(values.get('CMAKE_HOME_DIRECTORY', '')).resolve()
                require(home == p.resolve() or p.resolve() in home.parents,
                        f'Foreign CMake build cache: {current}')
                for name in ('CMakeCache.txt', 'CMakeFiles', 'cmake_install.cmake',
                             'Makefile', 'build.ninja', 'rules.ninja',
                             'CTestTestfile.cmake', 'Testing', 'compile_commands.json'):
                    targets.append((p, current / name))
                for manifest in current.glob('install_manifest*.txt'):
                    require(not manifest.is_symlink(), f'Symlink install manifest: {manifest}')
                    for line in manifest.read_text().splitlines():
                        output = Path(line)
                        require(output.is_absolute(), f'Invalid installed output: {line}')
                        checked(p, output)
                        if output.relative_to(p).parts[0] == 'include' and p != projects[2]:
                            continue
                        require(output.relative_to(p).parts[0] in ('bin', 'lib', 'lib64', 'share'),
                                f'Install manifest names a non-runtime path: {output}')
                        targets.append((p, output))
                    targets.append((p, manifest))
        # Expand the complete plan before deleting anything, keeping Git-tracked files.
        protected = {p: tracked_files(p) for p in [root, *projects]}
        # The cleanup script, installer and project descriptors are never artifacts.
        for scope in protected:
            protected[scope].update({root / 'cleanall.sh', root / 'installer.py'})
        leaves = set()
        directories = set()
        for p, path in targets:
            checked(p, path)
            if path.is_symlink() or path.is_file():
                if path not in protected[p]:
                    leaves.add(path)
            elif path.is_dir():
                for current, dirs, files in os.walk(path, followlinks=False):
                    current = Path(current)
                    require('.git' not in dirs and '.git' not in files and 'JSD_PROJECT.json' not in files,
                            f'Cleanup target contains source/Git metadata: {current}')
                    directories.add(current)
                    for name in files + [d for d in dirs if (current / d).is_symlink()]:
                        entry = checked(p, current / name)
                        if entry not in protected[p]:
                            leaves.add(entry)
        # Preview directories too, including empty cache folders.
        for path in sorted(directories):
            print('Cleanup directory ' + str(path.relative_to(root)))
        # Preview exactly the collected leaves; lstat never follows symlink targets.
        total_bytes = sum(path.lstat().st_size for path in leaves)
        size = float(total_bytes)
        unit = 'B'
        for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
            if size < 1024 or unit == 'PiB':
                break
            size /= 1024
        for path in sorted(leaves):
            print('Would remove ' + str(path.relative_to(root)))
        print(f'Cleanup summary: {len(leaves)} artifacts to be deleted; '
              f'total size: {size:.2f} {unit} ({total_bytes} bytes); '
              f'{len(directories)} directories considered (only empty directories removed).')
        if args.dry_run or not leaves and not directories:
            return
        print('Really clean [y/n]: ', end='', flush=True)
        answer = sys.stdin.readline().strip().lower()
        if answer != 'y':
            print('Cleanup cancelled. No artifacts deleted.')
            return
        for path in sorted(leaves):
            print('Removing ' + str(path.relative_to(root)))
            path.unlink(missing_ok=True)
        if not args.dry_run:
            for path in sorted(directories, key=lambda x: len(x.parts), reverse=True):
                # Keep nonempty directories containing tracked source files.
                if not any(path.iterdir()):
                    path.rmdir()
        print(f"{'Would remove' if args.dry_run else 'Removed'} {len(leaves)} generated files from the workspace.")


try:
    main()
except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
    print(f'Cleanup stopped: {error}', file=sys.stderr)
    sys.exit(2)
PY
