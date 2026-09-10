#!/bin/bash

# ===========================================
# dotfiles verification script
# Verifies all components are installed correctly
# ===========================================

# brew PATH (Apple Silicon)
[[ -x "/opt/homebrew/bin/brew" ]] && eval "$(/opt/homebrew/bin/brew shellenv)"

DOTFILES="$HOME/.dotfiles"
CONFIG="$HOME/.config"
PREFS="$HOME/Library/Preferences"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local condition="$2"

    if eval "$condition"; then
        echo -e "  ${GREEN}✅${NC} $name"
        ((PASS++))
    else
        echo -e "  ${RED}❌${NC} $name"
        ((FAIL++))
    fi
}

check_warn() {
    local name="$1"
    local condition="$2"
    local hint="$3"

    if eval "$condition"; then
        echo -e "  ${GREEN}✅${NC} $name"
        ((PASS++))
    else
        echo -e "  ${YELLOW}⚠️${NC}  $name"
        [[ -n "$hint" ]] && echo -e "      ${YELLOW}→ $hint${NC}"
        ((WARN++))
    fi
}

# Helper: check symlink points to correct path
check_symlink() {
    local name="$1"
    local link="$2"
    local expected="$3"

    if [[ -L "$link" ]]; then
        local actual
        actual=$(readlink "$link")
        if [[ "$actual" == "$expected" ]]; then
            echo -e "  ${GREEN}✅${NC} $name"
            ((PASS++))
        else
            echo -e "  ${RED}❌${NC} $name (wrong path: $actual)"
            ((FAIL++))
        fi
    else
        echo -e "  ${RED}❌${NC} $name (not a symlink)"
        ((FAIL++))
    fi
}

echo ""
echo "================================"
echo "  dotfiles verification"
echo "================================"
echo ""

# ===========================================
# 1. Homebrew
# ===========================================
echo "📦 Homebrew"
check "brew installed" "command -v brew &>/dev/null"
check "Brewfile exists" "[[ -f '$DOTFILES/Brewfile' ]]"

# ===========================================
# 2. Shell
# ===========================================
echo ""
echo "🐚 Shell"
check "zsh installed" "command -v zsh &>/dev/null"
check "Oh-My-Zsh installed" "[[ -d '$HOME/.oh-my-zsh' ]]"
check "Powerlevel10k installed" "[[ -d '$HOME/.oh-my-zsh/custom/themes/powerlevel10k' ]]"
check_symlink ".zshrc symlink" "$HOME/.zshrc" "$DOTFILES/shell/zsh/zshrc"
check_symlink ".p10k.zsh symlink" "$HOME/.p10k.zsh" "$DOTFILES/shell/zsh/p10k.zsh"

# ===========================================
# 3. Tmux
# ===========================================
echo ""
echo "🖥️  Tmux"
check "tmux installed" "command -v tmux &>/dev/null"
check "gpakosz/.tmux installed" "[[ -d '$HOME/.tmux' ]]"
check "TPM installed" "[[ -d '$HOME/.tmux/plugins/tpm' ]]"
check_symlink ".tmux.conf.local symlink" "$HOME/.tmux.conf.local" "$DOTFILES/terminal/tmux/tmux.conf.local"
check_warn "Tmux plugins installed" "[[ -d '$HOME/.tmux/plugins/tmux-resurrect' ]]" "Run: ~/.tmux/plugins/tpm/bin/install_plugins"

# ===========================================
# 4. Neovim
# ===========================================
echo ""
echo "📝 Neovim"
check "nvim installed" "command -v nvim &>/dev/null"
check_symlink "nvim config symlink" "$CONFIG/nvim" "$DOTFILES/editors/nvim"
check_warn "LazyVim plugins" "[[ -d '$HOME/.local/share/nvim/lazy' ]]" "Run: nvim --headless '+Lazy! sync' +qa"

# ===========================================
# 5. Git Tools
# ===========================================
echo ""
echo "🔧 Git Tools"
check "git installed" "command -v git &>/dev/null"
check "lazygit installed" "command -v lazygit &>/dev/null"
check "lazygit config symlink" "[[ -L '$HOME/Library/Application Support/lazygit/config.yml' ]]"
check "gh installed" "command -v gh &>/dev/null"
check "gh authenticated" "gh auth status &>/dev/null"
check "delta installed" "command -v delta &>/dev/null"
check "delta git config" "[[ \$(git config --global core.pager) == 'delta' ]]"

