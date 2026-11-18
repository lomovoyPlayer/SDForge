import sys
import platform
import traceback
from pathlib import Path

from .config import log_debug
from . import localization


def log_header(plugin_dir, piplib_dir):
    log_debug(localization.get_text("init_diagnostics.log_header.begin.log_debug"))
    log_debug(localization.get_text("init_diagnostics.log_header.plugin_dir.log_debug", plugin_dir=plugin_dir))
    log_debug(localization.get_text("init_diagnostics.log_header.piplib_dir.log_debug", piplib_dir=piplib_dir))
    log_debug(localization.get_text("init_diagnostics.log_header.cwd.log_debug", cwd=Path.cwd()))
    log_debug(localization.get_text("init_diagnostics.log_header.executable.log_debug", executable=sys.executable))
    log_debug(localization.get_text("init_diagnostics.log_header.python_version.log_debug", python_version=sys.version))
    log_debug(localization.get_text("init_diagnostics.log_header.platform.log_debug", platform=platform.platform()))
    log_debug(localization.get_text("init_diagnostics.log_header.architecture.log_debug", architecture=platform.architecture()))
    log_debug(localization.get_text("init_diagnostics.log_header.maxsize.log_debug", maxsize=sys.maxsize))


def log_sys_path():
    for i, p in enumerate(sys.path):
        log_debug(localization.get_text("init_diagnostics.log_sys_path.entry.log_debug", index=i, path=p))


def log_piplib_contents(piplib_dir):
    if not piplib_dir.exists():
        log_debug(localization.get_text("init_diagnostics.log_piplib_contents.dir_missing.log_debug"))
        return

    try:
        entries = sorted(piplib_dir.iterdir(), key=lambda x: x.name.lower())
        log_debug(localization.get_text("init_diagnostics.log_piplib_contents.entries_count.log_debug", count=len(entries)))
        for e in entries:
            key = (
                "init_diagnostics.log_piplib_contents.entry_dir.log_debug"
                if e.is_dir()
                else "init_diagnostics.log_piplib_contents.entry_file.log_debug"
            )
            log_debug(localization.get_text(key, name=e.name))
    except Exception as e:
        log_debug(localization.get_text("init_diagnostics.log_piplib_contents.list_failed.error.log_debug", error=str(e)))

    for name in ("moderngl", "_moderngl.py", "glcontext",
                 "moderngl-5.12.0.dist-info", "glcontext-3.0.0.dist-info"):
        p = piplib_dir / name
        log_debug(localization.get_text("init_diagnostics.log_piplib_contents.path_exists.log_debug", name=name, exists=p.exists(), path=p))


def log_imports():
    log_debug(localization.get_text("init_diagnostics.log_imports.begin.log_debug"))

    try:
        import moderngl
        log_debug(localization.get_text("init_diagnostics.log_imports.moderngl_from.log_debug", file=getattr(moderngl, '__file__', 'no_file')))
        log_debug(localization.get_text("init_diagnostics.log_imports.moderngl_version.log_debug", version=getattr(moderngl, '__version__', 'no_version')))
        log_debug(localization.get_text("init_diagnostics.log_imports.moderngl_has_create_context.log_debug", has_create_context=hasattr(moderngl, 'create_context')))
    except Exception as e:
        log_debug(localization.get_text("init_diagnostics.log_imports.moderngl_import_failed.error.log_debug", error=str(e)))
        log_debug(traceback.format_exc())
        raise

    try:
        import _moderngl
        log_debug(localization.get_text("init_diagnostics.log_imports.private_moderngl_from.log_debug", file=getattr(_moderngl, '__file__', 'no_file')))
    except Exception as e:
        log_debug(localization.get_text("init_diagnostics.log_imports.private_moderngl_import_failed.error.log_debug", error=str(e)))
        log_debug(traceback.format_exc())

    try:
        import glcontext
        log_debug(localization.get_text("init_diagnostics.log_imports.glcontext_from.log_debug", file=getattr(glcontext, '__file__', 'no_file')))
        log_debug(localization.get_text("init_diagnostics.log_imports.glcontext_dir_sample.log_debug", sample=dir(glcontext)[:20]))
    except Exception as e:
        log_debug(localization.get_text("init_diagnostics.log_imports.glcontext_import_failed.error.log_debug", error=str(e)))
        log_debug(traceback.format_exc())
        raise

    try:
        log_debug(localization.get_text("init_diagnostics.log_imports.create_context_try.log_debug"))
        ctx = moderngl.create_context(standalone=True)
        log_debug(localization.get_text("init_diagnostics.log_imports.create_context_done.log_debug", version_code=ctx.version_code))
        log_debug(localization.get_text("init_diagnostics.log_imports.create_context_info.log_debug", info=getattr(ctx, 'info', {})))
        try:
            ctx.release()
            log_debug(localization.get_text("init_diagnostics.log_imports.create_context_released.log_debug"))
        except Exception as e:
            log_debug(localization.get_text("init_diagnostics.log_imports.create_context_release_failed.error.log_debug", error=str(e)))
    except Exception as e:
        log_debug(localization.get_text("init_diagnostics.log_imports.create_context_failed.error.log_debug", error=str(e)))
        log_debug(traceback.format_exc())

    log_debug(localization.get_text("init_diagnostics.log_imports.end.log_debug"))


def run_all(plugin_dir, piplib_dir):
    log_header(plugin_dir, piplib_dir)
    log_sys_path()
    log_piplib_contents(piplib_dir)
    log_imports()