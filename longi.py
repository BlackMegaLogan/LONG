import re
import sys
import os
import random
import time
import sys
import select
import termios
import tty
import atexit
try:
    import msvcrt
except ImportError:
    msvcrt = None

# ------------------------------
# Global State
# ------------------------------
variables = {}
functions = {}
labels = {}
program_lines = []
current_line = 0
current_fg = None
current_bg = None
fs_state = None
repeat_ms = None
last_input = ""
last_raw_input = ""
posix_raw_enabled = False
posix_tty_state = None

def ensure_posix_raw_mode():
    global posix_raw_enabled, posix_tty_state
    if posix_raw_enabled:
        return True
    if not sys.stdin.isatty():
        return False
    fd = sys.stdin.fileno()
    posix_tty_state = termios.tcgetattr(fd)
    tty.setraw(fd)
    # Re-enable output post-processing so '\n' becomes CRLF and cursor stays aligned.
    raw_attrs = termios.tcgetattr(fd)
    raw_attrs[1] |= termios.OPOST | termios.ONLCR
    termios.tcsetattr(fd, termios.TCSADRAIN, raw_attrs)
    posix_raw_enabled = True
    def _restore():
        try:
            if posix_tty_state is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, posix_tty_state)
        except Exception:
            pass
    atexit.register(_restore)
    return True

# ------------------------------
# Utilities
# ------------------------------

def get_repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def fs_db_path():
    return os.path.join(get_repo_root(), "build", "lush_fs.json")

def fs_default_state():
    return {
        "block_size": 4096,
        "next_block_id": 1,
        "blocks": {},
        "files": {},
    }

def fs_load():
    global fs_state
    if fs_state is not None:
        return
    path = fs_db_path()
    if os.path.exists(path):
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                fs_state = json.load(f)
        except Exception:
            fs_state = fs_default_state()
    else:
        fs_state = fs_default_state()