# ===========================================
# 6. Development Tools
# ===========================================
echo ""
echo "⚙️  Development Tools"
check "mise installed" "command -v mise &>/dev/null"
check_symlink "mise config symlink" "$CONFIG/mise/config.toml" "$DOTFILES/tools/mise/config.toml"
check_warn "node installed (mise)" "[[ -x ~/.local/share/mise/shims/node ]]" "Run: mise install"
check_warn "python installed (mise)" "[[ -x ~/.local/share/mise/shims/python ]]" "Run: mise install"
check "fzf installed" "command -v fzf &>/dev/null"
check_warn "fzf key-bindings" "[[ -f '$HOME/.fzf.zsh' ]]" "Run: /opt/homebrew/opt/fzf/install"
check "ripgrep installed" "command -v rg &>/dev/null"
check "fd installed" "command -v fd &>/dev/null"
check "bat installed" "command -v bat &>/dev/null"
check "uv installed" "command -v uv &>/dev/null"
check "tailscale installed" "command -v tailscale &>/dev/null"
check_warn "tailscale connected" "tailscale status &>/dev/null" "Run: sudo brew services start tailscale && sudo tailscale up --operator=\$USER"

# ===========================================
# 7. Karabiner
# ===========================================
echo ""
echo "⌨️  Karabiner"
check "karabiner-elements installed" "[[ -d '/Applications/Karabiner-Elements.app' ]]"
check_symlink "karabiner config symlink" "$CONFIG/karabiner" "$DOTFILES/macos/karabiner"
check "karabiner.json exists" "[[ -f '$CONFIG/karabiner/karabiner.json' ]]"

# ===========================================
# 8. Hammerspoon
# ===========================================
echo ""
echo "🔨 Hammerspoon"
check "hammerspoon installed" "[[ -d '/Applications/Hammerspoon.app' ]]"
check_symlink "init.lua symlink" "$HOME/.hammerspoon/init.lua" "$DOTFILES/macos/hammerspoon/init.lua"
check_symlink "Spoons symlink" "$HOME/.hammerspoon/Spoons" "$DOTFILES/macos/hammerspoon/Spoons"

# ===========================================
# 9. Rectangle
# ===========================================
echo ""
echo "🪟 Rectangle"
check "rectangle installed" "[[ -d '/Applications/Rectangle.app' ]]"

# ===========================================
# 10. iTerm2
# ===========================================
echo ""
echo "💻 iTerm2"
check "iterm2 installed" "[[ -d '/Applications/iTerm.app' ]]"
check "iterm2 plist" "[[ -f '$PREFS/com.googlecode.iterm2.plist' ]]"

# ===========================================
# 11. Claude Code
# ===========================================
echo ""
echo "🤖 Claude Code"
# 네이티브 설치본이 ~/.local/bin/claude 다. mise shim 은 옛 npm 설치의 잔재라
# 있는 기기도 없는 기기도 있어서, 둘 중 하나만 있으면 통과로 본다.
check "claude installed" "[[ -x ~/.local/bin/claude ]] || [[ -x ~/.local/share/mise/shims/claude ]]"
check "settings.json exists" "[[ -f '$HOME/.claude/settings.json' ]]"
check "statusline symlink" "[[ -L '$HOME/.claude/statusline-command.sh' ]]"
check "stop-notification.sh symlink" "[[ -L '$HOME/.claude/hooks/stop-notification.sh' ]]"
check "permission-notification.sh symlink" "[[ -L '$HOME/.claude/hooks/permission-notification.sh' ]]"
check "session-env.sh symlink" "[[ -L '$HOME/.claude/hooks/session-env.sh' ]]"

# ===========================================
# 12. macOS App Settings
# ===========================================
echo ""
echo "🍎 macOS App Settings"

declare -a apps=(
    "com.knollsoft.Rectangle.plist:Rectangle"
    "com.iktm.snap.plist:Snap"
    "com.clipy-app.Clipy.plist:Clipy"
    "org.youknowone.Gureum.plist:Gureum"
    "com.apphousekitchen.aldente-pro.plist:AlDente"
    "wang.jianing.app.OpenInTerminal-Lite.plist:OpenInTerminal"
    "com.izual.Easydict.plist:Easydict"
)

for app_info in "${apps[@]}"; do
    IFS=':' read -r plist_name app_name <<< "$app_info"
    check_warn "$app_name plist" "[[ -f '$PREFS/$plist_name' ]]" "Run init.sh to restore"
done

# ===========================================
# 13. System Permissions (informational)
# ===========================================
echo ""
echo "🔐 System Permissions Required"
echo -e "  ${YELLOW}ℹ️${NC}  Grant in System Settings > Privacy & Security > Accessibility:"
echo "      - Karabiner-Elements (Input Monitoring + Accessibility)"
echo "      - Hammerspoon"
echo "      - Rectangle"

# ===========================================
# Summary
# ===========================================
echo ""
echo "================================"
echo "  Summary"
echo "================================"
echo ""
echo -e "  ${GREEN}✅ Passed:${NC}  $PASS"
echo -e "  ${RED}❌ Failed:${NC}  $FAIL"
echo -e "  ${YELLOW}⚠️  Warnings:${NC} $WARN"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}All checks passed!${NC}"
    exit 0
else
    echo -e "${YELLOW}Some checks failed. Review the output above.${NC}"
    exit 1
fi
