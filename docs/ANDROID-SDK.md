# Android SDK setup

The Android SDK is required only for the `--avrcp` flag, which builds `src/Y1MediaBridge/` via Gradle. Gradle itself is bootstrapped by the in-tree wrapper (`src/Y1MediaBridge/gradlew`) — no separate Gradle install needed — but the wrapper still needs the SDK to compile against and locate `aapt`/`d8`/etc.

There is **no Linux distribution package for the Android SDK** (Google's licensing prevents redistribution, so it's not in DNF/APT/EPEL/RPMFusion). On macOS Homebrew has a cask; on Windows there's an installer. Everything below ends up at the same end state: an SDK directory containing `cmdline-tools/`, `platform-tools/`, `platforms/android-34/`, and `build-tools/34.0.0/`, with `ANDROID_HOME` pointing at it.

## Components needed for this project

| Component | Why |
|---|---|
| `platforms;android-34` | `compileSdk 34` in `src/Y1MediaBridge/app/build.gradle` |
| `build-tools;34.0.0` | AGP 8.7.3 invokes `aapt2`, `d8`, `zipalign` from this version |
| `platform-tools` | `adb` for device interaction. Optional for *building*, mandatory for the post-flash verification steps. |

Total fresh install (cmdline-tools + the three components above): **~1.5–2 GB** before Gradle pulls its own dependency cache (another ~500 MB on first `./gradlew assembleDebug`).

## Already have it?

Skip ahead if these already work:

```bash
echo "$ANDROID_HOME"                                 # should print a path
ls "$ANDROID_HOME/platforms/android-34"              # should list non-empty
ls "$ANDROID_HOME/build-tools/34.0.0"                # should list non-empty
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --version    # should print a version
```

If `ANDROID_HOME` is unset but Android Studio is installed, the SDK is typically at:

- Linux: `~/Android/Sdk`
- macOS: `~/Library/Android/sdk`
- Windows: `%LOCALAPPDATA%\Android\Sdk`

Set `ANDROID_HOME` to the right path and you're done.

## Linux (Rocky / Alma / RHEL / Fedora / Debian / Ubuntu / Arch)

Same steps regardless of distro — the cmdline-tools are platform-agnostic Java + scripts, distributed only by Google.

```bash
# 1. Download the standalone cmdline-tools (~150 MB).
#    Browse https://developer.android.com/studio#command-tools and grab the
#    "Command line tools only" Linux zip.
mkdir -p ~/Android/Sdk/cmdline-tools
cd ~/Android/Sdk/cmdline-tools
unzip ~/Downloads/commandlinetools-linux-*_latest.zip

# 2. Move into the canonical layout. sdkmanager expects to live at
#    cmdline-tools/latest/bin/sdkmanager, NOT cmdline-tools/cmdline-tools/...
mv cmdline-tools latest

# 3. Install the components. Accept licenses when prompted (or run
#    `yes | ~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager --licenses`).
~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager \
    --install "platforms;android-34" "build-tools;34.0.0" "platform-tools"

# 4. Persist ANDROID_HOME (bash; for zsh use ~/.zshrc, fish use ~/.config/fish/config.fish).
cat >> ~/.bashrc <<'EOF'
export ANDROID_HOME=$HOME/Android/Sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools
EOF
source ~/.bashrc
```

**JDK requirement:** `sdkmanager` and AGP 8.7.3 need JDK 17+. Install via your distro:

- Rocky / Alma / RHEL / Fedora: `sudo dnf install -y java-17-openjdk-devel`
- Debian / Ubuntu: `sudo apt install -y openjdk-17-jdk`
- Arch: `sudo pacman -S jdk17-openjdk`

If `java -version` reports < 17, set `JAVA_HOME` to the JDK 17 install location.

## macOS

### Homebrew (recommended)

```bash
brew install --cask android-commandlinetools

# Homebrew puts the SDK at /opt/homebrew/share/android-commandlinetools
# (Apple Silicon) or /usr/local/share/android-commandlinetools (Intel).
# Set ANDROID_HOME and install components:
export ANDROID_HOME=$(brew --prefix)/share/android-commandlinetools
sdkmanager --install "platforms;android-34" "build-tools;34.0.0" "platform-tools"

# Persist (zsh is default on modern macOS):
cat >> ~/.zshrc <<EOF
export ANDROID_HOME=$(brew --prefix)/share/android-commandlinetools
export PATH=\$PATH:\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools
EOF
source ~/.zshrc
```

JDK: `brew install --cask temurin` (Eclipse Temurin 21) or `brew install openjdk@17`.

### Manual download

Same shape as Linux — download `commandlinetools-mac-*_latest.zip` from `https://developer.android.com/studio#command-tools`, unzip into `~/Library/Android/sdk/cmdline-tools/latest/`, run `sdkmanager`, set `ANDROID_HOME=$HOME/Library/Android/sdk`.

## Windows

The full toolkit (bash, sudo, mtkclient, mount, simg2img) doesn't work natively on Windows — only the `src/Y1MediaBridge/` build does. If you're cross-developing the Android app from Windows, install via:

- **Android Studio** (heaviest, includes the SDK): https://developer.android.com/studio
- **Standalone cmdline-tools**: download `commandlinetools-win-*_latest.zip`, unzip into `%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\`, run `sdkmanager.bat`, set `ANDROID_HOME` via Control Panel → System → Environment Variables.

For everything else — flashing, patching — use WSL2 with one of the Linux setups above.

## Verify the install

After the steps above, in a fresh shell:

```bash
echo $ANDROID_HOME                                       # → your SDK path
sdkmanager --list_installed                              # → lists platforms;android-34, build-tools;34.0.0, platform-tools
java -version                                            # → 17 or newer
( cd src/Y1MediaBridge && ./gradlew --version )          # → Gradle 9.5.0, JVM 17+
```

If those four pass, `./innioasis-y1-fixes.bash --artifacts-dir <dir> --avrcp` will resolve the SDK and Gradle correctly.

## License acceptance

`sdkmanager --install …` will prompt to accept Google's licenses on first run. To accept up-front (e.g. in a script), use:

```bash
yes | sdkmanager --licenses
```

The licenses live at `$ANDROID_HOME/licenses/` after acceptance — back them up if you want to short-circuit license re-acceptance on a fresh machine.

## Bumping the SDK pins

If the project bumps `compileSdk` or AGP:

1. Update the corresponding pin in `src/Y1MediaBridge/app/build.gradle` (`compileSdkVersion`).
2. Re-run `sdkmanager --install "platforms;android-XX" "build-tools;XX.Y.Z"`.
3. Update the **Components needed** table at the top of this file.