def fs_save():
    if fs_state is None:
        return
    try:
        import json
        build_dir = os.path.dirname(fs_db_path())
        ensure_dir(build_dir)
        with open(fs_db_path(), "w", encoding="utf-8") as f:
            json.dump(fs_state, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[ERROR] Failed to save FS state: {e}")

def fs_normalize_path(path):
    path = (path or "").strip()
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    while "//" in path:
        path = path.replace("//", "/")
    return path

def fs_parse_meta(meta_text):
    meta = {}
    if not meta_text:
        return meta
    text = meta_text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    for token in re.split(r"[,\s]+", text.strip()):
        if not token or "=" not in token:
            continue
        key, val = token.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if key in ("role", "ui", "run", "backup"):
            meta[key] = val
    return meta

def fs_alloc_block():
    fs_load()
    block_id = fs_state["next_block_id"]
    fs_state["next_block_id"] = block_id + 1
    fs_state["blocks"][str(block_id)] = ""
    return str(block_id)

def fs_write_block(block_id, content):
    fs_load()
    block_id = str(block_id)
    if block_id not in fs_state["blocks"]:
        print(f"[ERROR] Block '{block_id}' not allocated.")
        return False
    block_size = int(fs_state.get("block_size", 4096))
    fs_state["blocks"][block_id] = (content or "")[:block_size]
    return True

def fs_read_block(block_id):
    fs_load()
    block_id = str(block_id)
    if block_id not in fs_state["blocks"]:
        print(f"[ERROR] Block '{block_id}' not allocated.")
        return ""
    return fs_state["blocks"].get(block_id, "")

def fs_get_file(path):
    fs_load()
    return fs_state["files"].get(path)

def fs_create_file(path, meta=None):
    fs_load()
    now = time.time()
    defaults = {
        "role": "doc",
        "ui": "none",
        "run": "fg",
        "backup": "versioned",
    }
    if meta:
        defaults.update(meta)
    fs_state["files"][path] = {
        "blocks": [],
        "size": 0,
        "role": defaults["role"],
        "ui": defaults["ui"],
        "run": defaults["run"],
        "backup": defaults["backup"],
        "versions": [],
        "created": now,
        "modified": now,
    }

def fs_write_file(path, content):
    fs_load()
    file_entry = fs_get_file(path)
    if file_entry is None:
        fs_create_file(path)
        file_entry = fs_get_file(path)
    if file_entry["blocks"]:
        file_entry["versions"].append({
            "blocks": list(file_entry["blocks"]),
            "size": file_entry["size"],
            "ts": time.time(),
        })
    block_size = int(fs_state.get("block_size", 4096))
    content = content or ""
    blocks = []
    if content:
        for i in range(0, len(content), block_size):
            chunk = content[i:i + block_size]
            block_id = fs_alloc_block()
            fs_write_block(block_id, chunk)
            blocks.append(block_id)
    file_entry["blocks"] = blocks
    file_entry["size"] = len(content)
    file_entry["modified"] = time.time()

def fs_read_file(path):
    fs_load()
    file_entry = fs_get_file(path)
    if file_entry is None:
        print(f"[ERROR] File '{path}' not found.")
        return ""
    content = []
    for block_id in file_entry.get("blocks", []):
        content.append(fs_read_block(block_id))
    return "".join(content)

def fs_list_dir(path):
    fs_load()
    prefix = fs_normalize_path(path)
    if not prefix.endswith("/"):
        prefix += "/"
    entries = set()
    for file_path in fs_state["files"].keys():
        if not file_path.startswith(prefix):
            continue
        remainder = file_path[len(prefix):]
        if not remainder:
            continue
        if "/" in remainder:
            entries.add(remainder.split("/", 1)[0] + "/")
        else:
            entries.add(remainder)
    return sorted(entries)

def fs_set_role(path, role):
    fs_load()
    file_entry = fs_get_file(path)
    if file_entry is None:
        print(f"[ERROR] File '{path}' not found.")
        return False
    file_entry["role"] = role
    file_entry["modified"] = time.time()
    return True

def fs_tran(path):
    fs_load()
    file_entry = fs_get_file(path)
    if file_entry is None:
        print(f"[ERROR] File '{path}' not found.")
        return False
    file_entry["role"] = "Tran"
    file_entry["run"] = "bg"
    file_entry["modified"] = time.time()
    return True

def strip_inline_comment(line: str) -> str:
    """Remove inline comments (// or #) that occur outside quotes.
    Supports single ('') and double ("") quoted strings. No escape handling.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        # Toggle quote states
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if not in_single and not in_double:
            # Check for //
            if ch == '/' and i + 1 < len(line) and line[i+1] == '/':
                return line[:i].rstrip()
            # Check for #
            if ch == '#':
                return line[:i].rstrip()
        i += 1
    return line.rstrip()

def substitute_variables(text):
    """Replace <`VAR`> with its value."""
    matches = re.findall(r"<`(.*?)`>", text)
    for var in matches:
        value = variables.get(var, f"<UNDEFINED:{var}>")
        text = text.replace(f"<`{var}`>", value)
    return text

def parse_value(value):
    """Handles quoted strings or variable references."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value.strip('"')
    return variables.get(value, value)

def parse_token_value(token):
    """Parse a token that may be quoted, contain <`VAR`>, or be a variable name."""
    if token is None:
        return ""
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return substitute_variables(token.strip('"'))
    token = substitute_variables(token)
    return variables.get(token, token)

def parse_path_token(token):
    """Parse a path token that may be quoted and include variable substitutions."""
    return parse_token_value(token)

def eval_math(expr):
    """Safely evaluate a math expression containing numbers and operators."""
    import ast
    expr = expr.strip()
    expr = substitute_variables(expr)
    if expr.startswith('"') and expr.endswith('"'):
        expr = expr[1:-1]

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.UAdd, ast.USub,
        ast.Load,
    )

    def _eval(node):
        if not isinstance(node, allowed_nodes):
            raise ValueError("Invalid math expression")
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants allowed")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Invalid unary operator")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right
            raise ValueError("Invalid binary operator")
        raise ValueError("Invalid math expression")

    tree = ast.parse(expr, mode="eval")
    return _eval(tree)

def ansi_prefix():
    """Build ANSI prefix based on current colors."""
    codes = []
    if current_fg:
        codes.append(current_fg)
    if current_bg:
        codes.append(current_bg)
    if not codes:
        return ""
    return f"\033[{';'.join(codes)}m"

def ansi_reset():
    return "\033[0m"

def set_color(tag, value):
    global current_fg, current_bg
    color_map = {
        "BLACK": "30",
        "RED": "31",
        "GREEN": "32",
        "YELLOW": "33",
        "BLUE": "34",
        "MAGENTA": "35",
        "CYAN": "36",
        "WHITE": "37",
        "BRIGHTBLACK": "90",
        "BRIGHTRED": "91",
        "BRIGHTGREEN": "92",
        "BRIGHTYELLOW": "93",
        "BRIGHTBLUE": "94",
        "BRIGHTMAGENTA": "95",
        "BRIGHTCYAN": "96",
        "BRIGHTWHITE": "97",
    }
    bg_map = {k: str(int(v) + 10) for k, v in color_map.items() if v.isdigit()}
    key = value.strip().upper()
    if tag == "FG":
        if key in color_map:
            current_fg = color_map[key]
        else:
            print(f"[WARN] Unknown FG color '{value}'")
    elif tag == "BG":
        if key in bg_map:
            current_bg = bg_map[key]
        else:
            print(f"[WARN] Unknown BG color '{value}'")

