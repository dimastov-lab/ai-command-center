# Homebrew cask for the unsigned-first distribution channel (#197).
#
# Lives here as the source of truth; publish by copying into the tap repo
# (dimastov-lab/homebrew-tap, path Casks/ai-command-center.rb) after each
# desktop-v* release and replacing `version`/`sha256` with the released values
# from SHA256SUMS.txt. Users install with:
#
#   brew tap dimastov-lab/tap
#   brew install --cask --no-quarantine ai-command-center
#
# The app is not signed/notarized yet, so --no-quarantine (or right-click →
# Open on first launch) is required. See docs/desktop/INSTALL_UNSIGNED.md.
cask "ai-command-center" do
  version "0.0.0" # replace with desktop-vX.Y.Z tag version on publish
  sha256 "REPLACE_WITH_SHA256_FROM_RELEASE"

  url "https://github.com/dimastov-lab/ai-command-center/releases/download/desktop-v#{version}/AI-Command-Center-macos-arm64.zip"
  name "AI Command Center"
  desc "AI Command Center desktop shell (unsigned build)"
  homepage "https://github.com/dimastov-lab/ai-command-center"

  depends_on arch: :arm64

  app "AI Command Center.app"

  caveats <<~EOS
    Эта сборка не подписана и не нотаризована Apple.
    Устанавливайте с флагом --no-quarantine:
      brew install --cask --no-quarantine ai-command-center
    Либо при первом запуске: правый клик по приложению → «Открыть».
  EOS
end
