tap "adoptopenjdk/openjdk"
tap "dwarvesf/tap"
tap "hashicorp/tap"
# tap "homebrew/cask-fonts" # deprecated - fonts now in homebrew/cask
# tap "homebrew/services" # deprecated
tap "jesseduffield/lazygit"
tap "mongodb/brew"
# tap "ringohub/redis-cli" # broken download
tap "stripe/stripe-cli"
tap "xo/xo"

# ===========================================
# CLI Tools
# ===========================================
brew "readline", link: true
brew "awscli"
brew "bat"
brew "bitwarden-cli"
brew "broot"
brew "coreutils"  # timeout(1), used by llm-wiki-ingest.sh
brew "p11-kit"
brew "unbound"
brew "gnutls"
brew "tree-sitter"
brew "emacs"
brew "cask"
brew "llvm"
brew "direnv"
brew "folly"
brew "fizz"
brew "wangle"
brew "fbthrift"
brew "fb303"
brew "fd"
brew "fzf"
brew "libmpc"
brew "gcc"
brew "gh"
brew "git-delta"
brew "git-lfs"
brew "gitleaks"
brew "glow"
brew "gnupg"
brew "guile"
brew "herdr"
brew "httpie"
brew "jq"
brew "k6"
brew "kubectl"
brew "lazygit"
brew "libyaml"
brew "lsd"
brew "luajit", args: ["HEAD"]
brew "mas"
brew "mise"
brew "mongosh"
brew "neovim"
brew "nmap"
# node는 mise로 관리
brew "openjdk@17"
brew "pass"
brew "pngpaste"
brew "protobuf"
brew "ripgrep"
# herdr --remote 로 서버 맥에 붙기 위한 것. 집·회사·외부 어느 망에서 시작하든
# 같은 MagicDNS 이름으로 닿아서, 공유기 포트포워딩으로 SSH를 인터넷에 열지
# 않아도 된다. 데몬은 `sudo brew services start tailscale`, 로그인은
# `tailscale up` — 둘 다 기기별 1회라 init.sh 가 대신 해주지 못한다.
brew "tailscale"
brew "terminal-notifier"
brew "tmux"
brew "watch"
brew "yarn", link: false
brew "zoxide"
brew "zsh"
brew "zsh-completions"
brew "xo/xo/usql"
brew "uv"
brew "hashicorp/tap/terraform-ls"
brew "mongodb/brew/mongodb-community"
brew "stripe/stripe-cli/stripe"

# ===========================================
# Casks (GUI Apps) - no sudo required
# ===========================================
cask "iterm2"
cask "rectangle"
cask "appcleaner"
cask "google-chrome"
cask "postman"
cask "dbeaver-community"
cask "discord"
cask "easydict"
cask "firefox"
cask "font-hack-nerd-font"
cask "gcloud-cli"
cask "hammerspoon"
cask "mongodb-compass"
cask "mysqlworkbench"
cask "ngrok"
cask "notion"
cask "obsidian"
cask "todoist-app"
cask "karabiner-elements"
cask "bitwarden"
cask "clipy"
cask "gureumkim"

# ===========================================
# Casks - sudo required (post-install에서 수동 설치)
# ===========================================
# cask "authy" # deprecated - 서비스 종료
# cask "docker-desktop"  # sudo 필요 - brew install --cask docker-desktop
# cask "session-manager-plugin" # deprecated 2026-09

# ===========================================
# Mac App Store (requires Apple ID login)
# ===========================================
mas "DevCleaner", id: 1388020431
mas "Hidden Bar", id: 1452453066
mas "Polaris Office", id: 1098211970
mas "QuickShade", id: 931571202
mas "ScreenBrush", id: 1233965871
mas "Shazam", id: 897118787
mas "Slack", id: 803453959
mas "Snap", id: 418073146
mas "TestFlight", id: 899247664
mas "카카오톡", id: 869223134
# Removed: EasyRes (688211836), Redacted (984968384) - no longer in App Store