def reset_color():
    global current_fg, current_bg
    current_fg = None
    current_bg = None

def clear_screen():
    """Clear screen and move cursor to home position."""
    print("\033[2J\033[H", end="")

def set_cursor(row, col):
    """Move cursor to 1-based row/col position."""
    print(f"\033[{row};{col}H", end="")

def tick_timer(ms):
    """Pause execution for the given milliseconds."""
    try:
        delay = float(ms) / 1000.0
    except ValueError:
        print("[ERROR] TickTimer requires a numeric millisecond value")
        return
    if delay < 0:
        return
    time.sleep(delay)

def tick_timer_seconds(seconds):
    try:
        delay = float(seconds)
    except ValueError:
        print("[ERROR] Time[SEC] requires a numeric second value")
        return
    if delay < 0:
        return
    time.sleep(delay)

def tick_timer_minutes(minutes):
    try:
        delay = float(minutes) * 60.0
    except ValueError:
        print("[ERROR] Time[MIN] requires a numeric minute value")
        return
    if delay < 0:
        return
    time.sleep(delay)

def draw_box(width, height, ch):
    try:
        w = int(width)
        h = int(height)
    except ValueError:
        print("[ERROR] DrawBox width/height must be integers")
        return
    if w <= 0 or h <= 0:
        return
    if len(ch) == 0:
        ch = "#"
    ch = ch[0]
    if w == 1:
        for _ in range(h):
            display_to_shell(ch)
        return
    if h == 1:
        display_to_shell(ch * w)
        return
    top = ch * w
    mid = ch + (" " * (w - 2)) + ch
    display_to_shell(top)
    for _ in range(h - 2):
        display_to_shell(mid)
    display_to_shell(top)

def normalize_input(text):
    """Lowercase and collapse whitespace for command parsing."""
    text = text.strip().lower()
    if not text:
        return ""
    return " ".join(text.split())

def set_word_vars(text):
    """Populate WORD1/WORD2/WORD3 and WORDCOUNT from normalized input."""
    words = text.split() if text else []
    variables["WORDCOUNT"] = str(len(words))
    variables["WORD1"] = words[0] if len(words) > 0 else ""
    variables["WORD2"] = words[1] if len(words) > 1 else ""
    variables["WORD3"] = words[2] if len(words) > 2 else ""

