import re, sys
kind = sys.argv[1]
SGR = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DIM = re.compile(r"\x1b\[2m.*?(?:\x1b\[0m|$)")
plain = lambda line: SGR.sub("", line).strip()
typed = lambda line: plain(DIM.sub("", line))
lines = sys.stdin.read().replace("\r", "").split("\n")
if len(sys.argv) > 2 and sys.argv[2] == "shell-bang":
    # Shell mode replaces the prompt's › with a red !. Match the final background block, never history or a command.
    bg = [i for i, line in enumerate(lines) if "\x1b[48;" in line]
    if kind != "codex" or not bg:
        sys.exit(1)
    end = bg[-1]
    start = end
    while start > 0 and "\x1b[48;" in lines[start - 1]:
        start -= 1
    content = [line for line in lines[start:end + 1] if plain(line)]
    shell = any(re.search(r"\bShell mode\b", plain(line)) for line in lines[end + 1:])
    sys.exit(0 if len(content) == 1 and plain(content[0]) == typed(content[0]) == "!" and shell else 1)
box = []
if kind == "claude":  # the last two rules, the upper one may carry the session name; ❯ elsewhere is history or a picker
    rules = [i for i, l in enumerate(lines) if re.fullmatch(r"─{3,}( .* ─+)?", plain(l)) and len(plain(l)) >= 10]
    if len(rules) >= 2:
        box = lines[rules[-2] + 1:rules[-1]]
    if not box or not SGR.sub("", box[0]).startswith("❯\xa0"):
        sys.exit(1)
    box[0] = typed(box[0])[1:]
elif kind == "codex":  # the last › line unless its mark is dim (an old prompt), then the block of lines with its background
    marks = [i for i, l in enumerate(lines) if SGR.sub("", l).startswith("›")]
    if not marks or not typed(lines[marks[-1]]).startswith("›"):
        sys.exit(1)
    i = marks[-1] + 1
    box = [typed(lines[marks[-1]])[1:]]
    while i < len(lines) and "\x1b[48;" in lines[i]:
        box.append(lines[i])
        i += 1
else:
    sys.exit(1)
sys.exit(1 if any(plain(line) for line in box) else 0)
