import os
import datetime

# --- Коррневая директория ---
BASE_DIR = os.path.dirname(__file__)
PARENT_DIR = os.path.dirname(BASE_DIR)

# --- Пути к данным ---

DATA_DIR = os.path.join(PARENT_DIR, "SDForge data")
DB_FILE = os.path.join(DATA_DIR, "sdforge.db")
CORE_DIR = os.path.join(BASE_DIR, "core")
SHADERS_DIR = os.path.join(CORE_DIR, "shaders")
SDF_FRAG_PATH = os.path.join(SHADERS_DIR, "sdf_frag_high.frag")
SDF_VERT_PATH = os.path.join(SHADERS_DIR, "sdf_vert.glsl")
SDF_FRAG_FAST_PATH = os.path.join(SHADERS_DIR, "sdf_frag_fast_outline.glsl")
SDF_FRAG_SIMPLE_PATH = os.path.join(SHADERS_DIR, "sdf_frag_simple.glsl") # Пока не используется, может пригодится в будущем при рендеринге физики мягких тел

# --- ЛОГИРОВАНИЕ ---

DEBUG_LOG_PATH = os.path.join(DATA_DIR, "debug.log")
DEBUG_LOG_ENABLED = False

def log_debug(message):
    if not DEBUG_LOG_ENABLED:
        return
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}: {message}\n")
    except:
        pass



DEFAULT_TEXTURE_MAX_SIZE: int = 1024