def send_to_hardware(text):
    """Simulate writing to hardware by appending to a hardware_output.log file next to the script.
    This keeps real hardware access safe while giving a place to inspect DIRECT output.
    """
    try:
        build_dir = os.path.join(get_repo_root(), "build")
        ensure_dir(build_dir)
        log_path = os.path.join(build_dir, "hardware_output.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        print(f"[ERROR] Failed to write to hardware log: {e}")


def display_to_shell(text):
    """Send text to the interactive shell (stdout)."""
    prefix = ansi_prefix()
    if prefix:
        print(f"{prefix}{text}{ansi_reset()}")
    else:
        print(text)
    
# ------------------------------
# Instruction Handlers
# ------------------------------
def handle_set(line):
    # Example: Set[USER]= "Logan"
    match = re.match(r"Set\[(.*?)\]\s*=\s*(.*)", line)
    if not match:
        print(f"[ERROR] Invalid Set syntax: {line}")
        return

    var_name = match.group(1)
    raw_value = match.group(2).strip()

    # Math evaluation: Set[X]=Math(1+2*3)
    if raw_value.startswith("Math(") and raw_value.endswith(")"):
        expr = raw_value[5:-1]
        try:
            result = eval_math(expr)
            variables[var_name] = str(result)
        except Exception as e:
            print(f"[ERROR] Math evaluation failed: {e}")
        return

    # Random choice: Set[VAR]=Random["a","b","c"]
    if raw_value.startswith("Random[") and raw_value.endswith("]"):
        inner = raw_value[len("Random["):-1].strip()
        choices = re.findall(r"\"([^\"]*)\"", inner)
        if not choices:
            print("[ERROR] Random requires quoted string choices, e.g. Random[\"a\",\"b\"]")
            return
        variables[var_name] = random.choice(choices)
        return

    # ReadFile: Set[VAR]=ReadFile["path"]
    if raw_value.startswith("ReadFile[") and raw_value.endswith("]"):
        path_token = raw_value[len("ReadFile["):-1]
        file_path = parse_path_token(path_token)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                variables[var_name] = f.read()
        except Exception as e:
            print(f"[ERROR] ReadFile failed: {e}")
        return

    # FS[Read]: Set[VAR]=FS[Read]["path"]
    fs_read_match = re.match(r"FS\[Read\]\[(.*?)\]\s*$", raw_value)
    if fs_read_match:
        file_path = fs_normalize_path(parse_path_token(fs_read_match.group(1)))
        variables[var_name] = fs_read_file(file_path)
        variables["LASTREADPATH"] = file_path
        variables["LASTREAD"] = variables[var_name]
        variables["LASTREADSIZE"] = str(len(variables[var_name]))
        fs_save()
        return

    # FS[List]: Set[VAR]=FS[List]["path"]
    fs_list_match = re.match(r"FS\[List\](?:\[(.*?)\])?\s*$", raw_value)
    if fs_list_match:
        list_path = fs_normalize_path(parse_path_token(fs_list_match.group(1)))
        entries = fs_list_dir(list_path)
        variables[var_name] = ",".join(entries)
        variables["LASTLISTPATH"] = list_path
        variables["LASTLIST"] = variables[var_name]
        variables["LASTLISTCOUNT"] = str(len(entries))
        fs_save()
        return

    # Block[Alloc]: Set[VAR]=Block[Alloc]
    if re.match(r"Block\[Alloc\]\s*$", raw_value):
        block_id = fs_alloc_block()
        variables[var_name] = block_id
        variables["LASTBLOCK"] = block_id
        fs_save()
        return

    # Block[Read]: Set[VAR]=Block[Read][id]
    block_read_match = re.match(r"Block\[Read\]\[(.*?)\]\s*$", raw_value)
    if block_read_match:
        block_id = parse_token_value(block_read_match.group(1))
        variables[var_name] = fs_read_block(block_id)
        variables["LASTBLOCK"] = str(block_id)
        fs_save()
        return

    # Support DisplayText(TAG)=... where TAG can be DIRECT or SHELL (case-insensitive)
    dt_match = re.match(r"DisplayText\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", raw_value)
    if dt_match:
        tag = dt_match.group(1).strip().upper()
        value = dt_match.group(3).strip()
        value = substitute_variables(value)
        # Route according to tag
        if tag == "DIRECT":
            # do not print to shell; write to simulated hardware
            send_to_hardware(value)
            # store the raw value in variable as well, in case code expects it
            variables[var_name] = value
        elif tag == "SHELL":
            display_to_shell(value)
            variables[var_name] = value
        else:
            # Unknown tag: default to shell and warn
            print(f"[WARN] Unknown DisplayText tag '{tag}', defaulting to SHELL")
            display_to_shell(value)
            variables[var_name] = value
        return

    # Support DisplayTextRaw(TAG)=... where TAG can be DIRECT or SHELL (case-insensitive)
    dtr_match = re.match(r"DisplayTextRaw\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", raw_value)
    if dtr_match:
        tag = dtr_match.group(1).strip().upper()
        value = dtr_match.group(3).strip()
        value = substitute_variables(value)
        if tag == "DIRECT":
            send_to_hardware(value, add_newline=False)
            variables[var_name] = value
        elif tag == "SHELL":
            prefix = ansi_prefix()
            if prefix:
                print(f"{prefix}{value}{ansi_reset()}", end="")
            else:
                print(value, end="")
            variables[var_name] = value
        else:
            print(f"[WARN] Unknown DisplayTextRaw tag '{tag}', defaulting to SHELL")
            print(value, end="")
            variables[var_name] = value
        return

    # Fallback: normal value or variable reference
    if raw_value.startswith('"') and raw_value.endswith('"'):
        variables[var_name] = substitute_variables(raw_value.strip('"'))
    else:
        variables[var_name] = parse_value(raw_value)


def handle_display(line):
    # line format: DisplayText(TAG)=<content>
    match = re.match(r"DisplayText\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", line)
    if not match:
        print(f"[ERROR] Invalid DisplayText syntax (must be quoted): {line}")
        return

    tag = match.group(1).strip().upper()
    content = match.group(3).strip()
    content = substitute_variables(content)

    if tag == "SHELL":
        display_to_shell(content)
    elif tag == "DIRECT":
        # Do not print to shell; write to simulated hardware
        send_to_hardware(content)
    else:
        print(f"[WARN] Unknown DisplayText tag '{tag}', defaulting to SHELL")
        display_to_shell(content)

def handle_display_raw(line):
    # line format: DisplayTextRaw(TAG)=<content>
    match = re.match(r"DisplayTextRaw\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", line)
    if not match:
        print(f"[ERROR] Invalid DisplayTextRaw syntax (must be quoted): {line}")
        return
    tag = match.group(1).strip().upper()
    content = match.group(3).strip()
    content = substitute_variables(content)

    if tag == "SHELL":
        prefix = ansi_prefix()
        if prefix:
            print(f"{prefix}{content}{ansi_reset()}", end="")
        else:
            print(content, end="")
    elif tag == "DIRECT":
        send_to_hardware(content, add_newline=False)
    else:
        print(f"[WARN] Unknown DisplayTextRaw tag '{tag}', defaulting to SHELL")
        print(content, end="")

def handle_fs_command(line):
    create_match = re.match(r"FS\[Create\]\[(.*?)\]\s*(?:=\s*(.*))?$", line)
    if create_match:
        file_path = fs_normalize_path(parse_path_token(create_match.group(1)))
        meta_raw = parse_token_value(create_match.group(2)) if create_match.group(2) else ""
        meta = fs_parse_meta(meta_raw)
        if fs_get_file(file_path) is not None:
            print(f"[ERROR] File '{file_path}' already exists.")
            return
        fs_create_file(file_path, meta)
        variables["LASTCREATEPATH"] = file_path
        fs_save()
        return True

    read_match = re.match(r"FS\[Read\]\[(.*?)\]\s*$", line)
    if read_match:
        file_path = fs_normalize_path(parse_path_token(read_match.group(1)))
        content = fs_read_file(file_path)
        variables["LASTREADPATH"] = file_path
        variables["LASTREAD"] = content
        variables["LASTREADSIZE"] = str(len(content))
        fs_save()
        return True

    write_match = re.match(r"FS\[Write\]\[(.*?)\]\s*=\s*(.*)$", line)
    if write_match:
        file_path = fs_normalize_path(parse_path_token(write_match.group(1)))
        content = parse_token_value(write_match.group(2))
        fs_write_file(file_path, content)
        variables["LASTWRITEPATH"] = file_path
        variables["LASTWRITESIZE"] = str(len(content))
        fs_save()
        return True

    list_match = re.match(r"FS\[List\](?:\[(.*?)\])?\s*$", line)
    if list_match:
        list_path = fs_normalize_path(parse_path_token(list_match.group(1)))
        entries = fs_list_dir(list_path)
        variables["LASTLISTPATH"] = list_path
        variables["LASTLIST"] = ",".join(entries)
        variables["LASTLISTCOUNT"] = str(len(entries))
        fs_save()
        return True

    role_match = re.match(r"FS\[SetRole\]\[(.*?)\]\s*=\s*(.*)$", line)
    if role_match:
        file_path = fs_normalize_path(parse_path_token(role_match.group(1)))
        role = parse_token_value(role_match.group(2))
        if fs_set_role(file_path, role):
            variables["LASTROLEPATH"] = file_path
            variables["LASTROLE"] = role
            fs_save()
        return True

    tran_match = re.match(r"FS\[Tran\]\[(.*?)\]\s*$", line)
    if tran_match:
        file_path = fs_normalize_path(parse_path_token(tran_match.group(1)))
        if fs_tran(file_path):
            variables["LASTROLEPATH"] = file_path
            variables["LASTROLE"] = "Tran"
            fs_save()
        return True

    return False

def handle_block_command(line):
    alloc_match = re.match(r"Block\[Alloc\]\s*$", line)
    if alloc_match:
        block_id = fs_alloc_block()
        variables["LASTBLOCK"] = block_id
        fs_save()
        return True

    read_match = re.match(r"Block\[Read\]\[(.*?)\]\s*$", line)
    if read_match:
        block_id = parse_token_value(read_match.group(1))
        content = fs_read_block(block_id)
        variables["LASTBLOCK"] = str(block_id)
        variables["LASTBLOCKDATA"] = content
        fs_save()
        return True

    write_match = re.match(r"Block\[Write\]\[(.*?)\]\s*=\s*(.*)$", line)
    if write_match:
        block_id = parse_token_value(write_match.group(1))
        content = parse_token_value(write_match.group(2))
        if fs_write_block(block_id, content):
            variables["LASTBLOCK"] = str(block_id)
            fs_save()
        return True

    return False

def handle_input():
    global last_input, last_raw_input
    user_input = input("> ").strip()
    variables["RAWINPUT"] = user_input
    normalized = normalize_input(user_input)
    variables["INPUT"] = normalized
    set_word_vars(normalized)
    if normalized:
        last_input = normalized
        last_raw_input = user_input
    print(f"[DEBUG] INPUT received: '{user_input}'")  # You can remove this later

def handle_input_instant():
    if msvcrt is None:
        # POSIX: read one char in raw mode
        if not ensure_posix_raw_mode():
            print("[ERROR] INSTANT input requires a TTY on this platform.")
            return
        ch = sys.stdin.read(1)
    else:
        ch = msvcrt.getwch()
    variables["RAWINPUT"] = ch
    normalized = normalize_input(ch)
    variables["INPUT"] = normalized
    set_word_vars(normalized)
    if normalized:
        global last_input, last_raw_input
        last_input = normalized
        last_raw_input = ch

def handle_input_noblock():
    global last_input, last_raw_input
    ch = ""
    if msvcrt is None:
        if not ensure_posix_raw_mode():
            return
        rlist, _, _ = select.select([sys.stdin], [], [], 0)
        if rlist:
            ch = sys.stdin.read(1)
    else:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
    if ch:
        variables["RAWINPUT"] = ch
        normalized = normalize_input(ch)
        variables["INPUT"] = normalized
        set_word_vars(normalized)
        if normalized:
            last_input = normalized
            last_raw_input = ch
    else:
        variables["RAWINPUT"] = ""
        variables["INPUT"] = ""
        set_word_vars("")

def handle_every(ms_value):
    global repeat_ms, last_input, last_raw_input
    try:
        repeat_ms = int(ms_value)
    except ValueError:
        print("[ERROR] Every[MS] requires a numeric millisecond value")
        return
    if repeat_ms < 0:
        return
    time.sleep(repeat_ms / 1000.0)
    # Repeat last input if nothing new was provided
    if variables.get("INPUT", "") == "" and last_input:
        variables["INPUT"] = last_input
        variables["RAWINPUT"] = last_raw_input
        set_word_vars(last_input)

IF_OP_RE = re.compile(r"If\[(.*?)\]\s*(>=|<=|=|>|<)\s*(.+)$")

def parse_uint_like_vm(value):
    s = str(value).strip()
    acc = 0
    for ch in s:
        if ch < '0' or ch > '9':
            break
        acc = acc * 10 + (ord(ch) - ord('0'))
    return acc

def parse_if_parts(line):
    match = IF_OP_RE.match(line)
    if not match:
        return None
    left = match.group(1).strip()
    op = match.group(2)
    right_raw = match.group(3).strip()
    if (right_raw.startswith('"') and right_raw.endswith('"')) or (right_raw.startswith("'") and right_raw.endswith("'")):
        right = substitute_variables(right_raw[1:-1])
        right_is_var = False
    else:
        right = right_raw
        right_is_var = right in variables
    return left, op, right, right_is_var

def handle_if(line):
    try:
        parsed = parse_if_parts(line)
        if not parsed:
            print(f"[ERROR] Invalid If condition: '{line}'. Expected format: If[VAR] OP VALUE")
            return False
        left, op, right, right_is_var = parsed
        left_val = variables.get(left, "").strip()
        right_val = variables.get(right, "").strip() if right_is_var else right

        if op == "=":
            return left_val == right_val
        if op == "<":
            return parse_uint_like_vm(left_val) < parse_uint_like_vm(right_val)
        if op == "<=":
            return parse_uint_like_vm(left_val) <= parse_uint_like_vm(right_val)
        if op == ">":
            return parse_uint_like_vm(left_val) > parse_uint_like_vm(right_val)
        if op == ">=":
            return parse_uint_like_vm(left_val) >= parse_uint_like_vm(right_val)
        print(f"[ERROR] Unsupported If operator: '{op}'")
        return False

    except Exception as e:
        print(f"[ERROR] Failed to parse If condition: {e}")
        return False

def skip_if_block(start_index):
    """Skip to matching Else or EndIf for the If at start_index.
    Supports nested If/EndIf pairs.
    Returns the index of Else or EndIf (or len(program_lines) if not found).
    """
    depth = 0
    i = start_index + 1
    while i < len(program_lines):
        l = program_lines[i].strip()
        if IF_OP_RE.match(l):
            depth += 1
        elif l == "EndIf":
            if depth == 0:
                return i
            depth -= 1
        elif l == "Else" and depth == 0:
            return i
        i += 1
    return i

def skip_to_endif(start_index):
    """Skip from an Else line to its matching EndIf."""
    depth = 0
    i = start_index + 1
    while i < len(program_lines):
        l = program_lines[i].strip()
        if IF_OP_RE.match(l):
            depth += 1
        elif l == "EndIf":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return i


def handle_loop(start_index):
    global current_line
    loop_lines = []
    i = start_index + 1
    while i < len(program_lines) and not program_lines[i].strip().startswith("EndLoop"):
        loop_lines.append(program_lines[i])
        i += 1
    while True:
        for line in loop_lines:
            execute_line(line)
    current_line = i  # Will never reach this due to infinite loop

def handle_goto(label):
    global current_line
    if label in labels:
        current_line = labels[label]
    else:
        print(f"[ERROR] Label '{label}' not found.")
        sys.exit(1)

def handle_call(func_name):
    global program_lines, current_line
    if func_name not in functions:
        print(f"[ERROR] Function '{func_name}' not found.")
        return
    # Execute function body in its own line context so If/Else skips don't
    # mutate the main program flow.
    saved_program_lines = program_lines
    saved_current_line = current_line
    try:
        program_lines = functions[func_name]
        current_line = 0
        while current_line < len(program_lines):
            line = program_lines[current_line].strip()
            execute_line(line)
            current_line += 1
    finally:
        program_lines = saved_program_lines
        current_line = saved_current_line

# ------------------------------
# Interpreter
# ------------------------------
def execute_line(line):
    global current_line

    line = line.strip()
    # Remove inline comments outside quotes first
    line = strip_inline_comment(line)
    # Treat lines starting with '//' as comments
    if not line or line.startswith("//"):
        return  

    # Structural-only keywords: tolerate optional spaces in the bit declaration
    if line.replace(" ", "") in ("[16BIT]", "startprogram", "endprogram", "startsection", "endsection"):
        return

    if line.startswith("Set["):
        handle_set(line)

    elif line.startswith("DisplayText(DIRECT)=") or line.startswith("DisplayText(SHELL)="):
        handle_display(line)

    elif line.startswith("DisplayTextRaw(DIRECT)=") or line.startswith("DisplayTextRaw(SHELL)="):
        handle_display_raw(line)

    elif line.startswith("FS["):
        if handle_fs_command(line):
            return

    elif line.startswith("Block["):
        if handle_block_command(line):
            return

    elif line.startswith("WriteFile"):
        match = re.match(r"WriteFile(?:\[(.*?)\])?\s*(?:=\s*(.*))?$", line)
        if not match:
            print(f"[ERROR] Invalid WriteFile syntax: {line}")
            return
        path_token = match.group(1)
        rhs = match.group(2)
        if path_token is None and rhs is not None:
            # Allow: WriteFile= "path"
            file_path = parse_path_token(rhs)
            content = ""
        else:
            file_path = parse_path_token(path_token)
            content = parse_token_value(rhs)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[ERROR] WriteFile failed: {e}")

    elif line.startswith("AppendFile"):
        match = re.match(r"AppendFile\[(.*?)\]\s*=\s*(.*)$", line)
        if not match:
            print(f"[ERROR] Invalid AppendFile syntax: {line}")
            return
        file_path = parse_path_token(match.group(1))
        content = parse_token_value(match.group(2))
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[ERROR] AppendFile failed: {e}")

    elif line.startswith("TrackInput[KEYBOARD]"):
        if re.match(r"TrackInput\[\s*KEYBOARD\s*\]\s*=\s*INSTANT\s*$", line, flags=re.IGNORECASE):
            handle_input_instant()
        elif re.match(r"TrackInput\[\s*KEYBOARD\s*\]\s*=\s*NOBLOCK\s*$", line, flags=re.IGNORECASE):
            handle_input_noblock()
        else:
            handle_input()
    elif line.startswith("Every[MS]"):
        match = re.match(r"Every\[MS\]\s*=\s*(.*)$", line)
        if not match:
            print(f"[ERROR] Invalid Every syntax: {line}")
            return
        ms = parse_token_value(match.group(1))
        handle_every(ms)

    elif IF_OP_RE.match(line):
        if not handle_if(line):
            current_line = skip_if_block(current_line)
            # If we landed on Else/EndIf, skip its line too.
            if current_line < len(program_lines):
                if program_lines[current_line].strip() in ("Else", "EndIf"):
                    # run_program will increment after this returns
                    return

    elif line == "Else":
        # Skip Else block if we reached it (meaning the If was true)
        current_line = skip_to_endif(current_line)
        return

    elif line == "EndIf":
        return

    elif line.startswith("Loop[FOREVER]"):
        handle_loop(current_line)

    elif line.startswith("Goto["):
        label = line.split("Goto[", 1)[1].split("]", 1)[0]
        handle_goto(label)

    elif line.startswith("CallFunction["):
        func = line.split("CallFunction[", 1)[1].split("]", 1)[0]
        handle_call(func)

    elif line.startswith("SetColor["):
        match = re.match(r"SetColor\[(FG|BG)\]\s*=\s*(.*)$", line, re.IGNORECASE)
        if not match:
            print(f"[ERROR] Invalid SetColor syntax: {line}")
            return
        tag = match.group(1).upper()
        value = parse_token_value(match.group(2))
        set_color(tag, value)

    elif line == "ResetColor":
        reset_color()

    elif line.startswith("DrawBox["):
        match = re.match(r"DrawBox\[(\d+)\s*,\s*(\d+)\]\s*=\s*(.*)$", line)
        if not match:
            print(f"[ERROR] Invalid DrawBox syntax: {line}")
            return
        width = match.group(1)
        height = match.group(2)
        ch = parse_token_value(match.group(3))
        draw_box(width, height, ch)

    elif line == "ClearScreen":
        clear_screen()

    elif line == "FillLine":
        try:
            import shutil
            cols = shutil.get_terminal_size((80, 20)).columns
        except Exception:
            cols = 80
        text = " " * max(1, cols)
        print(f"{ansi_prefix()}{text}{ansi_reset()}", end="")
        print("\r", end="")

    elif line.startswith("FillLines[") and line.endswith("]"):
        try:
            raw_count = line.split("FillLines[", 1)[1][:-1]
            count = int(parse_token_value(raw_count))
        except Exception:
            print(f"[ERROR] Invalid FillLines syntax: {line}")
            return
        if count <= 0:
            return
        try:
            import shutil
            cols = shutil.get_terminal_size((80, 20)).columns
        except Exception:
            cols = 80
        text = " " * max(1, cols)
        prefix = ansi_prefix()
        for i in range(count):
            if prefix:
                print(f"{prefix}{text}{ansi_reset()}", end="")
            else:
                print(text, end="")
            if i < count - 1:
                print()
        print("\r", end="")

    elif line.startswith("SetCursor["):
        match = re.match(r"SetCursor\[(.*?)\s*,\s*(.*?)\]\s*$", line)
        if not match:
            print(f"[ERROR] Invalid SetCursor syntax: {line}")
            return
        row = parse_token_value(match.group(1))
        col = parse_token_value(match.group(2))
        try:
            set_cursor(int(row), int(col))
        except ValueError:
            print("[ERROR] SetCursor requires integer row and column")

    elif line.startswith("TickTimer["):
        match = re.match(r"TickTimer\[(.*?)\]\s*$", line)
        if not match:
            print(f"[ERROR] Invalid TickTimer syntax: {line}")
            return
        ms = parse_token_value(match.group(1))
        tick_timer(ms)

    elif line.startswith("Time["):
        match = re.match(r"Time\[(MS|SEC|MIN)\]\s*=\s*(.*)$", line, re.IGNORECASE)
        if not match:
            print(f"[ERROR] Invalid Time syntax: {line}")
            return
        unit = match.group(1).upper()
        value = parse_token_value(match.group(2))
        if unit == "MS":
            tick_timer(value)
        elif unit == "SEC":
            tick_timer_seconds(value)
        elif unit == "MIN":
            tick_timer_minutes(value)

    elif line.startswith("StartFunction[") or line == "EndFunction":
        return  # Already handled in preload

    # Accept new Label[...] syntax; also accept old Label:NAME for compatibility
    elif line.startswith("Label[") and "]" in line:
        return  # Already stored
    elif line.startswith("Label:"):
        # backward compatibility: warn and ignore at runtime
        print("[WARN] Deprecated label syntax 'Label:NAME' used; prefer 'Label[NAME]'")
        return  # Already stored

    else:
        print(f"[ERROR] Unknown command: {line}")

# ------------------------------
# Program Loader (First Pass)
# ------------------------------
def load_program(file_path):
    global program_lines, labels, functions

    with open(file_path, "r") as f:
        raw_lines = f.readlines()

    in_function = False
    current_func = ""

    # We'll build a filtered program_lines that excludes function bodies so they are
    # not executed during the main run. Labels must be indexed against the filtered list.
    program_lines = []
    labels = {}
    functions = {}

    for line in raw_lines:
        stripped = line.strip()
        # Remove inline comments outside quotes first
        stripped = strip_inline_comment(stripped)
        # Skip full-line or empty lines after stripping
        if not stripped or stripped.startswith("//"):
            continue

        if stripped.startswith("StartFunction["):
            in_function = True
            current_func = stripped.split("StartFunction[", 1)[1].split("]", 1)[0]
            functions[current_func] = []
            continue

        if stripped == "EndFunction":
            in_function = False
            current_func = ""
            continue

        if in_function:
            # collect function body lines (already stripped)
            if stripped and not stripped.startswith("//"):
                functions[current_func].append(stripped)
            continue

        # Not inside a function: treat as part of the main program
        # New preferred syntax: Label[NAME]
        if stripped.startswith("Label[") and "]" in stripped:
            label_name = stripped.split("Label[", 1)[1].split("]", 1)[0].strip()
            labels[label_name] = len(program_lines)
        elif stripped.startswith("Label:"):
            # Backwards compatibility: accept old form but warn
            label_name = stripped[6:].strip()
            print(f"[WARN] Deprecated label syntax 'Label:NAME' used for '{label_name}'; prefer 'Label[{label_name}]'")
            labels[label_name] = len(program_lines)

        program_lines.append(stripped)

# ------------------------------
# Runner
# ------------------------------
def run_program():
    global current_line
    current_line = 0
    while current_line < len(program_lines):
        line = program_lines[current_line].strip()
        execute_line(line)
        current_line += 1

# ------------------------------
# Entry Point
# ------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python longc.py <program.long>")
        sys.exit(1)

    load_program(sys.argv[1])
    run_program()
